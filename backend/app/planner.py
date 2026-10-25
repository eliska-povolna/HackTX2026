"""Team planner: LLM when available; deterministic Toyota fallback for demos/tests."""

from __future__ import annotations

import logging
import uuid

from app.model_router import normalize_work_style
from app.permissions import PermissionDenied
from app.models import (
    AgentDefinition,
    AgentPermissions,
    BudgetPolicy,
    PlanTeamRequest,
    PlanTeamResponse,
    PlannerSource,
    TeamDefinition,
    TeamPolicy,
)
from app.services.llm_service import LLMUnavailableError, get_llm_service
from app.team_normalize import ensure_team_runtime_config

logger = logging.getLogger(__name__)

AVAILABLE_TOOLS = [
    "nessie_load_customer_financials",
    "nessie_accounts",
    "nessie_purchases",
    "build_financial_context",
    "load_toyota_customer",
    "evaluate_mobility_relevance",
    "calculate_affordability",
    "calculate_emergency_runway",
    "score_mobility_options",
    "synthesize_recommendation",
    "run_evaluators",
    "review_recommendation",
    "load_toyota_portfolio", "filter_mobility_portfolio", "build_portfolio_financials", "rank_customers", "explain_shortlist",
]

DEFAULT_TEAM_GUARDRAILS = [
    "Never expose raw customer financial data outside the financial boundary.",
    "Never include API keys or secrets in outputs.",
]


def work_style_from_budget_preference(preference: str) -> str:
    raw = (preference or "balanced").strip().lower()
    mapping = {
        "fast": "fast",
        "cheap": "fast",
        "economy": "fast",
        "balanced": "balanced",
        "default": "balanced",
        "thorough": "thorough",
        "quality": "thorough",
        "strict": "thorough",
    }
    return normalize_work_style(mapping.get(raw, "balanced"))


