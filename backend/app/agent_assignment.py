"""Assign Toyota plan roles to team agents by id / role / skills / tools.

Never silently collapse every task onto the first agent in the list.
Skills never grant permissions — they only influence matching.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.models import AgentDefinition


class AgentAssignmentError(ValueError):
    """No suitable agent for a required plan role."""


@dataclass(frozen=True)
class RoleSpec:
    key: str
    preferred_ids: tuple[str, ...]
    keywords: tuple[str, ...]  # matched against id, role, purpose, name
    skills: tuple[str, ...]
    tools: tuple[str, ...]  # bonus if present in allowed_tools
    required: bool = True


TOYOTA_ROLES: tuple[RoleSpec, ...] = (
    RoleSpec(
        key="context",
        preferred_ids=("context-agent", "agent_toyota_context", "agent-context"),
        keywords=("context", "toyota", "customer", "signal", "profile"),
        skills=("customer_context", "signal_detection", "data_scoping"),
        tools=("load_toyota_customer", "evaluate_mobility_relevance"),
    ),
    RoleSpec(
        key="financial",
        preferred_ids=("financial-agent", "agent_financial_profiler", "agent-financial"),
        keywords=("financial", "finance", "nessie", "cashflow", "banking", "profiler"),
        skills=("cashflow_analysis", "financial_context", "affordability"),
        tools=(
            "nessie_load_customer_financials",
            "build_financial_context",
            "nessie_accounts",
        ),
    ),
    RoleSpec(
        key="vehicle",
        preferred_ids=("vehicle-agent", "agent_vehicle", "agent-mobility"),
        keywords=("vehicle", "mobility", "lease", "usage", "ownership"),
        skills=("vehicle_fit", "mobility_options", "usage_analysis", "lease_evaluation"),
        tools=("score_mobility_options",),
    ),
    RoleSpec(
        key="decision",
        preferred_ids=("decision-agent", "agent_decision_strategist", "agent-decision"),
        keywords=("decision", "strategist", "recommend", "synthesis", "guidance"),
        skills=("recommendation", "tradeoff_synthesis", "customer_guidance"),
        tools=("synthesize_recommendation", "run_evaluators"),
    ),
    RoleSpec(
        key="affordability",
        preferred_ids=("affordability-agent", "agent_affordability_risk", "agent-risk"),
        keywords=("affordability", "risk", "runway", "stress", "guard"),
        skills=("affordability", "risk_check", "budget_compliance", "financial_stress"),
        tools=("build_financial_context", "calculate_affordability", "check_affordability_signals"),
        required=False,
    ),
    RoleSpec(
        key="reviewer",
        preferred_ids=("reviewer-agent", "agent_reviewer", "agent-review"),
        keywords=("review", "qa", "compliance", "policy"),
        skills=("policy_review", "risk_check", "budget_compliance"),
        tools=("review_recommendation",),
        required=False,
    ),
)


def _blob(agent: AgentDefinition) -> str:
    return " ".join(
        [
            agent.id,
            agent.name,
            agent.role,
            agent.purpose,
            " ".join(agent.skills or []),
            " ".join(agent.allowed_tools or []),
        ]
    ).lower()


def score_agent_for_role(agent: AgentDefinition, role: RoleSpec) -> int:
    score = 0
    if agent.id in role.preferred_ids:
        score += 100
    blob = _blob(agent)
    keyword_hits = 0
    for kw in role.keywords:
        if kw in blob:
            score += 12
            keyword_hits += 1
    have_skills = {s.lower() for s in (agent.skills or [])}
    skill_hits = 0
    for sk in role.skills:
        if sk.lower() in have_skills:
            score += 20
            skill_hits += 1
    tools = set(agent.allowed_tools or [])
    for tool in role.tools:
        if tool in tools:
            score += 25
    # Do not treat a generic Reviewer as the affordability specialist.
    if role.key == "affordability":
        if "review" in agent.id.lower() or "reviewer" in agent.role.lower():
            if "afford" not in blob and "stress" not in blob:
                return 0
        if keyword_hits == 0 and skill_hits == 0 and agent.id not in role.preferred_ids:
            return 0
    return score


def assign_toyota_roles(
    agents: list[AgentDefinition],
) -> dict[str, str]:
    """
    Return mapping role_key → agent_id.

    Required roles must get a positive-scoring agent. Agents are not reused for
    required roles when another unused agent scores > 0 for that role.
    Optional roles are filled only when a dedicated unused agent scores > 0.
    """
    if not agents:
        raise AgentAssignmentError("Team has no agents to assign.")

    by_id = {a.id: a for a in agents}
    remaining = set(by_id.keys())
    assignment: dict[str, str] = {}

    # Assign required roles first (highest max-score role order for stability).
    required = [r for r in TOYOTA_ROLES if r.required]
    optional = [r for r in TOYOTA_ROLES if not r.required]

    def _best(role: RoleSpec, pool: set[str]) -> tuple[Optional[str], int]:
        best_id: Optional[str] = None
        best_score = -1
        for aid in pool:
            s = score_agent_for_role(by_id[aid], role)
            if s > best_score:
                best_score = s
                best_id = aid
        return best_id, best_score

    for role in required:
        aid, sc = _best(role, remaining)
        if aid is None or sc <= 0:
            # Last resort: allow reuse only if literally one agent on the team
            if len(by_id) == 1:
                only = next(iter(by_id))
                assignment[role.key] = only
                continue
            # Try full pool (reuse) only if someone scores — still fail if zero
            aid2, sc2 = _best(role, set(by_id.keys()))
            if aid2 is None or sc2 <= 0:
                raise AgentAssignmentError(
                    f"No agent matches required role '{role.key}'. "
                    f"Team agent ids: {sorted(by_id.keys())}. "
                    "Rename roles/ids or grant matching tools/skills "
                    f"(expected tools like {list(role.tools)[:2]})."
                )
            # Reuse is allowed only when no unused agent fits — still prefer unused
            assignment[role.key] = aid2
        else:
            assignment[role.key] = aid
            remaining.discard(aid)

    for role in optional:
        if not remaining:
            break
        aid, sc = _best(role, remaining)
        if aid is not None and sc > 0:
            assignment[role.key] = aid
            remaining.discard(aid)

    # Detect silent collapse: multiple agents but all required roles same person
    required_ids = {assignment[r.key] for r in required if r.key in assignment}
    if len(by_id) >= 3 and len(required_ids) == 1:
        raise AgentAssignmentError(
            "All required Toyota tasks would collapse onto a single agent "
            f"('{next(iter(required_ids))}'), but the team has {len(by_id)} agents. "
            "Use distinct context / financial / vehicle / decision roles "
            "(ids, role names, skills, or allowed_tools)."
        )

    return assignment
