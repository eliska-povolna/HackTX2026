"""Toyota demo execution engine: CODE / TOOL / LLM with dependencies."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from app.cost_optimizer import RunCostTracker
from app.evaluators import recommendation_contains_raw_nessie, run_evaluators
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
    ToyotaRecommendation,
)
from app.permissions import (
    PermissionDenied,
    assert_tool_allowed,
    check_tool_allowed,
    decision_agent_blocked_from_raw_nessie,
)
from app.run_store import append_audit, append_output, get_run, record_task_event, update_run
from app.services.llm_service import LLMUnavailableError, get_llm_service
from app.tools.deterministic import build_fallback_recommendation, score_mobility_options
from app.tools.financial import build_financial_context
from app.tools.nessie import get_nessie_client
from app.tools.toyota import load_toyota_customer

logger = logging.getLogger(__name__)


def build_toyota_execution_plan(team: TeamDefinition) -> ExecutionPlan:
    agents = {a.id: a for a in team.agents}
    context_id = _pick_agent(agents, ["context-agent"], "context")
    financial_id = _pick_agent(agents, ["financial-agent"], "financial")
    vehicle_id = _pick_agent(agents, ["vehicle-agent"], "vehicle")
    decision_id = _pick_agent(agents, ["decision-agent"], "decision")

    return ExecutionPlan(
        tasks=[
            TaskDefinition(
                id="load-toyota-context",
                name="Load Toyota customer context",
                assigned_agent_id=context_id,
                executor=ExecutorType.TOOL,
                action="load_toyota_customer",
                depends_on=[],
                risk_level=RiskLevel.low,
            ),
            TaskDefinition(
                id="load-nessie-financials",
                name="Load banking data",
                assigned_agent_id=financial_id,
                executor=ExecutorType.TOOL,
                action="nessie_load_customer_financials",
                depends_on=["load-toyota-context"],
                risk_level=RiskLevel.medium,
            ),
            TaskDefinition(
                id="build-financial-context",
                name="Build financial context",
                assigned_agent_id=financial_id,
                executor=ExecutorType.CODE,
                action="build_financial_context",
                depends_on=["load-nessie-financials"],
                risk_level=RiskLevel.medium,
            ),
            TaskDefinition(
                id="score-mobility-options",
                name="Compare ownership options",
                assigned_agent_id=vehicle_id,
                executor=ExecutorType.CODE,
                action="score_mobility_options",
                depends_on=["build-financial-context", "load-toyota-context"],
                risk_level=RiskLevel.medium,
            ),
            TaskDefinition(
                id="synthesize-recommendation",
                name="Synthesize mobility recommendation",
                assigned_agent_id=decision_id,
                executor=ExecutorType.LLM,
                action="synthesize_recommendation",
                depends_on=["score-mobility-options", "build-financial-context"],
                risk_level=RiskLevel.medium,
            ),
            TaskDefinition(
                id="run-evaluators",
                name="Run deterministic evaluators",
                assigned_agent_id=decision_id,
                executor=ExecutorType.CODE,
                action="run_evaluators",
                depends_on=["synthesize-recommendation"],
                risk_level=RiskLevel.low,
            ),
        ]
    )


def _pick_agent(agents: dict[str, AgentDefinition], preferred: list[str], hint: str) -> str:
    for pid in preferred:
        if pid in agents:
            return pid
    for aid, agent in agents.items():
        if hint in aid or hint in agent.role.lower():
            return aid
    return next(iter(agents)) if agents else "unknown-agent"


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


def _ensure_plan_tools(team: TeamDefinition, plan: ExecutionPlan) -> dict[str, AgentDefinition]:
    """Ensure agents may use tools required by their assigned plan tasks."""
    agents = {a.id: a.model_copy(deep=True) for a in team.agents}
    for task in plan.tasks:
        agent = agents.get(task.assigned_agent_id)
        if agent is None:
            continue
        needed = task.action
        if needed not in agent.allowed_tools:
            agent.allowed_tools = list(agent.allowed_tools) + [needed]
        if task.action == "nessie_load_customer_financials":
            for t in ("nessie_accounts", "nessie_purchases"):
                if t not in agent.allowed_tools:
                    agent.allowed_tools.append(t)
            if "nessie_raw_financial" not in agent.permissions.data_scopes:
                agent.permissions.data_scopes = list(agent.permissions.data_scopes) + [
                    "nessie_raw_financial",
                    "financial_summary",
                ]
    return agents


async def execute_run(run_id: str, request: CreateRunRequest) -> None:
    """Background execution for one run."""
    plan = build_toyota_execution_plan(request.team)
    agents = _ensure_plan_tools(request.team, plan)
    costs = RunCostTracker()
    ctx: dict[str, Any] = {
        "toyota": None,
        "nessie_raw": None,  # stays inside financial boundary
        "nessie_source": None,
        "financial": None,
        "scored": None,
        "recommendation": None,
        "permission_allows": 0,
        "permission_denials": 0,
        "tool_failures": 0,
        "budget_ok": True,
    }
    nessie = get_nessie_client()
    nessie.clear_run_cache()

    update_run(run_id, lambda r: setattr(r, "status", RunStatus.RUNNING))
    _audit(run_id, agent_id=None, event_type="RUN_CREATED", message="Run execution started.")

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
            if not progress:
                raise RuntimeError("Task dependency deadlock")

        update_run(
            run_id,
            lambda r: (
                setattr(r, "status", RunStatus.REVIEWING),
                setattr(r, "metrics", type(r.metrics).model_validate(costs.metrics_dict())),
            ),
        )

        # Skip reviewer unless strict / high risk
        if not costs.should_run_reviewer(request.quality_mode.value, False):
            _audit(
                run_id,
                agent_id="reviewer-agent",
                event_type="TASK_COMPLETED",
                message="Reviewer skipped — normal risk. Estimated AI cost saved.",
                result="SKIPPED",
            )
            costs.llm_calls_avoided += 1

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
        logger.exception("Run %s failed: %s", run_id, exc)
        update_run(
            run_id,
            lambda r: (
                setattr(r, "status", RunStatus.FAILED),
                setattr(r, "metrics", type(r.metrics).model_validate(costs.metrics_dict())),
            ),
        )
        _audit(
            run_id,
            agent_id=None,
            event_type="RUN_COMPLETED",
            message=f"Run failed: {exc}",
            result="FAILED",
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
    from app.models import AgentPermissions, BudgetPolicy

    agent = agents.get(task.assigned_agent_id)
    if agent is None:
        agent = AgentDefinition(
            id=task.assigned_agent_id,
            name=task.assigned_agent_id,
            role="Agent",
            purpose="",
            skills=[],
            allowed_tools=[task.action],
            permissions=AgentPermissions(data_scopes=["financial_summary"], actions=["read"]),
            budget=BudgetPolicy(max_llm_cost_usd=0.05, max_llm_calls=2),
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
        if task.executor == ExecutorType.TOOL:
            tool_name, data_source, activity = await _run_tool(
                task, agent, request, ctx, run_id
            )
            costs.record_tool()
            ctx["permission_allows"] += 1
        elif task.executor == ExecutorType.CODE:
            activity = await _run_code(task, agent, ctx, run_id, costs)
            data_source = DataSource.LOCAL_CODE.value
        elif task.executor == ExecutorType.LLM:
            activity, cost, ok = await _run_llm(task, agent, ctx, run_id, costs, request)
            data_source = DataSource.LLM.value
            if not ok:
                ctx["budget_ok"] = False
                raise RuntimeError("LLM budget exceeded")
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
    _audit(
        run_id,
        agent_id=agent.id,
        event_type="PERMISSION_CHECK",
        executor_type=ExecutorType.TOOL,
        resource=tool,
        result="ALLOWED",
        message=f"{agent.name} may use {tool}.",
    )

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
            message=f"Retrieved financial data ({source.value}).",
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

    raise RuntimeError(f"Unsupported CODE action: {task.action}")


async def _run_llm(
    task: RunTaskState,
    agent: AgentDefinition,
    ctx: dict[str, Any],
    run_id: str,
    costs: RunCostTracker,
    request: CreateRunRequest,
) -> tuple[str, float, bool]:
    if task.action != "synthesize_recommendation":
        raise RuntimeError(f"Unsupported LLM action: {task.action}")

    from app.runtime_mode import llm_deterministic_synthesis_allowed

    # Decision agent must only see FinancialContext + scored options — never raw Nessie
    financial: FinancialContext = ctx["financial"]
    scored = ctx["scored"]
    seed = scored["recommendation_seed"]
    raw_blocked = ctx.get("nessie_raw") is not None
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
    if not costs.can_afford_llm(estimated_cost):
        _audit(
            run_id,
            agent_id=agent.id,
            event_type="BUDGET_LIMIT",
            executor_type=ExecutorType.LLM,
            resource="synthesize_recommendation",
            result="DENIED",
            message="LLM synthesis blocked by run budget.",
            cost_usd=0.0,
        )
        raise RuntimeError("BUDGET_LIMIT: cannot afford LLM synthesis")

    llm = get_llm_service()
    allow_deterministic = llm_deterministic_synthesis_allowed()
    recommendation: ToyotaRecommendation
    used_real_llm = False
    cost = 0.0

    if llm.is_configured():
        try:
            recommendation = await _llm_synthesize(financial, seed, request.goal)
            used_real_llm = True
            cost = estimated_cost
            costs.record_llm(cost_usd=cost)
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

    # Safety: keep Toyota demo scenario split truthful if the model drifts.
    safety_corrected = False
    if financial.financial_stress == "LOW" and recommendation.recommended_action not in {
        "BUY_CURRENT_VEHICLE"
    }:
        recommendation = build_fallback_recommendation(seed)
        safety_corrected = True
    if financial.financial_stress == "HIGH" and recommendation.recommended_action in {
        "BUY_CURRENT_VEHICLE",
        "UPGRADE_VEHICLE",
    }:
        recommendation = build_fallback_recommendation(seed)
        safety_corrected = True
    if safety_corrected:
        _audit(
            run_id,
            agent_id=agent.id,
            event_type="SAFETY_CORRECTION",
            executor_type=ExecutorType.CODE,
            resource="recommended_action",
            result="CORRECTED",
            message="Deterministic safety validator corrected recommended_action for financial stress.",
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
            message="Generated customer-facing mobility recommendation via live LLM.",
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


async def _llm_synthesize(
    financial: FinancialContext,
    seed: dict[str, Any],
    goal: str,
) -> ToyotaRecommendation:
    """Ask LLM to polish the deterministic recommendation — never send raw Nessie."""
    llm = get_llm_service()
    if not llm.is_configured():
        raise LLMUnavailableError("No LLM provider configured")
    system = (
        "You polish a Toyota mobility recommendation. Return ONLY JSON matching keys: "
        "recommended_action, confidence, headline, summary, reasons, assumptions, "
        "next_step {label, action_type}, options. Do not invent raw bank transactions. "
        "Keep recommended_action aligned with the seed unless the financial context clearly "
        "requires a different safe mobility choice."
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
        "model": llm._model_for_planning(),
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
