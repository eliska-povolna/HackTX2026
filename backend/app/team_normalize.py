"""Ensure TeamDefinition always carries enforceable budgets and TeamPolicy."""

from __future__ import annotations

from app.models import AgentDefinition, AgentPermissions, BudgetPolicy, TeamDefinition, TeamPolicy

# Planner / saved-team labels → canonical scopes used by ACTION_REQUIREMENTS.
DATA_SCOPE_ALIASES: dict[str, str] = {
    "customer_financials": "nessie_raw_financial",
    "customer_financial": "nessie_raw_financial",
    "bank_accounts": "nessie_raw_financial",
    "transactions": "nessie_raw_financial",
    "raw_transactions": "nessie_raw_financial",
    "banking": "nessie_raw_financial",
    "raw_financials": "nessie_raw_financial",
    "nessie_financials": "nessie_raw_financial",
    "financials": "financial_summary",
    "finance": "financial_summary",
    "financial_context": "financial_summary",
    "customer_finance": "financial_summary",
    "derived_financials": "financial_summary",
}


def canonicalize_scopes(scopes: list[str]) -> list[str]:
    out: list[str] = []
    for raw in scopes or []:
        key = (raw or "").strip()
        canon = DATA_SCOPE_ALIASES.get(key.lower(), key)
        if canon and canon not in out:
            out.append(canon)
    return out


def canonicalize_agent_permissions(agent: AgentDefinition) -> AgentDefinition:
    scopes = canonicalize_scopes(list(agent.permissions.data_scopes or []))
    actions = list(agent.permissions.actions or [])
    if agent.allowed_tools and not actions:
        actions = ["read"]
    if scopes == list(agent.permissions.data_scopes or []) and actions == list(
        agent.permissions.actions or []
    ):
        return agent
    return agent.model_copy(
        update={"permissions": AgentPermissions(data_scopes=scopes, actions=actions)}
    )


def ensure_team_runtime_config(team: TeamDefinition) -> TeamDefinition:
    """
    Return a deep-ish copy with defaults filled so planned/saved teams always
    have agent.budget and team.policy for enforcement.
    Renames claimed scope labels and fills an empty action list with read.
    Does not add tools — role grants stay in plan-time workflow alignment.
    """
    agents: list[AgentDefinition] = []
    for a in team.agents:
        a = canonicalize_agent_permissions(a)
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
