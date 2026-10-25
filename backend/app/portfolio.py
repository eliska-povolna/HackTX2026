"""Portfolio handlers using installed tools and derived data only.

The same executor owns permissions, budgets, task state and failure handling.
There are no financial side effects or customer-id outcome overrides.
"""

from datetime import datetime, timezone
from uuid import uuid4
import json

from fastapi import HTTPException

from app.models import AgentPermissions, ExecutorType, OutputItem, PortfolioResult
from app.permissions import ACTION_REQUIREMENTS
from app.tools.deterministic import (
    calculate_affordability,
    evaluate_mobility_relevance,
    rank_customers,
    score_mobility_options,
)
from app.tools.financial import build_financial_context
from app.tools.toyota import load_toyota_customers
from app.tools.nessie import get_nessie_client


def align_portfolio_team(team, kind):
    """Plan-time minimum grants; runtime never calls this function."""
    working = team.model_copy(deep=True)
    if len(working.agents) < 2:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "NEEDS_SETUP",
                "message": "Portfolio planning requires separate financial and explanation responsibilities.",
                "recoverable": True,
            },
        )

    def choose(words, tools, excluded=()):
        scored = []
        for agent in working.agents:
            if agent.id in excluded:
                continue
            blob = " ".join([agent.role, agent.purpose, *agent.skills]).lower()
            score = sum(5 for w in words if w in blob) + sum(
                20 for t in tools if t in agent.allowed_tools
            )
            if score:
                scored.append((-score, agent.id, agent))
        if not scored:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "NEEDS_SETUP",
                    "message": "Planned team lacks a matching portfolio financial or explanation responsibility.",
                    "recoverable": True,
                },
            )
        return sorted(scored, key=lambda x: (x[0], x[1]))[0][2]

    finance = choose(
        ["financial", "banking", "cashflow", "affordability"],
        ["nessie_load_customer_financials", "build_portfolio_financials"],
    )
    decision = choose(
        ["decision", "strateg", "synthesis", "shortlist", "ranking"],
        ["explain_shortlist", "synthesize_recommendation", "rank_customers"],
        [finance.id],
    )
    decision.permissions.data_scopes = [
        scope
        for scope in decision.permissions.data_scopes
        if scope != "nessie_raw_financial"
    ]
    decision.allowed_tools = [
        tool
        for tool in decision.allowed_tools
        if tool
        not in {
            "nessie_load_customer_financials",
            "nessie_accounts",
            "nessie_purchases",
            "build_portfolio_financials",
        }
    ]
    decision.allowed_integrations = [
        name for name in decision.allowed_integrations if name != "nessie"
    ]
    context_candidates = [
        a
        for a in working.agents
        if "context" in a.role.lower() or "load_toyota_portfolio" in a.allowed_tools
    ]
    context = (
        sorted(context_candidates, key=lambda a: a.id)[0]
        if context_candidates
        else finance
    )

    assignments = [
        (context, "load_toyota_portfolio"),
        (finance, "build_portfolio_financials"),
        (decision, "rank_customers"),
        (decision, "explain_shortlist"),
    ]
    if kind == "mobility_decision":
        assignments += [
            (context, "filter_mobility_portfolio"),
            (decision, "score_mobility_options"),
        ]
    for agent, action in assignments:
        req = ACTION_REQUIREMENTS[action]
        agent.allowed_tools = sorted(set(agent.allowed_tools) | set(req.tools))
        agent.permissions = AgentPermissions(
            data_scopes=sorted(
                set(agent.permissions.data_scopes) | set(req.data_scopes)
            ),
            actions=sorted(
                set(agent.permissions.actions)
                | set(req.actions)
                | ({req.actions_any[0]} if req.actions_any else set())
            ),
        )
        agent.allowed_integrations = sorted(
            set(agent.allowed_integrations) | set(req.integrations)
        )
    if (
        decision.model_policy == "deterministic_only"
        or decision.budget.max_llm_calls < 1
        or decision.budget.max_llm_cost_usd <= 0
    ):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "NEEDS_SETUP",
                "message": "Shortlist explanation requires an LLM-enabled agent with a positive model budget.",
                "recoverable": True,
            },
        )
    return working


