"""Ensure TeamDefinition always carries enforceable budgets and TeamPolicy."""

from __future__ import annotations

from app.models import AgentDefinition, BudgetPolicy, TeamDefinition, TeamPolicy


def ensure_team_runtime_config(team: TeamDefinition) -> TeamDefinition:
    """
    Return a deep-ish copy with defaults filled so planned/saved teams always
    have agent.budget and team.policy for enforcement.
    Does not broaden tools/permissions.
    """
    agents: list[AgentDefinition] = []
    for a in team.agents:
        budget = a.budget
        # Clamp missing/zero budgets from imperfect planner JSON so enforcement is real.
        max_calls = budget.max_llm_calls if budget.max_llm_calls and budget.max_llm_calls > 0 else 2
        max_cost = (
            budget.max_llm_cost_usd
            if budget.max_llm_cost_usd and budget.max_llm_cost_usd > 0
            else 0.05
        )
        budget = BudgetPolicy(max_llm_cost_usd=max_cost, max_llm_calls=max_calls)
        agents.append(a.model_copy(update={"budget": budget}))

    policy = team.policy or TeamPolicy()
    if not policy.work_style:
        policy = policy.model_copy(update={"work_style": "balanced"})
    if policy.max_llm_calls <= 0:
        policy = policy.model_copy(update={"max_llm_calls": 6})
    if policy.max_llm_cost_usd <= 0:
        policy = policy.model_copy(update={"max_llm_cost_usd": 0.20})

    return team.model_copy(update={"agents": agents, "policy": policy})
