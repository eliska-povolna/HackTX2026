"""Backend-enforced tool, data-scope, action, and integration permissions.

Runtime MUST NOT silently broaden an agent's permissions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.models import AgentDefinition, PermissionDecision, TaskDefinition


class PermissionDenied(Exception):
    def __init__(self, agent_id: str, tool_name: str, message: str):
        self.agent_id = agent_id
        self.tool_name = tool_name
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class ActionRequirements:
    tools: tuple[str, ...] = ()
    data_scopes: tuple[str, ...] = ()
    actions: tuple[str, ...] = ()  # agent must have ALL listed
    actions_any: tuple[str, ...] = ()  # or at least one of these
    integrations: tuple[str, ...] = ()


# Capability → what the assigned agent must already have authorized.
ACTION_REQUIREMENTS: dict[str, ActionRequirements] = {
    "load_toyota_customer": ActionRequirements(
        tools=("load_toyota_customer",),
        actions=("read",),
    ),
    "nessie_load_customer_financials": ActionRequirements(
        tools=("nessie_load_customer_financials",),
        data_scopes=("nessie_raw_financial",),
        actions=("read",),
        integrations=("nessie",),
    ),
    "evaluate_mobility_relevance": ActionRequirements(
        tools=("evaluate_mobility_relevance",),
        actions_any=("read", "calculate"),
    ),
    "build_financial_context": ActionRequirements(
        tools=("build_financial_context",),
        data_scopes=("financial_summary",),
        actions_any=("read", "calculate"),
    ),
    "score_mobility_options": ActionRequirements(
        tools=("score_mobility_options",),
        data_scopes=("financial_summary",),
        actions_any=("read", "calculate", "recommend"),
    ),
    "synthesize_recommendation": ActionRequirements(
        tools=("synthesize_recommendation",),
        data_scopes=("financial_summary",),
        actions_any=("synthesize", "recommend"),
        integrations=("meta",),
    ),
    "run_evaluators": ActionRequirements(
        tools=("run_evaluators",),
        actions_any=("read", "synthesize", "recommend"),
    ),
    "check_affordability_signals": ActionRequirements(
        # Deterministic CODE gate — authorized by data scope + actions, not a tool grant.
        tools=(),
        data_scopes=("financial_summary",),
        actions_any=("read", "calculate"),
    ),
    "review_recommendation": ActionRequirements(
        tools=("review_recommendation",),
        data_scopes=("financial_summary",),
        actions_any=("read", "synthesize"),
        integrations=("meta",),
    ),
    "load_toyota_portfolio": ActionRequirements(
        tools=("load_toyota_portfolio",),
        actions_any=("read", "calculate"),
    ),
    "filter_mobility_portfolio": ActionRequirements(
        tools=("filter_mobility_portfolio", "evaluate_mobility_relevance"),
        actions_any=("read", "calculate"),
    ),
    "build_portfolio_financials": ActionRequirements(
        tools=("build_portfolio_financials", "nessie_load_customer_financials"),
        data_scopes=("nessie_raw_financial", "financial_summary"),
        actions=("read",),
        integrations=("nessie",),
    ),
    "rank_customers": ActionRequirements(
        tools=("rank_customers",),
        data_scopes=("financial_summary",),
        actions_any=("read", "calculate", "recommend"),
    ),
    "calculate_affordability": ActionRequirements(
        tools=("calculate_affordability",),
        data_scopes=("financial_summary",),
        actions_any=("read", "calculate"),
    ),
    "calculate_emergency_runway": ActionRequirements(
        tools=("calculate_emergency_runway",),
        data_scopes=("financial_summary",),
        actions_any=("read", "calculate"),
    ),
    "identify_financial_stress": ActionRequirements(
        tools=("identify_financial_stress",),
        data_scopes=("financial_summary",),
        actions_any=("read", "calculate"),
    ),
    "explain_shortlist": ActionRequirements(
        tools=("explain_shortlist",),
        data_scopes=("financial_summary",),
        actions_any=("synthesize", "recommend"),
        integrations=("meta",),
    ),
}


def check_tool_allowed(agent: AgentDefinition, tool_name: str) -> PermissionDecision:
    if tool_name in agent.allowed_tools:
        return PermissionDecision.ALLOWED
    return PermissionDecision.DENIED


def assert_tool_allowed(agent: AgentDefinition, tool_name: str) -> None:
    decision = check_tool_allowed(agent, tool_name)
    if decision != PermissionDecision.ALLOWED:
        raise PermissionDenied(
            agent.id,
            tool_name,
            f"Agent '{agent.id}' is not allowed to use tool '{tool_name}'.",
        )


def check_data_scope(agent: AgentDefinition, scope: str) -> PermissionDecision:
    if scope in agent.permissions.data_scopes:
        return PermissionDecision.ALLOWED
    return PermissionDecision.DENIED


def agent_allows_integration(agent: AgentDefinition, integration: str) -> bool:
    """
    Only explicitly named integrations are permitted. An empty list grants none.
    Secret values are never stored on the agent.
    """
    allowed = list(getattr(agent, "allowed_integrations", None) or [])
    return integration in allowed


def assert_integration_allowed(agent: AgentDefinition, integration: str) -> None:
    if not agent_allows_integration(agent, integration):
        raise PermissionDenied(
            agent.id,
            integration,
            (
                f"Agent '{agent.id}' is not granted named integration '{integration}'. "
                "Secret values stay server-side; only the named grant is checked."
            ),
        )


RAW_NESSIE_TOOLS = {
    "nessie_load_customer_financials",
    "nessie_accounts",
    "nessie_purchases",
}


def decision_agent_blocked_from_raw_nessie(agent: AgentDefinition, tool_name: str) -> bool:
    """Decision agents must not pull raw Nessie rows."""
    if tool_name not in RAW_NESSIE_TOOLS:
        return False
    if "nessie_raw_financial" in agent.permissions.data_scopes:
        return False
    return True


def validate_task_permissions(
    agent: AgentDefinition,
    task: TaskDefinition | str,
    *,
    require_integrations: bool = True,
) -> None:
    """
    Fail clearly if the agent lacks tools/scopes/actions/integrations for the action.
    Does NOT mutate AgentDefinition.
    """
    action = task.action if hasattr(task, "action") else str(task)
    req = ACTION_REQUIREMENTS.get(action)
    if req is None:
        # Unknown actions still need the primary tool name present when it matches action.
        if action not in agent.allowed_tools:
            raise PermissionDenied(
                agent.id,
                action,
                f"Agent '{agent.id}' is not allowed to perform action '{action}'.",
            )
        return

    for tool in req.tools:
        if tool not in agent.allowed_tools:
            raise PermissionDenied(
                agent.id,
                tool,
                f"Agent '{agent.id}' missing allowed_tools entry '{tool}' for '{action}'.",
            )

    for scope in req.data_scopes:
        if scope not in agent.permissions.data_scopes:
            raise PermissionDenied(
                agent.id,
                scope,
                f"Agent '{agent.id}' missing data_scope '{scope}' for '{action}'.",
            )

    if req.actions:
        for act in req.actions:
            if act not in agent.permissions.actions:
                raise PermissionDenied(
                    agent.id,
                    act,
                    f"Agent '{agent.id}' missing action '{act}' for '{action}'.",
                )

    if req.actions_any:
        if not any(a in agent.permissions.actions for a in req.actions_any):
            needed = ", ".join(req.actions_any)
            raise PermissionDenied(
                agent.id,
                action,
                f"Agent '{agent.id}' needs one of actions [{needed}] for '{action}'.",
            )

    if require_integrations:
        for integ in req.integrations:
            assert_integration_allowed(agent, integ)


def validate_plan_permissions(
    agents: dict[str, AgentDefinition],
    tasks: list,
) -> Optional[PermissionDenied]:
    """Return first permission error for the plan, or None if valid."""
    for task in tasks:
        agent = agents.get(task.assigned_agent_id)
        if agent is None:
            return PermissionDenied(
                task.assigned_agent_id,
                task.action,
                f"Assigned agent '{task.assigned_agent_id}' not found on team.",
            )
        try:
            # Integrations checked at execution time for LLM/tools that need them;
            # plan validation always checks tools/scopes/actions.
            validate_task_permissions(agent, task, require_integrations=False)
        except PermissionDenied as exc:
            return exc
    return None