def run_portfolio_code(task, ctx, run_id, agent_id):
    from app.executor import _audit

    if task.action == "load_toyota_portfolio":
        ctx["portfolio"] = load_toyota_customers()
        return f"Loaded {len(ctx['portfolio'])} synthetic Toyota customers."
    if task.action == "filter_mobility_portfolio":
        relevant, skipped = [], []
        for customer in ctx["portfolio"]:
            relevance = evaluate_mobility_relevance(customer)
            if relevance["relevant"]:
                relevant.append(customer)
            else:
                skipped.append(
                    {
                        "customer_id": customer["id"],
                        "name": customer["name"],
                        "reason": "; ".join(relevance["reasons"]),
                    }
                )
        ctx["portfolio"] = relevant
        ctx["skipped_customers"] = skipped
        ctx["no_action_needed"] = not relevant
        _audit(
            run_id,
            agent_id=agent_id,
            event_type="RELEVANCE_CHECK",
            executor_type=ExecutorType.CODE,
            result="RELEVANT" if relevant else "NO_ACTION_NEEDED",
            message=f"Relevant={len(relevant)}; no-action={len(skipped)}. No banking was requested for skipped customers.",
        )
        return f"Filtered portfolio to {len(relevant)} relevant customers; skipped {len(skipped)}."
    if task.action == "rank_customers":
        rows = ctx["portfolio_summaries"]
        mobility = ctx["team_policy"].workflow_kind == "mobility_decision"
        if mobility:
            for row in rows:
                from app.models import FinancialContext

                fc = FinancialContext.model_validate(row["financial_context"])
                row["mobility_recommendation"] = score_mobility_options(
                    row["toyota"], fc
                )["recommendation_seed"]
        result = rank_customers(rows, purpose="mobility" if mobility else "loan")
        for candidate in result["candidates"]:
            row = next(r for r in rows if r["customer_id"] == candidate["customer_id"])
            candidate["recommended_action"] = (
                row.get("mobility_recommendation") or {}
            ).get("recommended_action")
            candidate["next_step"] = (row.get("mobility_recommendation") or {}).get(
                "next_step"
            )
        ctx["portfolio_result"] = PortfolioResult(
            purpose=result["purpose"],
            candidates=result["candidates"],
            skipped_customers=ctx.get("skipped_customers", []),
        )
        _audit(
            run_id,
            agent_id=agent_id,
            event_type="CODE",
            executor_type=ExecutorType.CODE,
            resource="rank_customers",
            message=f"Ranked {len(rows)} customers for {result['purpose']} using deterministic derived financial and Toyota signals.",
        )
        return f"Ranked {len(rows)} customers for {result['purpose']}; no model call."
    return None


def build_portfolio_financials(ctx, run_id, agent_id):
    from app.executor import _audit

    summaries = []
    sources = set()
    for customer in ctx["portfolio"]:
        raw, source = get_nessie_client().load_customer_financials(
            customer["nessie_customer_id"]
        )
        # Raw banking exists only inside this financial handler. Never retain it
        # in context, run payloads, audit messages or downstream model inputs.
        financial = build_financial_context(raw)
        del raw
        sources.add(source.value)
        _audit(
            run_id,
            agent_id=agent_id,
            event_type="TOOL_CALL",
            executor_type=ExecutorType.TOOL,
            resource="nessie_load_customer_financials",
            result="ALLOWED",
            message=f"Customer {customer['id']}: financial data source={source.value}; raw rows remain inside the financial boundary.",
        )
        _audit(
            run_id,
            agent_id=agent_id,
            event_type="CODE",
            executor_type=ExecutorType.CODE,
            resource="build_financial_context",
            message=f"Customer {customer['id']}: derived FinancialContext and affordability with local code.",
        )
        summaries.append(
            {
                "customer_id": customer["id"],
                "name": customer["name"],
                "toyota": customer,
                "financial": calculate_affordability(financial),
                "financial_context": financial.model_dump(),
                "relevance": evaluate_mobility_relevance(customer),
                "nessie_provenance": source.value,
            }
        )
    ctx["portfolio_summaries"] = summaries
    source = next(iter(sources)) if len(sources) == 1 else "NESSIE_MIXED"
    return (
        "build_portfolio_financials",
        source,
        f"Derived banking summaries for {len(summaries)} customers; sources={','.join(sorted(sources))}.",
    )


