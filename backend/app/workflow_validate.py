"""Plan-time workflow ↔ team capability contract.

Runtime NEVER mutates permissions. Planning may safely align (union) the
minimum tools/scopes/actions/integrations an assigned role needs for the
designed workflow, then validate before the team is returned as runnable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from app.agent_assignment import AgentAssignmentError, TOYOTA_ROLES, assign_toyota_roles
from app.models import AgentDefinition, AgentPermissions, BudgetPolicy, TeamDefinition
from app.permissions import (
    ACTION_REQUIREMENTS,
    PermissionDenied,
    validate_plan_permissions,
    validate_task_permissions,
)
from app.routing import CAPABILITIES, normalize_model_policy


class WorkStepKind(str, Enum):
    """Conceptual work classification — distinct from ExecutorType.

    An AGENT step may still execute via CODE/TOOL/LLM depending on the action.
    Multi-agent must not mean multi-LLM.
    """

    NO_ACTION = "NO_ACTION"
    CODE = "CODE"
    TOOL = "TOOL"
    AGENT = "AGENT"


# Action → work-step kind (judgment vs deterministic vs connector).
WORK_STEP_KINDS: dict[str, WorkStepKind] = {
    "load_toyota_customer": WorkStepKind.TOOL,
    "nessie_load_customer_financials": WorkStepKind.TOOL,
    "evaluate_mobility_relevance": WorkStepKind.CODE,  # may yield NO_ACTION run status
    "build_financial_context": WorkStepKind.CODE,
    "score_mobility_options": WorkStepKind.CODE,
    "check_affordability_signals": WorkStepKind.CODE,
    "run_evaluators": WorkStepKind.CODE,
    "synthesize_recommendation": WorkStepKind.AGENT,
    "review_recommendation": WorkStepKind.AGENT,
}


@dataclass(frozen=True)
class RoleCapabilityGrant:
    """Minimum capabilities a role must hold for the Toyota mobility workflow."""

    allowed_tools: tuple[str, ...] = ()
    data_scopes: tuple[str, ...] = ()
    actions: tuple[str, ...] = ()
    integrations: tuple[str, ...] = ()
    min_llm_calls: int = 1
    min_llm_cost_usd: float = 0.01
    # Policies that can execute AGENT steps assigned to this role.
    llm_policies_ok: tuple[str, ...] = ("auto", "llm_allowed")


# Canonical grants — mirrors build_fallback_team + ACTION_REQUIREMENTS.
ROLE_CAPABILITY_GRANTS: dict[str, RoleCapabilityGrant] = {
    "context": RoleCapabilityGrant(
        allowed_tools=("load_toyota_customer", "evaluate_mobility_relevance"),
        data_scopes=(
            "toyota_customer_profile",
            "vehicle_data",
            "lease_data",
            "usage_data",
        ),
        actions=("read", "calculate"),
        integrations=(),
        llm_policies_ok=("auto", "deterministic_only", "llm_allowed"),
    ),
    "financial": RoleCapabilityGrant(
        allowed_tools=(
            "nessie_load_customer_financials",
            "build_financial_context",
            "nessie_accounts",
            "nessie_purchases",
        ),
        data_scopes=("nessie_raw_financial", "financial_summary"),
        actions=("read", "calculate"),
        integrations=("nessie",),
        llm_policies_ok=("auto", "deterministic_only", "llm_allowed"),
    ),
    "vehicle": RoleCapabilityGrant(
        allowed_tools=("score_mobility_options",),
        data_scopes=(
            "toyota_customer_profile",
            "vehicle_data",
            "lease_data",
            "usage_data",
            "financial_summary",
        ),
        actions=("read", "calculate", "recommend"),
        integrations=(),
        llm_policies_ok=("auto", "deterministic_only", "llm_allowed"),
    ),
    "decision": RoleCapabilityGrant(
        allowed_tools=(
            "synthesize_recommendation",
            "run_evaluators",
            "score_mobility_options",
        ),
        data_scopes=("financial_summary", "vehicle_data", "usage_data", "lease_data"),
        actions=("read", "synthesize", "recommend"),
        integrations=("meta",),
        min_llm_calls=2,
        min_llm_cost_usd=0.05,
        llm_policies_ok=("auto", "llm_allowed"),
    ),
    "affordability": RoleCapabilityGrant(
        allowed_tools=(),
        data_scopes=("financial_summary",),
        actions=("read", "calculate"),
        integrations=(),
        llm_policies_ok=("auto", "deterministic_only", "llm_allowed"),
    ),
    "reviewer": RoleCapabilityGrant(
        allowed_tools=("review_recommendation",),
        data_scopes=("financial_summary",),
        actions=("read", "synthesize"),
        integrations=("meta",),
        llm_policies_ok=("auto", "llm_allowed"),
    ),
}


@dataclass
class WorkflowValidationResult:
    ok: bool
    team: TeamDefinition
    repaired: bool = False
    repairs: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    role_assignment: dict[str, str] = field(default_factory=dict)

    @property
    def status(self) -> str:
        return "READY_TO_RUN" if self.ok else "NEEDS_SETUP"


def work_step_kind(action: str) -> WorkStepKind:
    return WORK_STEP_KINDS.get(action, WorkStepKind.AGENT)


def _union_unique(existing: list[str], required: tuple[str, ...] | list[str]) -> list[str]:
    out = list(existing or [])
    for item in required:
        if item not in out:
            out.append(item)
    return out


def align_agent_for_role(agent: AgentDefinition, role_key: str) -> tuple[AgentDefinition, list[str]]:
    """Union missing workflow capabilities onto the agent (plan-time only)."""
    grant = ROLE_CAPABILITY_GRANTS.get(role_key)
    if grant is None:
        return agent, []

    notes: list[str] = []
    tools = list(agent.allowed_tools or [])
    scopes = list(agent.permissions.data_scopes or [])
    actions = list(agent.permissions.actions or [])
    integrations = list(getattr(agent, "allowed_integrations", None) or [])

    for tool in grant.allowed_tools:
        if tool not in tools:
            tools.append(tool)
            notes.append(f"{agent.id}: +tool {tool} for role {role_key}")
    for scope in grant.data_scopes:
        if scope not in scopes:
            scopes.append(scope)
            notes.append(f"{agent.id}: +scope {scope} for role {role_key}")
    for act in grant.actions:
        if act not in actions:
            actions.append(act)
            notes.append(f"{agent.id}: +action {act} for role {role_key}")
    for integ in grant.integrations:
        if integ not in integrations:
            integrations.append(integ)
            notes.append(f"{agent.id}: +integration {integ} for role {role_key}")

    budget = agent.budget or BudgetPolicy(max_llm_cost_usd=0.05, max_llm_calls=2)
    max_calls = budget.max_llm_calls
    max_cost = budget.max_llm_cost_usd
    if max_calls < grant.min_llm_calls:
        max_calls = grant.min_llm_calls
        notes.append(f"{agent.id}: raised max_llm_calls to {max_calls} for role {role_key}")
    if max_cost < grant.min_llm_cost_usd:
        max_cost = grant.min_llm_cost_usd
        notes.append(f"{agent.id}: raised max_llm_cost_usd to {max_cost} for role {role_key}")

    policy = normalize_model_policy(agent.model_policy)
    # Do not silently change deterministic_only → that is NEEDS_SETUP for AGENT roles.

    updated = agent.model_copy(
        deep=True,
        update={
            "allowed_tools": tools,
            "permissions": AgentPermissions(data_scopes=scopes, actions=actions),
            "allowed_integrations": integrations,
            "budget": BudgetPolicy(max_llm_cost_usd=max_cost, max_llm_calls=max_calls),
            "model_policy": policy,
        },
    )
    return updated, notes


def align_team_capabilities_for_workflow(
    team: TeamDefinition,
    role_assignment: dict[str, str],
) -> tuple[TeamDefinition, list[str]]:
    """Plan-time repair: grant each assigned role its minimum workflow capabilities."""
    by_id = {a.id: a for a in team.agents}
    notes: list[str] = []
    for role_key, agent_id in role_assignment.items():
        agent = by_id.get(agent_id)
        if agent is None:
            continue
        updated, role_notes = align_agent_for_role(agent, role_key)
        by_id[agent_id] = updated
        notes.extend(role_notes)
    repaired = team.model_copy(deep=True, update={"agents": [by_id[a.id] for a in team.agents]})
    return repaired, notes


def _check_model_policy_for_plan(
    agents: dict[str, AgentDefinition],
    tasks: list,
    role_assignment: dict[str, str],
) -> list[str]:
    errors: list[str] = []
    id_to_role = {aid: role for role, aid in role_assignment.items()}
    for task in tasks:
        kind = work_step_kind(task.action)
        if kind != WorkStepKind.AGENT:
            continue
        agent = agents.get(task.assigned_agent_id)
        if agent is None:
            continue
        policy = normalize_model_policy(agent.model_policy)
        role_key = id_to_role.get(agent.id)
        grant = ROLE_CAPABILITY_GRANTS.get(role_key or "")
        ok_policies = grant.llm_policies_ok if grant else ("auto", "llm_allowed")
        if policy not in ok_policies:
            errors.append(
                f"Agent '{agent.id}' model_policy '{policy}' cannot run AGENT step "
                f"'{task.action}' (needs one of {list(ok_policies)})."
            )
        # Capability registry must agree LLM is allowed
        cap = CAPABILITIES.get(task.action)
        if cap is None:
            errors.append(f"No capability registered for action '{task.action}'.")
    return errors


def validate_team_against_workflow(
    team: TeamDefinition,
    *,
    context_source_ids: Optional[list[str]] = None,
    repair: bool = False,
) -> WorkflowValidationResult:
    """
    Assign roles → optional plan-time capability alignment → build plan →
    validate tools/scopes/actions/budgets/model_policy.

    When repair=False (execution path), never mutates the team.
    """
    from app.executor import build_toyota_execution_plan

    errors: list[str] = []
    repairs: list[str] = []
    working = team.model_copy(deep=True)

    try:
        roles = assign_toyota_roles(working.agents)
    except AgentAssignmentError as exc:
        return WorkflowValidationResult(
            ok=False,
            team=working,
            errors=[str(exc)],
        )

    if repair:
        working, repairs = align_team_capabilities_for_workflow(working, roles)

    try:
        plan = build_toyota_execution_plan(working, context_source_ids=context_source_ids)
    except (ValueError, Exception) as exc:
        return WorkflowValidationResult(
            ok=False,
            team=working,
            repaired=bool(repairs),
            repairs=repairs,
            errors=[str(exc)],
            role_assignment=roles,
        )

    agents = {a.id: a for a in working.agents}
    perm_err = validate_plan_permissions(agents, plan.tasks)
    if perm_err is not None:
        errors.append(perm_err.message)

    # Integrations required for TOOL/AGENT steps that declare them
    for task in plan.tasks:
        agent = agents.get(task.assigned_agent_id)
        if agent is None:
            continue
        req = ACTION_REQUIREMENTS.get(task.action)
        if not req or not req.integrations:
            continue
        try:
            validate_task_permissions(agent, task, require_integrations=True)
        except PermissionDenied as exc:
            errors.append(exc.message)

    errors.extend(_check_model_policy_for_plan(agents, plan.tasks, roles))

    # Budget presence (ensure_team_runtime_config should have filled these)
    for role_key, agent_id in roles.items():
        grant = ROLE_CAPABILITY_GRANTS.get(role_key)
        agent = agents.get(agent_id)
        if not grant or not agent or not agent.budget:
            continue
        if agent.budget.max_llm_calls < 1 and work_step_kind("synthesize_recommendation") == WorkStepKind.AGENT:
            if role_key == "decision":
                errors.append(f"Agent '{agent_id}' budget.max_llm_calls must be >= 1 for decision work.")

    return WorkflowValidationResult(
        ok=len(errors) == 0,
        team=working,
        repaired=bool(repairs),
        repairs=repairs,
        errors=errors,
        role_assignment=roles,
    )


def toyota_workflow_applies(goal: str, context_source_ids: Optional[list[str]]) -> bool:
    """Whether the fixed Toyota mobility workflow applies to this plan.

    Planning: only when toyota_customer is explicitly enabled OR the goal is
    clearly mobility-related. Do NOT treat empty context_source_ids as Toyota
    (runtime still defaults builtins on for backward-compatible runs).
    """
    from app.context_sources import TOYOTA_CUSTOMER

    requested = list(context_source_ids or [])
    if TOYOTA_CUSTOMER in requested:
        return True
    blob = (goal or "").lower()
    return any(
        token in blob
        for token in (
            "toyota",
            "mobility",
            "lease buyout",
            "vehicle lease",
            "buyout",
            "customer-stable",
            "customer-stressed",
            "next mobility decision",
        )
    )


def capability_catalog_for_prompt() -> str:
    """Compact role → capability contract for the LLM planner prompt."""
    lines = [
        "When planning a Toyota/mobility team, each role MUST include at least:",
    ]
    for role in TOYOTA_ROLES:
        grant = ROLE_CAPABILITY_GRANTS.get(role.key)
        if not grant:
            continue
        lines.append(
            f"- {role.key}: tools={list(grant.allowed_tools)}; "
            f"scopes={list(grant.data_scopes)}; actions={list(grant.actions)}; "
            f"integrations={list(grant.integrations) or ['(none)']}"
        )
    lines.append(
        "CODE/TOOL steps use deterministic or connector executors; "
        "only synthesize_recommendation / review_recommendation are AGENT (LLM) steps."
    )
    return "\n".join(lines)