def build_fallback_team(goal: str, max_team_size: int, work_style: str = "balanced") -> TeamDefinition:
    """Deterministic Toyota mobility team used for demos/tests and trigger runs."""
    style = normalize_work_style(work_style)
    agents = [
        AgentDefinition(
            id="context-agent",
            name="Casey",
            role="Context Agent",
            purpose="Identify relevant customer and vehicle signals and decide which information is needed.",
            skills=["customer_context", "signal_detection", "data_scoping"],
            instructions="Use only Toyota profile, vehicle, lease, and usage data. Never request raw Nessie transactions.",
            allowed_tools=["load_toyota_customer", "evaluate_mobility_relevance"],
            permissions=AgentPermissions(
                data_scopes=[
                    "toyota_customer_profile",
                    "vehicle_data",
                    "lease_data",
                    "usage_data",
                ],
                actions=["read", "calculate"],
            ),
            guardrails=[
                "Do not access raw Nessie financial data.",
                "Do not invent customer facts not present in approved data.",
            ],
            model_policy="auto",
            budget=BudgetPolicy(max_llm_cost_usd=0.02, max_llm_calls=1),
            estimated_cost_usd=0.002,
            allowed_integrations=[],
        ),
        AgentDefinition(
            id="financial-agent",
            name="Maya",
            role="Financial Context Analyst",
            purpose="Determine what mobility options are financially safe for the customer.",
            skills=["cashflow_analysis", "affordability", "financial_context"],
            instructions="Minimize exposure of raw banking data. Emit only FinancialContext downstream.",
            allowed_tools=[
                "nessie_load_customer_financials",
                "nessie_accounts",
                "nessie_purchases",
                "build_financial_context",
                "calculate_affordability",
                "calculate_emergency_runway",
            ],
            permissions=AgentPermissions(
                data_scopes=["nessie_raw_financial", "financial_summary"],
                actions=["read", "calculate"],
            ),
            guardrails=[
                "Do not expose raw transactions to downstream agents.",
                "Do not create transfers or financial obligations.",
            ],
            model_policy="auto",
            budget=BudgetPolicy(max_llm_cost_usd=0.05, max_llm_calls=1),
            estimated_cost_usd=0.005,
            allowed_integrations=["nessie"],
        ),
        AgentDefinition(
            id="vehicle-agent",
            name="Riley",
            role="Vehicle / Mobility Analyst",
            purpose="Evaluate vehicle fit, usage, contract terms, and mobility alternatives.",
            skills=["vehicle_fit", "usage_analysis", "lease_evaluation", "mobility_options"],
            instructions="Combine Toyota vehicle/contract/usage with derived financial summary only.",
            allowed_tools=["load_toyota_customer", "score_mobility_options"],
            permissions=AgentPermissions(
                data_scopes=[
                    "toyota_customer_profile",
                    "vehicle_data",
                    "lease_data",
                    "usage_data",
                    "financial_summary",
                ],
                actions=["read", "calculate", "recommend"],
            ),
            guardrails=[
                "Do not access raw Nessie purchase rows.",
                "Prefer deterministic scoring before LLM interpretation.",
            ],
            model_policy="auto",
            budget=BudgetPolicy(max_llm_cost_usd=0.03, max_llm_calls=1),
            estimated_cost_usd=0.004,
            allowed_integrations=[],
        ),
        AgentDefinition(
            id="decision-agent",
            name="Dana",
            role="Decision Specialist",
            purpose="Combine approved derived outputs and produce a clear mobility recommendation.",
            skills=["recommendation", "tradeoff_synthesis", "customer_guidance"],
            instructions="Use financial_summary and vehicle/mobility summaries only. Never request raw banking rows.",
            allowed_tools=[
                "score_mobility_options",
                "synthesize_recommendation",
                "run_evaluators",
            ],
            permissions=AgentPermissions(
                data_scopes=["financial_summary", "vehicle_data", "usage_data", "lease_data"],
                actions=["read", "synthesize", "recommend"],
            ),
            guardrails=[
                "Do not access nessie_raw_financial or raw purchases.",
                "Do not propose silently executed external banking actions.",
            ],
            model_policy="auto",
            budget=BudgetPolicy(max_llm_cost_usd=0.05, max_llm_calls=2),
            estimated_cost_usd=0.01,
            allowed_integrations=["meta"],
        ),
        AgentDefinition(
            id="reviewer-agent",
            name="Quinn",
            role="Reviewer",
            purpose="Optional thorough/high-risk review of the recommendation against guardrails.",
            skills=["policy_review", "risk_check", "budget_compliance"],
            instructions=(
                "Review only approved FinancialContext and the proposed recommendation. "
                "Never request raw Nessie rows. Return PASS or FAIL with issues."
            ),
            allowed_tools=["review_recommendation"],
            permissions=AgentPermissions(
                data_scopes=["financial_summary"],
                actions=["read", "synthesize"],
            ),
            guardrails=[
                "Do not expand data access beyond summary scopes.",
                "Prefer skipping when risk is normal to save AI cost.",
            ],
            model_policy="auto",
            budget=BudgetPolicy(max_llm_cost_usd=0.02, max_llm_calls=1),
            estimated_cost_usd=0.0,
            allowed_integrations=["meta"],
        ),
    ]

    trimmed = agents[: max(1, min(max_team_size, len(agents)))]
    summary = (
        "Combines financial, vehicle and mobility context to recommend the best next step."
        if not goal
        else f"Team assembled for: {goal[:160]}"
    )
    return TeamDefinition(
        id=f"generated-team-{uuid.uuid4().hex[:8]}",
        team_name="Toyota Mobility Decision Team",
        summary=summary,
        agents=trimmed,
        policy=TeamPolicy(
            max_llm_calls=6,
            max_llm_cost_usd=0.20,
            work_style=style,
            guardrails=list(DEFAULT_TEAM_GUARDRAILS),
        ),
    )


