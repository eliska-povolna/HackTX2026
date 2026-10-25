"""Build runtime LLM system prompts from AgentDefinition + TeamPolicy.

Never inject secrets, API keys, DB URLs, or raw Nessie rows.
"""

from __future__ import annotations

from typing import Optional

from app.models import AgentDefinition, TeamPolicy


def build_agent_system_prompt(
    agent: AgentDefinition,
    team_policy: Optional[TeamPolicy],
    *,
    task_instruction: str,
    response_format: str = "",
) -> str:
    """Assemble a structured system prompt from real agent/team configuration."""
    policy = team_policy or TeamPolicy()
    team_rails = list(policy.guardrails or [])
    agent_rails = list(agent.guardrails or [])
    skills = list(agent.skills or [])
    scopes = list(agent.permissions.data_scopes or [])
    actions = list(agent.permissions.actions or [])
    tools = list(agent.allowed_tools or [])
    integrations = list(getattr(agent, "allowed_integrations", None) or [])

    lines: list[str] = [
        f"You are {agent.name}.",
        "",
        "ROLE",
        agent.role or "(unspecified)",
        "",
        "PURPOSE",
        agent.purpose or "(unspecified)",
        "",
        "SKILLS",
    ]
    if skills:
        lines.extend(f"- {s}" for s in skills)
    else:
        lines.append("- (none listed)")

    lines.extend(["", "INSTRUCTIONS", agent.instructions.strip() or "(none)"])

    lines.extend(["", "ALLOWED DATA"])
    if scopes:
        lines.extend(f"- {s}" for s in scopes)
    else:
        lines.append("- (none)")

    lines.extend(["", "ALLOWED ACTIONS"])
    if actions:
        lines.extend(f"- {a}" for a in actions)
    else:
        lines.append("- (none)")

    lines.extend(["", "ALLOWED TOOLS (names only — no credentials)"])
    if tools:
        lines.extend(f"- {t}" for t in tools)
    else:
        lines.append("- (none)")

    if integrations:
        lines.extend(["", "ALLOWED INTEGRATIONS (named refs only — secrets stay server-side)"])
        lines.extend(f"- {i}" for i in integrations)

    lines.extend(["", "GUARDRAILS", "TEAM:"])
    if team_rails:
        lines.extend(f"- {g}" for g in team_rails)
    else:
        lines.append("- (none)")
    lines.append("AGENT:")
    if agent_rails:
        lines.extend(f"- {g}" for g in agent_rails)
    else:
        lines.append("- (none)")

    lines.extend(
        [
            "",
            "HARD CONSTRAINTS",
            "- Never request or invent raw bank transactions or Nessie purchase rows.",
            "- Never ask for or emit API keys, passwords, or database credentials.",
            "- Never execute external financial transactions.",
            "- Use only the approved derived context supplied in the user message.",
            "",
            "CURRENT TASK",
            task_instruction.strip(),
        ]
    )
    if response_format.strip():
        lines.extend(["", "RESPONSE FORMAT", response_format.strip()])

    prompt = "\n".join(lines)
    # Belt-and-suspenders: never allow common secret markers if misconfigured upstream.
    banned = ("META_API_KEY", "NESSIE_API_KEY", "DATABASE_URL", "TIMESCALE_SERVICE_URL")
    for token in banned:
        if token in prompt:
            prompt = prompt.replace(token, "[REDACTED]")
    return prompt
