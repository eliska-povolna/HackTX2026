"""Minimal no-integration workflow: assemble a tiny LLM team and run one play turn.

Used for goals that need no Toyota/Nessie pack (games, brainstorm, roleplay).
Recruiting and other missing-connector goals stay NEEDS_SETUP.
"""

from __future__ import annotations

from app.models import (
    AgentDefinition,
    AgentPermissions,
    BudgetPolicy,
    ExecutionPlan,
    ExecutorType,
    RiskLevel,
    TaskDefinition,
    TeamDefinition,
    TeamPolicy,
)


SIMPLE_PLAY_ACTION = "simple_play"


def align_simple_play_team(team: TeamDefinition) -> TeamDefinition:
    """Ensure at least one agent can call Meta for a single LLM play turn."""
    agents = list(team.agents or [])
    if not agents:
        agents = [
            AgentDefinition(
                id="play-agent",
                name="Playwright",
                role="Game Host",
                purpose="Run a short interactive or creative turn with no external tools.",
                skills=["conversation", "games"],
                allowed_tools=[SIMPLE_PLAY_ACTION],
                permissions=AgentPermissions(
                    data_scopes=[],
                    actions=["synthesize", "read"],
                ),
                budget=BudgetPolicy(max_llm_calls=3, max_llm_cost_usd=0.08),
                model_policy="llm_allowed",
                allowed_integrations=["meta"],
            )
        ]
    else:
        lead = agents[0]
        tools = list(lead.allowed_tools or [])
        if SIMPLE_PLAY_ACTION not in tools:
            tools.append(SIMPLE_PLAY_ACTION)
        actions = list(lead.permissions.actions or [])
        for a in ("synthesize", "read"):
            if a not in actions:
                actions.append(a)
        integrations = list(lead.allowed_integrations or [])
        if "meta" not in integrations:
            integrations.append("meta")
        budget = lead.budget or BudgetPolicy()
        if budget.max_llm_calls < 1:
            budget = budget.model_copy(update={"max_llm_calls": 2})
        if budget.max_llm_cost_usd <= 0:
            budget = budget.model_copy(update={"max_llm_cost_usd": 0.05})
        agents[0] = lead.model_copy(
            update={
                "allowed_tools": tools,
                "permissions": AgentPermissions(
                    data_scopes=list(lead.permissions.data_scopes or []),
                    actions=actions,
                ),
                "allowed_integrations": integrations,
                "budget": budget,
                "model_policy": lead.model_policy
                if lead.model_policy in {"auto", "llm_allowed"}
                else "llm_allowed",
            }
        )

    policy = team.policy or TeamPolicy()
    policy = policy.model_copy(
        update={
            "workflow_kind": "simple_play",
            "scope": "single_customer",
            "max_llm_calls": max(policy.max_llm_calls, 2),
            "max_llm_cost_usd": max(policy.max_llm_cost_usd, 0.08),
        }
    )
    return team.model_copy(update={"agents": agents, "policy": policy})


def build_simple_play_plan(team: TeamDefinition) -> ExecutionPlan:
    team = align_simple_play_team(team)
    lead = team.agents[0]
    return ExecutionPlan(
        tasks=[
            TaskDefinition(
                id="task-simple-play",
                name="Play / respond",
                assigned_agent_id=lead.id,
                executor=ExecutorType.LLM,
                action=SIMPLE_PLAY_ACTION,
                depends_on=[],
                risk_level=RiskLevel.low,
            )
        ]
    )
