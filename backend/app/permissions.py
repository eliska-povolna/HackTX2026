"""Backend-enforced tool and data-scope permissions."""

from __future__ import annotations

from app.models import AgentDefinition, PermissionDecision


class PermissionDenied(Exception):
    def __init__(self, agent_id: str, tool_name: str, message: str):
        self.agent_id = agent_id
        self.tool_name = tool_name
        self.message = message
        super().__init__(message)


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