async def explain_portfolio(task, agent, ctx, run_id, costs, request):
    from app.executor import BudgetLimitError, _audit
    from app.model_router import choose_model
    from app.prompts import build_agent_system_prompt
    from app.run_store import append_output
    from app.runtime_mode import llm_deterministic_synthesis_allowed
    from app.services.llm_service import get_llm_service, LLMUnavailableError

    result = ctx["portfolio_result"]
    # Deterministic table is authoritative; model can only explain this table.
    minimized = [
        {
            "customer_id": c["customer_id"],
            "score": c["score"],
            "reasons": c["reasons"],
            "financial_summary": c["financial_summary"],
            "recommended_action": c.get("recommended_action"),
        }
        for c in result.candidates
    ]
    _audit(
        run_id,
        agent_id=agent.id,
        event_type="DATA_MINIMIZATION",
        executor_type=ExecutorType.LLM,
        resource="explain_shortlist",
        message="Shortlist model receives derived scores, reasons and financial summaries only; no raw accounts, purchases, transactions or bank IDs.",
    )
    estimate = 0.0045
    if not costs.can_afford_llm(estimate, agent=agent):
        _audit(
            run_id,
            agent_id=agent.id,
            event_type="BUDGET_LIMIT",
            resource=task.action,
            result="DENIED",
            message="Shortlist explanation blocked by server/team/agent budget.",
        )
        raise BudgetLimitError("BUDGET_LIMIT: cannot afford shortlist explanation")
    decision = choose_model(
        task_kind="synthesis", work_style=ctx["work_style"], risk=task.risk_level.value
    )
    _audit(
        run_id,
        agent_id=agent.id,
        event_type="MODEL_DECISION",
        executor_type=ExecutorType.LLM,
        resource=task.action,
        result=decision.tier,
        message=f"tier={decision.tier} model={decision.model}. Reason: {decision.reason}",
    )
    llm = get_llm_service()
    if llm.is_configured():
        system = build_agent_system_prompt(
            agent,
            ctx["team_policy"],
            task_instruction="Explain the supplied deterministic demo ranking. Do not change scores, invent eligibility decisions, or claim to approve loans or send offers. For mobility explain the supplied next actions. Distinguish fixture banking from live Nessie. Use a short paragraph; only supplied derived facts.",
        )
        data = await llm._post_chat_completion(
            headers={
                "Authorization": f"Bearer {llm.meta_api_key}",
                "Content-Type": "application/json",
            },
            payload={
                "model": decision.model,
                "messages": [
                    {"role": "system", "content": system},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {"purpose": result.purpose, "ranked_summaries": minimized}
                        ),
                    },
                ],
                "temperature": 0.1,
            },
        )
        content = data["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        if not isinstance(content, str) or not content.strip():
            raise LLMUnavailableError("Shortlist model returned an empty explanation")
        result.narrative = content.strip()[:6000]
        costs.record_llm(cost_usd=estimate, agent_id=agent.id, model_tier=decision.tier)
        _audit(
            run_id,
            agent_id=agent.id,
            event_type="LLM_CALL",
            executor_type=ExecutorType.LLM,
            resource=task.action,
            result="ALLOWED",
            message="Live model explained the deterministic shortlist; rankings were not model-generated.",
            cost_usd=estimate,
        )
        cost, source = estimate, "LLM"
    elif llm_deterministic_synthesis_allowed():
        result.narrative = "Deterministic demo ranking calculated from derived financial and Toyota context. No live model was used."
        _audit(
            run_id,
            agent_id=agent.id,
            event_type="CODE_FALLBACK",
            executor_type=ExecutorType.CODE,
            resource=task.action,
            message="Test/development-only deterministic shortlist explanation; no LLM call.",
        )
        cost, source = 0.0, "CODE"
    else:
        raise LLMUnavailableError(
            "Shortlist explanation requires the configured model provider"
        )
    append_output(
        run_id,
        OutputItem(
            id=f"out-{uuid4().hex[:8]}",
            title="Portfolio shortlist",
            agent_id=agent.id,
            type="SHORTLIST",
            summary=result.narrative,
            content=result.narrative,
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            cost_usd=cost,
            source_type=source,
        ),
    )
    return "Explained deterministic portfolio ranking.", cost, True


def validate_portfolio_result(result, expected_ids):
    """Evaluate produced output, rather than issuing unconditional PASS badges."""
    import math
    from app.models import EvaluatorResult, EvaluatorStatus

    actual = [c.get("customer_id") for c in result.candidates]
    scores = [c.get("score") for c in result.candidates]
    valid = len(actual) == len(set(actual)) and set(actual) == set(expected_ids)
    valid = valid and all(
        isinstance(s, (int, float)) and math.isfinite(s) and 0 <= s <= 100
        for s in scores
    )
    valid = valid and scores == sorted(scores, reverse=True)
    if result.purpose == "mobility":
        valid = valid and all(
            c.get("recommended_action")
            in {
                "BUY_CURRENT_VEHICLE",
                "RETURN_VEHICLE",
                "UPGRADE_VEHICLE",
                "FLEXIBLE_MOBILITY",
            }
            for c in result.candidates
        )

    def contains_raw(value):
        if isinstance(value, dict):
            return any(
                k
                in {
                    "accounts",
                    "purchases",
                    "transactions",
                    "nessie_customer_id",
                    "account_id",
                }
                or contains_raw(v)
                for k, v in value.items()
            )
        return isinstance(value, list) and any(contains_raw(v) for v in value)

    minimized = not contains_raw(result.model_dump())
    return [
        EvaluatorResult(
            name="portfolio_integrity",
            label="Portfolio integrity",
            status=EvaluatorStatus.PASS if valid else EvaluatorStatus.FAIL,
            details="Customer coverage, unique IDs, finite scores, sorted order and mobility actions checked.",
        ),
        EvaluatorResult(
            name="data_minimization",
            label="Data minimization",
            status=EvaluatorStatus.PASS if minimized else EvaluatorStatus.FAIL,
            details="Portfolio payload checked for raw banking fields.",
        ),
    ]
