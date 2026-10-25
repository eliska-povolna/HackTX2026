"""Deterministic ExecutionRouter — CODE → TOOL → LLM. Never uses an LLM to choose."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.models import AgentDefinition, ExecutorType


class RoutingError(ValueError):
    """No valid executor for this action under the current policy."""


@dataclass(frozen=True)
class Capability:
    executor: ExecutorType
    reason: str
    requires_external_data: bool = False
    deterministic: bool = False
    requires_reasoning: bool = False


# Capability registry: preferred executor for each known action.
CAPABILITIES: dict[str, Capability] = {
    "load_toyota_customer": Capability(
        executor=ExecutorType.TOOL,
        reason="Approved external connector required",
        requires_external_data=True,
    ),
    "nessie_load_customer_financials": Capability(
        executor=ExecutorType.TOOL,
        reason="Approved external connector required",
        requires_external_data=True,
    ),
    "evaluate_mobility_relevance": Capability(
        executor=ExecutorType.CODE,
        reason="Deterministic implementation available",
        deterministic=True,
    ),
    "build_financial_context": Capability(
        executor=ExecutorType.CODE,
        reason="Deterministic implementation available",
        deterministic=True,
    ),
    "score_mobility_options": Capability(
        executor=ExecutorType.CODE,
        reason="Deterministic implementation available",
        deterministic=True,
    ),
    "synthesize_recommendation": Capability(
        executor=ExecutorType.LLM,
        reason="Natural-language synthesis required",
        requires_reasoning=True,
    ),
    "run_evaluators": Capability(
        executor=ExecutorType.CODE,
        reason="Deterministic implementation available",
        deterministic=True,
    ),
    "check_affordability_signals": Capability(
        executor=ExecutorType.CODE,
        reason="Deterministic implementation available",
        deterministic=True,
    ),
    "review_recommendation": Capability(
        executor=ExecutorType.LLM,
        reason="Natural-language synthesis required",
        requires_reasoning=True,
    ),
}


@dataclass(frozen=True)
class RouteDecision:
    executor: ExecutorType
    reason: str
    action: str


def normalize_model_policy(policy: Optional[str]) -> str:
    raw = (policy or "auto").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "": "auto",
        "auto": "auto",
        "default": "auto",
        "deterministic": "deterministic_only",
        "deterministic_only": "deterministic_only",
        "code_only": "deterministic_only",
        "llm": "llm_allowed",
        "llm_allowed": "llm_allowed",
        "llm_ok": "llm_allowed",
    }
    return aliases.get(raw, "auto" if raw not in {"auto", "deterministic_only", "llm_allowed"} else raw)


def route(action: str, agent: Optional[AgentDefinition] = None) -> RouteDecision:
    """
    Cheap-safe-adequate routing:
    1. Approved deterministic CODE when registered as CODE
    2. Approved TOOL when external data/action required
    3. LLM only when capability requires reasoning and model_policy allows
    4. Otherwise fail clearly — never invent an executor
    """
    cap = CAPABILITIES.get(action)
    if cap is None:
        raise RoutingError(f"Unsupported action '{action}': no registered capability")

    policy = normalize_model_policy(agent.model_policy if agent else "auto")

    if cap.executor == ExecutorType.LLM:
        if policy == "deterministic_only":
            raise RoutingError(
                f"Action '{action}' requires LLM but agent model_policy is deterministic_only"
            )
        # auto and llm_allowed both permit LLM when the capability says so
        return RouteDecision(executor=ExecutorType.LLM, reason=cap.reason, action=action)

    if cap.executor == ExecutorType.CODE:
        return RouteDecision(executor=ExecutorType.CODE, reason=cap.reason, action=action)

    if cap.executor == ExecutorType.TOOL:
        return RouteDecision(executor=ExecutorType.TOOL, reason=cap.reason, action=action)

    raise RoutingError(f"No valid executor for action '{action}'")