async def plan_team(request: PlanTeamRequest) -> PlanTeamResponse:
    """Plan a team via LLM only. On failure raise — do not silently substitute a Toyota fallback."""
    from fastapi import HTTPException

    from app.attachments import planning_context_for_ids

    goal = (request.goal or "").strip()

    attachment_ctx = planning_context_for_ids(list(request.attachment_ids or []))
    missing = [a["id"] for a in attachment_ctx if a.get("missing")]
    if missing:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_REQUEST",
                "message": f"Unknown attachment id(s): {', '.join(missing)}",
                "recoverable": True,
            },
        )

    if len(goal) < 12:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "NEED_MORE_INFO",
                "message": (
                    "Need more information to plan a team. Describe the goal with "
                    "enough detail (who, what outcome, and any domain constraints)."
                ),
                "recoverable": True,
            },
        )

    from app.workflow_support import require_supported_responsibility, require_supported_team

    analysis = require_supported_responsibility(goal, planning=True, answers=request.planning_answers)

    work_style = work_style_from_budget_preference(request.budget_preference)
    llm = get_llm_service()
    try:
        team = await llm.plan_team(
            goal=goal,
            max_team_size=min(request.max_team_size, 2) if analysis.scope.value == "portfolio" else request.max_team_size,
            budget_preference=request.budget_preference,
            available_tools=AVAILABLE_TOOLS,
            attachments=attachment_ctx,
            work_style=work_style,
            context_source_ids=list(request.context_source_ids or []),
        )
        if len(team.agents) > request.max_team_size:
            team.agents = team.agents[: request.max_team_size]
        if team.policy is None:
            team.policy = TeamPolicy(
                work_style=work_style,
                guardrails=list(DEFAULT_TEAM_GUARDRAILS),
            )
        elif not team.policy.work_style:
            team.policy.work_style = work_style
        team = ensure_team_runtime_config(team)
        team.policy.workflow_kind = analysis.workflow_kind.value
        team.policy.scope = analysis.scope.value
        require_supported_team(team)

        # Toyota mobility runs use a fixed workflow. Align capabilities at PLAN
        # time (never at execution), then validate before returning READY_TO_RUN.
        from app.workflow_validate import (
            toyota_workflow_applies,
            validate_team_against_workflow,
        )

        source_ids = list(request.context_source_ids or [])
        if analysis.scope.value == "portfolio":
            from app.portfolio import align_portfolio_team
            from app.pack_workflows import build_pack_execution_plan
            team = align_portfolio_team(team, analysis.workflow_kind.value)
            try:
                build_pack_execution_plan(team, context_source_ids=source_ids or None)
            except (ValueError, PermissionDenied) as exc:
                raise HTTPException(status_code=400, detail={"code": "NEEDS_SETUP", "message": str(exc), "recoverable": True}) from exc
        elif toyota_workflow_applies(goal, source_ids or None):
            result = validate_team_against_workflow(
                team,
                context_source_ids=source_ids or None,
                repair=True,
            )
            team = result.team
            if not result.ok:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "code": "NEEDS_SETUP",
                        "message": (
                            "Planned team is not ready for the mobility workflow: "
                            + "; ".join(result.errors[:4])
                        ),
                        "errors": result.errors,
                        "repairs": result.repairs,
                        "recoverable": True,
                    },
                )
            if result.repairs:
                logger.info(
                    "Plan-time capability alignment (%d): %s",
                    len(result.repairs),
                    "; ".join(result.repairs[:8]),
                )

        return PlanTeamResponse(
            team=team,
            planner_source=PlannerSource.LLM,
            fallback_used=False,
            job_analysis=analysis.model_dump(mode="json"),
        )
    except HTTPException:
        raise
    except (LLMUnavailableError, Exception) as exc:
        logger.warning("Team planner failed (no silent fallback): %s", exc)
        raise HTTPException(
            status_code=503,
            detail={
                "code": "LLM_UNAVAILABLE",
                "message": (
                    "Team planning failed. The AI planner is unavailable or returned "
                    "an invalid response. Check LLM credentials and try again."
                ),
                "recoverable": True,
            },
        ) from exc
