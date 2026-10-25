"""Toyota demo execution engine: CODE / TOOL / LLM with dependencies."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from app.agent_assignment import AgentAssignmentError, assign_toyota_roles
from app.context_sources import (
    nessie_enabled,
    resolve_enabled_ids,
    toyota_enabled,
)
from app.cost_optimizer import RunCostTracker
from app.evaluators import recommendation_contains_raw_nessie, run_evaluators
from app.model_router import ModelRoutingError, choose_model, normalize_work_style
from app.models import (
    AgentDefinition,
    AuditEvent,
    CreateRunRequest,
    DataSource,
    ExecutorType,
    ExecutionPlan,
    FinancialContext,
    OutputItem,
    PermissionDecision,
    QualityMode,
    RiskLevel,
    RunStatus,
    RunTaskState,
    TaskDefinition,
    TaskStatus,
    TeamDefinition,
    TeamPolicy,
    ToyotaRecommendation,
)
from app.team_normalize import ensure_team_runtime_config
from app.permissions import (
    PermissionDenied,
    assert_integration_allowed,
    assert_tool_allowed,
    check_tool_allowed,
    decision_agent_blocked_from_raw_nessie,
    validate_plan_permissions,
    validate_task_permissions,
)
from app.prompts import build_agent_system_prompt
from app.routing import RouteDecision, RoutingError, route
from app.run_store import append_audit, append_output, get_run, record_task_event, update_run
from app.services.llm_service import LLMUnavailableError, get_llm_service
from app.tools.deterministic import (
    build_fallback_recommendation,
    evaluate_mobility_relevance,
    score_mobility_options,
)
from app.tools.financial import build_financial_context
from app.tools.nessie import get_nessie_client
from app.tools.toyota import load_toyota_customer

logger = logging.getLogger(__name__)


class BudgetLimitError(RuntimeError):
    """A configured server, team or agent ceiling prevented model work."""



def _task(
    *,
    task_id: str,
    name: str,
    agent_id: str,
    action: str,
    agents: dict[str, AgentDefinition],
    depends_on: list[str],
    risk_level: RiskLevel,
) -> TaskDefinition:
    agent = agents.get(agent_id)
    decision = route(action, agent)
    return TaskDefinition(
        id=task_id,
        name=name,
        assigned_agent_id=agent_id,
        executor=decision.executor,
        action=action,
        depends_on=depends_on,
        risk_level=risk_level,
    )


def build_toyota_execution_plan(
    team: TeamDefinition,
    context_source_ids: Optional[list[str]] = None,
    *,
    include_downstream: Optional[bool] = None,
) -> ExecutionPlan:
    """Build the Toyota plan; executors come only from ExecutionRouter.route().

    include_downstream=False keeps only load + relevance (no-action customers).
    """
    enabled = resolve_enabled_ids(context_source_ids)
    if not toyota_enabled(enabled):
        raise ValueError(
            "Context source 'toyota_customer' is required for the Mobility Decision Team."
        )

    team = ensure_team_runtime_config(team)
    agents = {a.id: a for a in team.agents}
    try:
        roles = assign_toyota_roles(team.agents)
    except AgentAssignmentError as exc:
        raise ValueError(str(exc)) from exc

    context_id = roles["context"]
    financial_id = roles["financial"]
    vehicle_id = roles["vehicle"]
    decision_id = roles["decision"]
    affordability_id = roles.get("affordability")

    tasks: list[TaskDefinition] = [
        _task(
            task_id="load-toyota-context",
            name="Load Toyota customer context",
            agent_id=context_id,
            action="load_toyota_customer",
            agents=agents,
            depends_on=[],
            risk_level=RiskLevel.low,
        ),
        _task(
            task_id="evaluate-mobility-relevance",
            name="Evaluate mobility relevance",
            agent_id=context_id,
            action="evaluate_mobility_relevance",
            agents=agents,
            depends_on=["load-toyota-context"],
            risk_level=RiskLevel.low,
        ),
    ]

    include_full = nessie_enabled(enabled) and include_downstream is not False
    if include_full:
        tasks.extend(
            [
                _task(
                    task_id="load-nessie-financials",
                    name="Load banking data",
                    agent_id=financial_id,
                    action="nessie_load_customer_financials",
                    agents=agents,
                    depends_on=["evaluate-mobility-relevance"],
                    risk_level=RiskLevel.medium,
                ),
                _task(
                    task_id="build-financial-context",
                    name="Build financial context",
                    agent_id=financial_id,
                    action="build_financial_context",
                    agents=agents,
                    depends_on=["load-nessie-financials"],
                    risk_level=RiskLevel.medium,
                ),
            ]
        )
        score_depends = ["build-financial-context", "load-toyota-context"]
        if affordability_id:
            aff = agents[affordability_id]
            # CODE affordability check: needs financial_summary + read/calculate (no tool grant).
            can_check = (
                "financial_summary" in aff.permissions.data_scopes
                and any(a in aff.permissions.actions for a in ("read", "calculate"))
            )
            if can_check:
                tasks.append(
                    _task(
                        task_id="check-affordability-signals",
                        name="Check affordability signals",
                        agent_id=affordability_id,
                        action="check_affordability_signals",
                        agents=agents,
                        depends_on=["build-financial-context"],
                        risk_level=RiskLevel.low,
                    )
                )
                score_depends = [
                    "build-financial-context",
                    "load-toyota-context",
                    "check-affordability-signals",
                ]
        tasks.extend(
            [
                _task(
                    task_id="score-mobility-options",
                    name="Compare ownership options",
                    agent_id=vehicle_id,
                    action="score_mobility_options",
                    agents=agents,
                    depends_on=score_depends,
                    risk_level=RiskLevel.medium,
                ),
                _task(
                    task_id="synthesize-recommendation",
                    name="Synthesize mobility recommendation",
                    agent_id=decision_id,
                    action="synthesize_recommendation",
                    agents=agents,
                    depends_on=["score-mobility-options", "build-financial-context"],
                    risk_level=RiskLevel.medium,
                ),
                _task(
                    task_id="run-evaluators",
                    name="Run deterministic evaluators",
                    agent_id=decision_id,
                    action="run_evaluators",
                    agents=agents,
                    depends_on=["synthesize-recommendation"],
                    risk_level=RiskLevel.low,
                ),
            ]
        )

    return ExecutionPlan(tasks=tasks)


def _team_policy(team: TeamDefinition) -> TeamPolicy:
    return team.policy or TeamPolicy()


def _agents_by_id(team: TeamDefinition) -> dict[str, AgentDefinition]:
    """Shallow index — never mutate permissions to make a task succeed."""
    return {a.id: a for a in team.agents}


def _audit(
    run_id: str,
    *,
    agent_id: Optional[str],
    event_type: str,
    message: str,
    executor_type: Optional[ExecutorType] = None,
    resource: Optional[str] = None,
    result: Optional[str] = None,
    cost_usd: float = 0.0,
) -> None:
    append_audit(
        run_id,
        AuditEvent(
            id=f"evt-{uuid4().hex[:8]}",
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            run_id=run_id,
            agent_id=agent_id,
            event_type=event_type,
            executor_type=executor_type,
            resource=resource,
            result=result,
            message=message,
            cost_usd=cost_usd,
        ),
    )


def _merged_context_source_ids(request: CreateRunRequest) -> list[str]:
    return list(
        dict.fromkeys(
            [
                *(request.context_source_ids or []),
                *(request.context.context_source_ids or []),
            ]
        )
    )


def _emit_route_decisions(
    run_id: str,
    plan: ExecutionPlan,
    agents: dict[str, AgentDefinition],
    costs: RunCostTracker,
) -> None:
    for task in plan.tasks:
        agent = agents.get(task.assigned_agent_id)
        try:
            decision: RouteDecision = route(task.action, agent)
        except RoutingError as exc:
            _audit(
                run_id,
                agent_id=task.assigned_agent_id,
                event_type="ROUTE_DECISION",
                resource=task.action,
                result="FAILED",
                message=str(exc),
            )
            raise
        costs.record_route()
        _audit(
            run_id,
            agent_id=task.assigned_agent_id,
            event_type="ROUTE_DECISION",
            executor_type=decision.executor,
            resource=task.action,
            result=decision.executor.value,
            message=f"{task.action} → {decision.executor.value}. Reason: {decision.reason}",
        )


def _skip_remaining_tasks(run_id: str, pending: dict[str, RunTaskState], reason: str) -> None:
    skipped_ids = list(pending.keys())

    def _mut(r) -> None:
        for t in r.tasks:
            if t.id in skipped_ids:
                t.status = TaskStatus.SKIPPED
                t.activity_summary = reason

    update_run(run_id, _mut)
    for tid in skipped_ids:
        task = pending[tid]
        _audit(
            run_id,
            agent_id=task.assigned_agent_id,
            event_type="TASK_COMPLETED",
            executor_type=task.executor,
            resource=task.action,
            result="SKIPPED",
            message=reason,
        )
    pending.clear()


def mobility_relevant(customer_id: str) -> bool:
    """Cheap CODE preflight — same gate the executor uses after load."""
    from app.tools.deterministic import evaluate_mobility_relevance
    from app.tools.toyota import load_toyota_customer

    toyota = load_toyota_customer(customer_id)
    return bool(evaluate_mobility_relevance(toyota).get("relevant"))


async def execute_run(run_id: str, request: CreateRunRequest) -> None:
    """Background execution for one run."""
    request.team = ensure_team_runtime_config(request.team)
    source_ids = _merged_context_source_ids(request)
    from app.pack_workflows import build_pack_execution_plan
    from app.simple_play import align_simple_play_team, build_simple_play_plan

    policy_preview = _team_policy(request.team)
    if (policy_preview.workflow_kind or "") == "simple_play":
        request.team = align_simple_play_team(request.team)
        plan = build_simple_play_plan(request.team)
    elif request.team.policy and request.team.policy.scope == "portfolio":
        plan = build_pack_execution_plan(request.team, context_source_ids=source_ids or None)
    else:
        plan = build_toyota_execution_plan(request.team, context_source_ids=source_ids or None, include_downstream=mobility_relevant(request.context.customer_id))
    agents = _agents_by_id(request.team)
    policy = _team_policy(request.team)
    work_style = normalize_work_style(policy.work_style)

    perm_err = validate_plan_permissions(agents, plan.tasks)
    if perm_err is not None:
        update_run(
            run_id,
            lambda r: setattr(r, "status", RunStatus.FAILED),
        )
        _audit(
            run_id,
            agent_id=perm_err.agent_id,
            event_type="PERMISSION_CHECK",
            resource=perm_err.tool_name,
            result="DENIED",
            message=perm_err.message,
        )
        _audit(
            run_id,
            agent_id=None,
            event_type="RUN_COMPLETED",
            result="FAILED",
            message=f"Plan permission validation failed: {perm_err.message}",
        )
        return

    costs = RunCostTracker.from_team_policy(policy)
    ctx: dict[str, Any] = {
        "toyota": None,
        "nessie_raw": None,  # stays inside financial boundary
        "nessie_source": None,
        "financial": None,
        "scored": None,
        "recommendation": None,
        "relevance": None,
        "no_action_needed": False,
        "permission_allows": 0,
        "permission_denials": 0,
        "tool_failures": 0,
        "budget_ok": True,
        "review_status": None,
        "team_policy": policy,
        "work_style": work_style,
    }
    nessie = get_nessie_client()
    nessie.clear_run_cache()

    update_run(run_id, lambda r: setattr(r, "status", RunStatus.RUNNING))
    _audit(
        run_id,
        agent_id=None,
        event_type="RUN_CREATED",
        message=(
            f"Run execution started. work_style={work_style}; "
            f"team_budget calls≤{policy.max_llm_calls} cost≤{policy.max_llm_cost_usd}."
        ),
    )
    _emit_route_decisions(run_id, plan, agents, costs)

    # Security demo: decision agent denied raw Nessie
    decision = next((a for a in request.team.agents if "decision" in a.id), None)
    if decision:
        denied = check_tool_allowed(decision, "nessie_purchases")
        if denied == PermissionDecision.DENIED or decision_agent_blocked_from_raw_nessie(
            decision, "nessie_purchases"
        ):
            ctx["permission_denials"] += 1
            _audit(
                run_id,
                agent_id=decision.id,
                event_type="PERMISSION_CHECK",
                executor_type=ExecutorType.TOOL,
                resource="nessie_raw_purchases",
                result="DENIED",
                message="Decision Agent may receive financial_summary but not raw purchases.",
            )

    run = get_run(run_id)
    if run is None:
        return

    pending = {t.id: t for t in run.tasks}
    completed: set[str] = set()

    try:
        while pending:
            progress = False
            for task_id, task in list(pending.items()):
                if not all(dep in completed for dep in task.depends_on):
                    continue
                progress = True
                await _execute_task(
                    run_id=run_id,
                    task=task,
                    agents=agents,
                    request=request,
                    costs=costs,
                    ctx=ctx,
                )
                completed.add(task_id)
                del pending[task_id]

                if task.action in {"evaluate_mobility_relevance", "filter_mobility_portfolio"} and ctx.get("no_action_needed"):
                    skip_reason = (
                        "No mobility decision needed — skipped expensive Nessie/LLM work."
                    )
                    # Count remaining TOOL/LLM tasks as avoided LLM baseline work.
                    for skipped in list(pending.values()):
                        if skipped.executor in (ExecutorType.TOOL, ExecutorType.LLM):
                            costs.record_avoided_optional_llm()
                        elif skipped.executor == ExecutorType.CODE:
                            costs.record_avoided_optional_llm()
                    _skip_remaining_tasks(run_id, pending, skip_reason)
                    break
            if not progress and pending:
                raise RuntimeError("Task dependency deadlock")

        if ctx.get("no_action_needed"):
            def _finalize_noop(r) -> None:
                r.status = RunStatus.NO_ACTION_NEEDED
                r.financial_context = ctx["financial"]
                r.recommendation = None
                if request.team.policy.scope == "portfolio":
                    from app.models import PortfolioResult
                    r.portfolio_result = PortfolioResult(purpose="mobility", skipped_customers=ctx.get("skipped_customers", []))
                r.metrics = type(r.metrics).model_validate(costs.metrics_dict())
                r.evaluators = []

            update_run(run_id, _finalize_noop)
            reasons = (ctx.get("relevance") or {}).get("reasons") or []
            _audit(
                run_id,
                agent_id=None,
                event_type="RUN_COMPLETED",
                result="NO_ACTION_NEEDED",
                message=(
                    "No relevant mobility decision — run stopped before Nessie/LLM. "
                    + "; ".join(reasons)
                ),
            )
            return

        if ctx.get("portfolio_result") is not None:
            from app.portfolio import validate_portfolio_result
            from app.models import EvaluatorStatus
            result = ctx["portfolio_result"]
            evaluators = validate_portfolio_result(result, [row["customer_id"] for row in ctx["portfolio_summaries"]])
            def finalize_portfolio(r):
                r.status = RunStatus.COMPLETED if all(e.status == EvaluatorStatus.PASS for e in evaluators) else RunStatus.FAILED
                r.portfolio_result = result
                r.metrics = type(r.metrics).model_validate(costs.metrics_dict())
                r.evaluators = evaluators
            update_run(run_id,finalize_portfolio)
            for evaluator in get_run(run_id).evaluators:
                _audit(run_id,agent_id=None,event_type="EVALUATOR_RESULT",executor_type=ExecutorType.CODE,result=evaluator.status.value,message=evaluator.details)
            _audit(run_id,agent_id=None,event_type="RUN_COMPLETED",result=get_run(run_id).status.value,message="Portfolio workflow evaluated; no offers or financial transactions submitted.")
            return

        if (policy.workflow_kind or "") == "simple_play":
            def _finalize_simple(r) -> None:
                r.status = RunStatus.COMPLETED if ctx["budget_ok"] else RunStatus.BUDGET_LIMIT
                r.metrics = type(r.metrics).model_validate(costs.metrics_dict())
                r.evaluators = []

            update_run(run_id, _finalize_simple)
            _audit(
                run_id,
                agent_id=None,
                event_type="RUN_COMPLETED",
                result=get_run(run_id).status.value,
                message="Simple play completed (Meta LLM only; no external connectors).",
            )
            return

        update_run(
            run_id,
            lambda r: (
                setattr(r, "status", RunStatus.REVIEWING),
                setattr(r, "metrics", type(r.metrics).model_validate(costs.metrics_dict())),
            ),
        )

        reviewer = next(
            (a for a in request.team.agents if "reviewer" in a.id or "review" in a.role.lower()),
            None,
        )
        high_risk = any(t.risk_level == RiskLevel.high for t in plan.tasks)
        run_review = costs.should_run_reviewer(
            work_style=work_style,
            quality_mode=request.quality_mode.value,
            high_risk=high_risk,
            has_reviewer=reviewer is not None,
        )
        if run_review and reviewer is not None:
            await _run_reviewer(
                run_id=run_id,
                agent=reviewer,
                ctx=ctx,
                costs=costs,
                request=request,
                policy=policy,
            )
            if ctx.get("review_status") == "FAIL":
                def _finalize_fail(r) -> None:
                    r.status = RunStatus.FAILED if ctx["budget_ok"] else RunStatus.BUDGET_LIMIT
                    r.financial_context = ctx["financial"]
                    r.recommendation = ctx["recommendation"]
                    r.metrics = type(r.metrics).model_validate(costs.metrics_dict())
                    r.evaluators = run_evaluators(
                        financial=ctx["financial"],
                        recommendation=ctx["recommendation"],
                        permission_denials=ctx["permission_denials"],
                        permission_allows=ctx["permission_allows"],
                        budget_ok=ctx["budget_ok"],
                        tool_failures=ctx["tool_failures"],
                        raw_nessie_in_recommendation=recommendation_contains_raw_nessie(
                            ctx["recommendation"].model_dump() if ctx["recommendation"] else {}
                        ),
                    )

                update_run(run_id, _finalize_fail)
                _audit(
                    run_id,
                    agent_id=reviewer.id,
                    event_type="RUN_COMPLETED",
                    result="FAILED",
                    message="Reviewer returned FAIL — run not marked successful.",
                )
                return
        else:
            _audit(
                run_id,
                agent_id=(reviewer.id if reviewer else "reviewer-agent"),
                event_type="TASK_COMPLETED",
                message=(
                    "Reviewer skipped — Reason: normal-risk run; "
                    "additional LLM review not required"
                    if work_style != "thorough"
                    else "Reviewer skipped — Reason: no Reviewer agent on team"
                ),
                result="SKIPPED",
            )
            costs.record_avoided_optional_llm()

        def _finalize(r) -> None:
            r.status = RunStatus.COMPLETED if ctx["budget_ok"] else RunStatus.BUDGET_LIMIT
            r.financial_context = ctx["financial"]
            r.recommendation = ctx["recommendation"]
            r.metrics = type(r.metrics).model_validate(costs.metrics_dict())
            r.evaluators = run_evaluators(
                financial=ctx["financial"],
                recommendation=ctx["recommendation"],
                permission_denials=ctx["permission_denials"],
                permission_allows=ctx["permission_allows"],
                budget_ok=ctx["budget_ok"],
                tool_failures=ctx["tool_failures"],
                raw_nessie_in_recommendation=recommendation_contains_raw_nessie(
                    ctx["recommendation"].model_dump() if ctx["recommendation"] else {}
                ),
            )

        update_run(run_id, _finalize)
        _audit(run_id, agent_id=None, event_type="RUN_COMPLETED", message="Run completed.")
        for ev in get_run(run_id).evaluators:  # type: ignore
            _audit(
                run_id,
                agent_id=None,
                event_type="EVALUATOR_RESULT",
                message=f"{ev.label}: {ev.status.value} — {ev.details}",
                result=ev.status.value,
            )
    except Exception as exc:
        final_status = RunStatus.BUDGET_LIMIT if isinstance(exc, BudgetLimitError) else RunStatus.FAILED
        logger.exception("Run %s failed: %s", run_id, exc)
        update_run(
            run_id,
            lambda r: (
                setattr(r, "status", final_status),
                setattr(r, "metrics", type(r.metrics).model_validate(costs.metrics_dict())),
            ),
        )
        _audit(
            run_id,
            agent_id=None,
            event_type="RUN_COMPLETED",
            message=f"Run failed: {exc}",
            result=final_status.value,
        )


async def _execute_task(
    *,
    run_id: str,
    task: RunTaskState,
    agents: dict[str, AgentDefinition],
    request: CreateRunRequest,
    costs: RunCostTracker,
    ctx: dict[str, Any],
) -> None:
    agent = agents.get(task.assigned_agent_id)
    if agent is None:
        raise PermissionDenied(
            task.assigned_agent_id,
            task.action,
            f"Assigned agent '{task.assigned_agent_id}' not found on team.",
        )

    def _start(r) -> None:
        for t in r.tasks:
            if t.id == task.id:
                t.status = TaskStatus.RUNNING
                t.activity_summary = f"Starting {task.name}."

    update_run(run_id, _start)
    _audit(
        run_id,
        agent_id=agent.id,
        event_type="TASK_STARTED",
        executor_type=task.executor,
        resource=task.action,
        message=f"Started task {task.name}.",
    )

    started = time.perf_counter()
    success = True
    tool_name: Optional[str] = None
    data_source: Optional[str] = None
    cost = 0.0
    activity = ""

    try:
        # Enforce configured permissions — never broaden them at runtime.
        require_integ = task.executor in (ExecutorType.TOOL, ExecutorType.LLM) and task.action in {
            "nessie_load_customer_financials",
            "synthesize_recommendation",
            "review_recommendation",
            "build_portfolio_financials",
            "explain_shortlist",
        }
        validate_task_permissions(
            agent,
            task,
            require_integrations=require_integ,
        )
        ctx["permission_allows"] += 1
        _audit(
            run_id,
            agent_id=agent.id,
            event_type="PERMISSION_CHECK",
            executor_type=task.executor,
            resource=task.action,
            result="ALLOWED",
            message=f"{agent.name} is authorized for '{task.action}'.",
        )

        if task.executor == ExecutorType.TOOL:
            tool_name, data_source, activity = await _run_tool(
                task, agent, request, ctx, run_id
            )
            costs.record_tool()
        elif task.executor == ExecutorType.CODE:
            activity = await _run_code(task, agent, ctx, run_id, costs)
            data_source = DataSource.LOCAL_CODE.value
        elif task.executor == ExecutorType.LLM:
            activity, cost, ok = await _run_llm(task, agent, ctx, run_id, costs, request)
            data_source = DataSource.LLM.value
            if not ok:
                ctx["budget_ok"] = False
                raise BudgetLimitError("LLM budget exceeded")
        else:
            raise RuntimeError(f"Unknown executor {task.executor}")
    except PermissionDenied as exc:
        success = False
        ctx["permission_denials"] += 1
        activity = exc.message
        _audit(
            run_id,
            agent_id=agent.id,
            event_type="PERMISSION_CHECK",
            executor_type=task.executor,
            resource=exc.tool_name,
            result="DENIED",
            message=exc.message,
        )
        raise
    except Exception as exc:
        success = False
        if task.executor == ExecutorType.TOOL:
            ctx["tool_failures"] += 1
        activity = f"Failed: {exc}"
        raise
    finally:
        duration_ms = int((time.perf_counter() - started) * 1000)

        def _finish(r) -> None:
            for t in r.tasks:
                if t.id == task.id:
                    t.status = TaskStatus.COMPLETED if success else TaskStatus.FAILED
                    t.activity_summary = activity
                    t.tool = tool_name
                    t.duration_ms = duration_ms
                    t.cost_usd = cost
                    t.data_source = data_source
            r.metrics = type(r.metrics).model_validate(costs.metrics_dict())

        update_run(run_id, _finish)
        record_task_event(
            run_id=run_id,
            agent_id=agent.id,
            task_id=task.id,
            executor_type=task.executor.value,
            event_type="TASK_COMPLETED" if success else "TASK_FAILED",
            latency_ms=duration_ms,
            cost_usd=cost,
            success=success,
        )
        if success:
            _audit(
                run_id,
                agent_id=agent.id,
                event_type="TASK_COMPLETED",
                executor_type=task.executor,
                resource=task.action,
                result="ALLOWED",
                message=activity,
                cost_usd=cost,
            )


async def _run_tool(
    task: RunTaskState,
    agent: AgentDefinition,
    request: CreateRunRequest,
    ctx: dict[str, Any],
    run_id: str,
) -> tuple[str, str, str]:
    tool = task.action
    assert_tool_allowed(agent, tool)

    if tool == "build_portfolio_financials":
        from app.portfolio import build_portfolio_financials
        return build_portfolio_financials(ctx,run_id,agent.id)

    if tool == "load_toyota_customer":
        toyota = load_toyota_customer(request.context.customer_id)
        ctx["toyota"] = toyota
        _audit(
            run_id,
            agent_id=agent.id,
            event_type="TOOL_CALL",
            executor_type=ExecutorType.TOOL,
            resource=tool,
            result="ALLOWED",
            message="Loaded synthetic Toyota customer/vehicle/lease/usage context.",
        )
        return tool, DataSource.TOYOTA_SYNTHETIC.value, "Loaded Toyota customer context."

    if tool == "nessie_load_customer_financials":
        assert_integration_allowed(agent, "nessie")
        nessie_id = (ctx.get("toyota") or {}).get("nessie_customer_id")
        if not nessie_id:
            raise RuntimeError("Toyota customer missing nessie_customer_id")
        try:
            payload, source = get_nessie_client().load_customer_financials(nessie_id)
        except Exception as exc:
            _audit(
                run_id,
                agent_id=agent.id,
                event_type="TOOL_CALL",
                executor_type=ExecutorType.TOOL,
                resource="nessie_accounts",
                result="FAILED",
                message=f"Nessie live load failed: {exc}",
            )
            raise
        ctx["nessie_raw"] = payload
        ctx["nessie_source"] = source
        _audit(
            run_id,
            agent_id=agent.id,
            event_type="TOOL_CALL",
            executor_type=ExecutorType.TOOL,
            resource="nessie_accounts",
            result="ALLOWED",
            message=(
                f"Retrieved financial data ({source.value}) via named integration 'nessie' "
                "(API key stayed server-side)."
            ),
        )
        return "nessie_accounts", source.value, f"Retrieved financial data from {source.value}."

    raise RuntimeError(f"Unsupported tool action: {tool}")


async def _run_code(
    task: RunTaskState,
    agent: AgentDefinition,
    ctx: dict[str, Any],
    run_id: str,
    costs: RunCostTracker,
) -> str:
    costs.record_code()
    if task.action in {"load_toyota_portfolio", "filter_mobility_portfolio", "rank_customers"}:
        from app.portfolio import run_portfolio_code
        return run_portfolio_code(task,ctx,run_id,agent.id)
    if task.action == "evaluate_mobility_relevance":
        toyota = ctx.get("toyota")
        if not toyota:
            raise RuntimeError("Toyota customer missing for relevance check")
        relevance = evaluate_mobility_relevance(toyota)
        ctx["relevance"] = relevance
        ctx["no_action_needed"] = not bool(relevance.get("relevant"))
        _audit(
            run_id,
            agent_id=agent.id,
            event_type="RELEVANCE_CHECK",
            executor_type=ExecutorType.CODE,
            resource="evaluate_mobility_relevance",
            result="RELEVANT" if relevance.get("relevant") else "NOT_RELEVANT",
            message=(
                f"relevant={relevance.get('relevant')}; "
                + "; ".join(relevance.get("reasons") or [])
            ),
        )
        append_output(
            run_id,
            OutputItem(
                id=f"out-{uuid4().hex[:8]}",
                title="Mobility Relevance",
                agent_id=agent.id,
                type="RELEVANCE",
                summary=(
                    "Relevant — continuing full decision"
                    if relevance.get("relevant")
                    else "Not relevant — no action needed"
                ),
                content=json.dumps(relevance),
                timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                cost_usd=0.0,
                source_type="CODE",
            ),
        )
        return (
            "Mobility decision is relevant."
            if relevance.get("relevant")
            else "No mobility decision needed right now."
        )

    if task.action == "build_financial_context":
        raw = ctx.get("nessie_raw")
        if not raw:
            raise RuntimeError("Nessie payload missing for financial context")
        financial = build_financial_context(raw)
        ctx["financial"] = financial
        # Drop raw from being attached to outputs — keep only in ctx for this run
        _audit(
            run_id,
            agent_id=agent.id,
            event_type="DATA_MINIMIZATION",
            executor_type=ExecutorType.CODE,
            resource="financial_summary",
            result="ALLOWED",
            message="Created FinancialContext; raw Nessie rows retained only in financial boundary.",
        )
        append_output(
            run_id,
            OutputItem(
                id=f"out-{uuid4().hex[:8]}",
                title="Financial Context",
                agent_id=agent.id,
                type="FINANCIAL_CONTEXT",
                summary=f"Stress={financial.financial_stress}, budget=${financial.safe_mobility_budget}",
                content=json.dumps(financial.model_dump()),
                timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                cost_usd=0.0,
                source_type="CODE",
            ),
        )
        def _set_fc(r) -> None:
            r.financial_context = financial

        update_run(run_id, _set_fc)
        return "Calculated safe mobility budget with local code."

    if task.action == "score_mobility_options":
        toyota = ctx.get("toyota")
        financial = ctx.get("financial")
        if not toyota or not financial:
            raise RuntimeError("Toyota or FinancialContext missing for scoring")
        scored = score_mobility_options(toyota, financial)
        ctx["scored"] = scored
        append_output(
            run_id,
            OutputItem(
                id=f"out-{uuid4().hex[:8]}",
                title="Mobility Option Scores",
                agent_id=agent.id,
                type="ANALYSIS",
                summary=f"Top option: {scored['recommendation_seed']['recommended_action']}",
                content=json.dumps({"options": scored["options"]}),
                timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                cost_usd=0.0,
                source_type="CODE",
            ),
        )
        return "Compared ownership options with deterministic scoring."

    if task.action == "run_evaluators":
        return "Prepared deterministic evaluator suite."

    if task.action == "check_affordability_signals":
        financial = ctx.get("financial")
        if not financial:
            raise RuntimeError("FinancialContext missing for affordability check")
        stress = financial.financial_stress
        budget = financial.safe_mobility_budget
        runway = financial.emergency_runway_months
        summary = (
            f"Affordability signals: stress={stress}, "
            f"safe_mobility_budget=${budget:.0f}, runway={runway:.1f} months."
        )
        append_output(
            run_id,
            OutputItem(
                id=f"out-{uuid4().hex[:8]}",
                title="Affordability Signals",
                agent_id=agent.id,
                type="ANALYSIS",
                summary=summary,
                content=json.dumps(
                    {
                        "financial_stress": stress,
                        "safe_mobility_budget": budget,
                        "emergency_runway_months": runway,
                    }
                ),
                timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                cost_usd=0.0,
                source_type="CODE",
            ),
        )
        return summary

    raise RuntimeError(f"Unsupported CODE action: {task.action}")


async def _run_llm(
    task: RunTaskState,
    agent: AgentDefinition,
    ctx: dict[str, Any],
    run_id: str,
    costs: RunCostTracker,
    request: CreateRunRequest,
) -> tuple[str, float, bool]:
    if task.action == "explain_shortlist":
        from app.portfolio import explain_portfolio
        return await explain_portfolio(task,agent,ctx,run_id,costs,request)

    if task.action == "simple_play":
        return await _llm_simple_play(
            run_id=run_id,
            task=task,
            agent=agent,
            costs=costs,
            request=request,
            ctx=ctx,
        )

    if task.action != "synthesize_recommendation":
        raise RuntimeError(f"Unsupported LLM action: {task.action}")

    from app.runtime_mode import llm_deterministic_synthesis_allowed

    # Decision agent must only see FinancialContext + scored options — never raw Nessie
    financial: FinancialContext = ctx["financial"]
    scored = ctx["scored"]
    seed = scored["recommendation_seed"]
    raw_blocked = ctx.get("nessie_raw") is not None
    policy: TeamPolicy = ctx.get("team_policy") or TeamPolicy()
    work_style = ctx.get("work_style") or normalize_work_style(policy.work_style)

    _audit(
        run_id,
        agent_id=agent.id,
        event_type="DATA_MINIMIZATION",
        executor_type=ExecutorType.LLM,
        resource="financial_summary",
        result="ALLOWED",
        message="Decision Agent receives FinancialContext only; raw Nessie excluded from prompt.",
    )

    estimated_cost = 0.0045
    if not costs.can_afford_llm(estimated_cost, agent=agent):
        _audit(
            run_id,
            agent_id=agent.id,
            event_type="BUDGET_LIMIT",
            executor_type=ExecutorType.LLM,
            resource="synthesize_recommendation",
            result="DENIED",
            message=(
                "LLM synthesis blocked by server/team/agent budget "
                f"(agent max_llm_calls={agent.budget.max_llm_calls}, "
                f"max_llm_cost_usd={agent.budget.max_llm_cost_usd}; "
                f"team max_llm_calls={policy.max_llm_calls})."
            ),
            cost_usd=0.0,
        )
        raise BudgetLimitError("BUDGET_LIMIT: cannot afford LLM synthesis")

    try:
        model_decision = choose_model(
            task_kind="synthesis",
            work_style=work_style,
            risk=task.risk_level.value if hasattr(task, "risk_level") else "medium",
        )
    except ModelRoutingError as exc:
        _audit(
            run_id,
            agent_id=agent.id,
            event_type="MODEL_DECISION",
            executor_type=ExecutorType.LLM,
            resource="synthesize_recommendation",
            result="FAILED",
            message=str(exc),
        )
        raise RuntimeError(str(exc)) from exc

    _audit(
        run_id,
        agent_id=agent.id,
        event_type="MODEL_DECISION",
        executor_type=ExecutorType.LLM,
        resource="synthesize_recommendation",
        result=model_decision.tier,
        message=(
            f"tier={model_decision.tier} model={model_decision.model}. "
            f"Reason: {model_decision.reason}"
        ),
    )

    llm = get_llm_service()
    allow_deterministic = llm_deterministic_synthesis_allowed()
    recommendation: ToyotaRecommendation
    used_real_llm = False
    cost = 0.0

    if llm.is_configured():
        assert_integration_allowed(agent, "meta")
        try:
            recommendation = await _llm_synthesize(
                agent=agent,
                policy=policy,
                financial=financial,
                seed=seed,
                goal=request.goal,
                model_name=model_decision.model,
            )
            used_real_llm = True
            cost = estimated_cost
            costs.record_llm(
                cost_usd=cost, agent_id=agent.id, model_tier=model_decision.tier
            )
        except (LLMUnavailableError, Exception) as exc:
            _audit(
                run_id,
                agent_id=agent.id,
                event_type="LLM_ERROR",
                executor_type=ExecutorType.LLM,
                resource="synthesize_recommendation",
                result="FAILED",
                message=f"LLM synthesis failed: {exc}",
                cost_usd=0.0,
            )
            if not allow_deterministic:
                raise RuntimeError(f"LLM synthesis failed: {exc}") from exc
            recommendation = build_fallback_recommendation(seed)
            _audit(
                run_id,
                agent_id=agent.id,
                event_type="CODE_FALLBACK",
                executor_type=ExecutorType.CODE,
                resource="synthesize_recommendation",
                result="ALLOWED",
                message="Dev/test mode: deterministic recommendation seed used after LLM failure.",
                cost_usd=0.0,
            )
    else:
        if not allow_deterministic:
            _audit(
                run_id,
                agent_id=agent.id,
                event_type="LLM_ERROR",
                executor_type=ExecutorType.LLM,
                resource="synthesize_recommendation",
                result="FAILED",
                message="No LLM provider configured for synthesis.",
                cost_usd=0.0,
            )
            raise RuntimeError("LLM provider not configured for recommendation synthesis")
        recommendation = build_fallback_recommendation(seed)
        _audit(
            run_id,
            agent_id=agent.id,
            event_type="CODE_FALLBACK",
            executor_type=ExecutorType.CODE,
            resource="synthesize_recommendation",
            result="ALLOWED",
            message="Dev/test mode: deterministic recommendation seed (LLM unconfigured).",
            cost_usd=0.0,
        )

    # Generic affordability safety only — never force an outcome from customer id
    # or a fixed stress→action map. If the chosen ownership option exceeds the
    # safe budget, prefer the deterministic scored seed.
    if recommendation.options:
        action_to_option = {
            "BUY_CURRENT_VEHICLE": "buy-current",
            "UPGRADE_VEHICLE": "upgrade",
            "RETURN_VEHICLE": "return",
            "FLEXIBLE_MOBILITY": "flexible",
        }
        opt_id = action_to_option.get(recommendation.recommended_action)
        chosen = next((o for o in recommendation.options if o.id == opt_id), None)
        if (
            chosen is not None
            and chosen.safe_for_budget is False
            and recommendation.recommended_action
            in {"BUY_CURRENT_VEHICLE", "UPGRADE_VEHICLE"}
        ):
            recommendation = build_fallback_recommendation(seed)
            _audit(
                run_id,
                agent_id=agent.id,
                event_type="SAFETY_CORRECTION",
                executor_type=ExecutorType.CODE,
                resource="recommended_action",
                result="CORRECTED",
                message=(
                    "Deterministic safety validator replaced an over-budget "
                    "ownership recommendation with the scored seed."
                ),
                cost_usd=0.0,
            )

    ctx["recommendation"] = recommendation
    assert not recommendation_contains_raw_nessie(recommendation.model_dump()) or not raw_blocked

    if used_real_llm:
        _audit(
            run_id,
            agent_id=agent.id,
            event_type="LLM_CALL",
            executor_type=ExecutorType.LLM,
            resource="synthesize_recommendation",
            result="ALLOWED",
            message=(
                f"Generated customer-facing mobility recommendation via live LLM "
                f"({model_decision.tier})."
            ),
            cost_usd=cost,
        )
    append_output(
        run_id,
        OutputItem(
            id=f"out-{uuid4().hex[:8]}",
            title="Mobility Recommendation",
            agent_id=agent.id,
            type="RECOMMENDATION",
            summary=recommendation.summary,
            content=recommendation.headline + "\n\n" + "\n".join(recommendation.reasons),
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            cost_usd=cost,
            source_type="LLM" if used_real_llm else "CODE",
        ),
    )

    def _set_rec(r) -> None:
        r.recommendation = recommendation

    update_run(run_id, _set_rec)
    summary = (
        "Generated customer-friendly explanation via LLM."
        if used_real_llm
        else "Generated deterministic recommendation (dev/test path)."
    )
    return summary, cost, True


async def _llm_simple_play(
    *,
    run_id: str,
    task: TaskDefinition,
    agent: AgentDefinition,
    costs: RunCostTracker,
    request: CreateRunRequest,
    ctx: dict[str, Any],
) -> tuple[str, float, bool]:
    """One Meta LLM turn for connector-free goals (games, brainstorm, roleplay)."""
    from app.prompts import build_agent_system_prompt
    from app.runtime_mode import llm_deterministic_synthesis_allowed
    from app.services.llm_service import LLMUnavailableError

    policy: TeamPolicy = ctx.get("team_policy") or TeamPolicy()
    work_style = ctx.get("work_style") or normalize_work_style(policy.work_style)
    estimated_cost = 0.004
    if not costs.can_afford_llm(estimated_cost, agent=agent):
        raise BudgetLimitError("BUDGET_LIMIT: cannot afford simple_play LLM turn")

    try:
        model_decision = choose_model(
            task_kind="synthesis",
            work_style=work_style,
            risk="low",
        )
    except ModelRoutingError as exc:
        raise RuntimeError(str(exc)) from exc

    _audit(
        run_id,
        agent_id=agent.id,
        event_type="MODEL_DECISION",
        executor_type=ExecutorType.LLM,
        resource="simple_play",
        result=model_decision.tier,
        message=f"tier={model_decision.tier} model={model_decision.model}. Reason: {model_decision.reason}",
    )

    llm = get_llm_service()
    text = ""
    used_real = False
    cost = 0.0
    if llm.is_configured():
        assert_integration_allowed(agent, "meta")
        system = build_agent_system_prompt(
            agent,
            policy,
            task_instruction=(
                "Complete the user's goal in one short turn. Stay in character. "
                "No tools, no external APIs, no inventing bank/vehicle data."
            ),
        )
        payload = {
            "model": model_decision.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": request.goal},
            ],
            "temperature": 0.7,
            "max_tokens": 600,
        }
        try:
            raw = await llm._post_chat_completion(
                headers={"Authorization": f"Bearer {llm.meta_api_key}"},
                payload=payload,
            )
            text = (
                ((raw.get("choices") or [{}])[0].get("message") or {}).get("content")
                or ""
            ).strip()
            used_real = True
            cost = estimated_cost
            costs.record_llm(cost_usd=cost, agent_id=agent.id, model_tier=model_decision.tier)
        except Exception as exc:
            if not llm_deterministic_synthesis_allowed():
                raise LLMUnavailableError(str(exc)) from exc
            text = (
                f"[dev fallback] {agent.name} acknowledges the goal "
                f"“{request.goal[:120]}” and plays a fair round."
            )
    else:
        if not llm_deterministic_synthesis_allowed():
            raise LLMUnavailableError("No LLM provider configured")
        text = (
            f"[dev fallback] {agent.name} acknowledges the goal "
            f"“{request.goal[:120]}” and plays a fair round."
        )

    if used_real:
        _audit(
            run_id,
            agent_id=agent.id,
            event_type="LLM_CALL",
            executor_type=ExecutorType.LLM,
            resource="simple_play",
            result="ALLOWED",
            message=f"Simple play turn via live LLM ({model_decision.tier}).",
            cost_usd=cost,
        )
    append_output(
        run_id,
        OutputItem(
            id=f"out-{uuid4().hex[:8]}",
            title="Simple play result",
            agent_id=agent.id,
            type="PLAY",
            summary=(text[:160] + ("…" if len(text) > 160 else "")),
            content=text,
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            cost_usd=cost,
            source_type="LLM" if used_real else "CODE",
        ),
    )
    return "Completed connector-free LLM play turn.", cost, True


async def _llm_synthesize(
    *,
    agent: AgentDefinition,
    policy: TeamPolicy,
    financial: FinancialContext,
    seed: dict[str, Any],
    goal: str,
    model_name: str,
) -> ToyotaRecommendation:
    """Ask LLM to polish the deterministic recommendation — never send raw Nessie."""
    llm = get_llm_service()
    if not llm.is_configured():
        raise LLMUnavailableError("No LLM provider configured")
    system = build_agent_system_prompt(
        agent,
        policy,
        task_instruction=(
            "Synthesize the customer-facing mobility recommendation from the supplied "
            "approved FinancialContext and deterministic seed. Do not invent raw bank "
            "transactions. Keep recommended_action aligned with the seed unless the "
            "financial context clearly requires a different safe mobility choice."
        ),
        response_format=(
            "Return ONLY JSON with keys: recommended_action, confidence, headline, "
            "summary, reasons, assumptions, next_step {label, action_type}, options."
        ),
    )
    user = json.dumps(
        {
            "goal": goal,
            "financial_context": financial.model_dump(),
            "seed_recommendation": seed,
        }
    )
    headers = {
        "Authorization": f"Bearer {llm.meta_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
    }
    data = await llm._post_chat_completion(headers=headers, payload=payload)
    content = data["choices"][0]["message"]["content"]
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part) for part in content
        )
    from app.services.llm_service import _extract_json_object

    parsed = _extract_json_object(str(content))
    if not parsed.get("options"):
        parsed["options"] = seed.get("options", [])
    return ToyotaRecommendation.model_validate(parsed)


async def _run_reviewer(
    *,
    run_id: str,
    agent: AgentDefinition,
    ctx: dict[str, Any],
    costs: RunCostTracker,
    request: CreateRunRequest,
    policy: TeamPolicy,
) -> None:
    """Optional thorough/strict LLM review — STRONG tier; approved summaries only."""
    from app.runtime_mode import llm_deterministic_synthesis_allowed

    recommendation = ctx.get("recommendation")
    financial = ctx.get("financial")
    if recommendation is None or financial is None:
        ctx["review_status"] = "FAIL"
        _audit(
            run_id,
            agent_id=agent.id,
            event_type="TASK_FAILED",
            executor_type=ExecutorType.LLM,
            resource="review_recommendation",
            result="FAIL",
            message="Reviewer missing recommendation or FinancialContext.",
        )
        return

    try:
        validate_task_permissions(agent, "review_recommendation", require_integrations=False)
    except PermissionDenied as exc:
        ctx["permission_denials"] += 1
        ctx["review_status"] = "FAIL"
        _audit(
            run_id,
            agent_id=agent.id,
            event_type="PERMISSION_CHECK",
            executor_type=ExecutorType.LLM,
            resource="review_recommendation",
            result="DENIED",
            message=exc.message,
        )
        return

    estimated_cost = 0.005
    if not costs.can_afford_llm(estimated_cost, agent=agent):
        ctx["budget_ok"] = False
        ctx["review_status"] = "FAIL"
        _audit(
            run_id,
            agent_id=agent.id,
            event_type="BUDGET_LIMIT",
            executor_type=ExecutorType.LLM,
            resource="review_recommendation",
            result="DENIED",
            message="Reviewer blocked by server/team/agent budget.",
        )
        return

    try:
        model_decision = choose_model(task_kind="review", work_style=policy.work_style, risk="high")
    except ModelRoutingError as exc:
        ctx["review_status"] = "FAIL"
        _audit(
            run_id,
            agent_id=agent.id,
            event_type="MODEL_DECISION",
            result="FAILED",
            message=str(exc),
        )
        return

    _audit(
        run_id,
        agent_id=agent.id,
        event_type="MODEL_DECISION",
        executor_type=ExecutorType.LLM,
        resource="review_recommendation",
        result=model_decision.tier,
        message=(
            f"tier={model_decision.tier} model={model_decision.model}. "
            f"Reason: {model_decision.reason}"
        ),
    )

    llm = get_llm_service()
    allow_deterministic = llm_deterministic_synthesis_allowed()
    status = "PASS"
    issues: list[str] = []

    if llm.is_configured():
        try:
            assert_integration_allowed(agent, "meta")
            system = build_agent_system_prompt(
                agent,
                policy,
                task_instruction=(
                    "Review the proposed mobility recommendation against team and agent "
                    "guardrails and financial safety. Use only the approved summaries. "
                    "Never request raw Nessie rows."
                ),
                response_format='Return ONLY JSON: {"status":"PASS"|"FAIL","issues":[...]}',
            )
            user = json.dumps(
                {
                    "financial_context": financial.model_dump(),
                    "recommendation": recommendation.model_dump(),
                    "goal": request.goal,
                }
            )
            headers = {
                "Authorization": f"Bearer {llm.meta_api_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": model_decision.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.0,
            }
            data = await llm._post_chat_completion(headers=headers, payload=payload)
            content = data["choices"][0]["message"]["content"]
            if isinstance(content, list):
                content = "".join(
                    part.get("text", "") if isinstance(part, dict) else str(part)
                    for part in content
                )
            from app.services.llm_service import _extract_json_object

            parsed = _extract_json_object(str(content))
            status = str(parsed.get("status") or "FAIL").upper()
            if status not in {"PASS", "FAIL"}:
                status = "FAIL"
                issues.append("Reviewer returned invalid status")
            issues = list(parsed.get("issues") or issues)
            costs.record_llm(
                cost_usd=estimated_cost, agent_id=agent.id, model_tier=model_decision.tier
            )
            _audit(
                run_id,
                agent_id=agent.id,
                event_type="LLM_CALL",
                executor_type=ExecutorType.LLM,
                resource="review_recommendation",
                result=status,
                message=f"Reviewer LLM result={status}; issues={issues}",
                cost_usd=estimated_cost,
            )
        except Exception as exc:
            _audit(
                run_id,
                agent_id=agent.id,
                event_type="LLM_ERROR",
                executor_type=ExecutorType.LLM,
                resource="review_recommendation",
                result="FAILED",
                message=f"Reviewer LLM failed: {exc}",
            )
            if not allow_deterministic:
                ctx["review_status"] = "FAIL"
                return
            status, issues = _deterministic_review(financial, recommendation)
            _audit(
                run_id,
                agent_id=agent.id,
                event_type="CODE_FALLBACK",
                executor_type=ExecutorType.CODE,
                resource="review_recommendation",
                result=status,
                message="Dev/test deterministic review after LLM failure.",
            )
    else:
        if not allow_deterministic:
            ctx["review_status"] = "FAIL"
            _audit(
                run_id,
                agent_id=agent.id,
                event_type="LLM_ERROR",
                result="FAILED",
                message="Reviewer requires LLM but none configured.",
            )
            return
        status, issues = _deterministic_review(financial, recommendation)
        _audit(
            run_id,
            agent_id=agent.id,
            event_type="CODE_FALLBACK",
            executor_type=ExecutorType.CODE,
            resource="review_recommendation",
            result=status,
            message="Dev/test deterministic review (LLM unconfigured).",
        )

    ctx["review_status"] = status
    append_output(
        run_id,
        OutputItem(
            id=f"out-{uuid4().hex[:8]}",
            title="Reviewer Result",
            agent_id=agent.id,
            type="REVIEW",
            summary=f"Review {status}",
            content=json.dumps({"status": status, "issues": issues}),
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            cost_usd=estimated_cost if llm.is_configured() and not allow_deterministic else 0.0,
            source_type="LLM" if llm.is_configured() else "CODE",
        ),
    )


def _deterministic_review(
    financial: FinancialContext,
    recommendation: ToyotaRecommendation,
) -> tuple[str, list[str]]:
    """Generic safety review — no customer-id or stress→action forcing."""
    issues: list[str] = []
    if recommendation_contains_raw_nessie(recommendation.model_dump()):
        issues.append("Recommendation appears to contain raw Nessie-like fields")
    action_to_option = {
        "BUY_CURRENT_VEHICLE": "buy-current",
        "UPGRADE_VEHICLE": "upgrade",
    }
    opt_id = action_to_option.get(recommendation.recommended_action)
    if opt_id and recommendation.options:
        chosen = next((o for o in recommendation.options if o.id == opt_id), None)
        if chosen is not None and chosen.safe_for_budget is False:
            issues.append(
                "Recommended ownership option exceeds the safe mobility budget"
            )
        if (
            chosen is not None
            and chosen.monthly_cost is not None
            and chosen.monthly_cost > financial.safe_mobility_budget
        ):
            issues.append(
                "Recommended monthly cost exceeds safe_mobility_budget"
            )
    return ("FAIL" if issues else "PASS"), issues
