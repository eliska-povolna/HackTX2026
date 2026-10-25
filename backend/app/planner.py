"""Team planner: LLM when available, otherwise deterministic fallback."""

from __future__ import annotations

import logging
import uuid

from app.models import (
    AgentDefinition,
    AgentPermissions,
    BudgetPolicy,
    PlanTeamRequest,
    PlanTeamResponse,
    PlannerSource,
    TeamDefinition,
)
from app.services.llm_service import LLMUnavailableError, get_llm_service

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
]


def build_fallback_team(goal: str, max_team_size: int) -> TeamDefinition:
    """Deterministic Toyota mobility team used when LLM is unavailable."""
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
        ),
        AgentDefinition(
            id="decision-agent",
            name="Dana",
            role="Decision Specialist",
            purpose="Combine approved derived outputs and produce a clear mobility recommendation.",
            skills=["recommendation", "tradeoff_synthesis", "customer_guidance"],
            instructions="Use financial_summary and vehicle/mobility summaries only. Never request raw banking rows.",
            allowed_tools=["score_mobility_options", "synthesize_recommendation"],
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
        ),
        AgentDefinition(
            id="reviewer-agent",
            name="Quinn",
            role="Reviewer",
            purpose="Optional high-risk or strict-mode review of the recommendation.",
            skills=["policy_review", "risk_check", "budget_compliance"],
            instructions="Run only for strict quality mode or high-risk external actions. Skip on normal demo runs.",
            allowed_tools=[],
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
    )


async def plan_team(request: PlanTeamRequest) -> PlanTeamResponse:
    """Plan a team via LLM only. On failure raise — do not silently substitute a Toyota fallback."""
    from fastapi import HTTPException

    from app.attachments import planning_context_for_ids

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

    llm = get_llm_service()
    try:
        team = await llm.plan_team(
            goal=request.goal,
            max_team_size=request.max_team_size,
            budget_preference=request.budget_preference,
            available_tools=AVAILABLE_TOOLS,
            attachments=attachment_ctx,
        )
        if len(team.agents) > request.max_team_size:
            team.agents = team.agents[: request.max_team_size]
        return PlanTeamResponse(
            team=team,
            planner_source=PlannerSource.LLM,
            fallback_used=False,
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
