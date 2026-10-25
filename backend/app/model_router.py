"""Deterministic ModelRouter — FAST vs STRONG. Never uses an LLM to choose."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from app.services.llm_service import get_llm_service

ModelTier = Literal["FAST", "STRONG"]
TaskKind = Literal["planning", "synthesis", "review"]


class ModelRoutingError(ValueError):
    """Requested model tier is not configured."""


@dataclass(frozen=True)
class ModelDecision:
    tier: ModelTier
    model: str
    reason: str


def normalize_work_style(style: Optional[str]) -> str:
    raw = (style or "balanced").strip().lower()
    if raw in {"fast", "balanced", "thorough"}:
        return raw
    aliases = {
        "quick": "fast",
        "cheap": "fast",
        "default": "balanced",
        "normal": "balanced",
        "strict": "thorough",
        "deep": "thorough",
    }
    return aliases.get(raw, "balanced")


def choose_model(
    *,
    task_kind: TaskKind,
    work_style: str = "balanced",
    risk: str = "medium",
) -> ModelDecision:
    """
    Select FAST or STRONG from configured env models.

    - planning: fast → FAST; balanced/thorough → STRONG
    - synthesis: fast/balanced → FAST; thorough or high risk → STRONG
    - review: always STRONG
    """
    style = normalize_work_style(work_style)
    risk_l = (risk or "medium").strip().lower()
    high_risk = risk_l == "high"

    if task_kind == "review":
        tier: ModelTier = "STRONG"
        reason = "High-risk / policy review requires the strong model tier"
    elif task_kind == "planning":
        if style == "fast":
            tier = "FAST"
            reason = "Fast work style: team planning uses the fast model tier"
        else:
            tier = "STRONG"
            reason = "Balanced/thorough planning uses the strong model tier"
    elif task_kind == "synthesis":
        if style == "thorough" or high_risk:
            tier = "STRONG"
            reason = (
                "Thorough work style or high risk: synthesis uses the strong model tier"
            )
        else:
            tier = "FAST"
            reason = (
                "Deterministic scoring is complete; only customer-facing synthesis "
                "is required — fast/balanced uses the fast model tier"
            )
    else:
        raise ModelRoutingError(f"Unknown task_kind: {task_kind}")

    model = _resolve_configured_model(tier)
    return ModelDecision(tier=tier, model=model, reason=reason)


def _resolve_configured_model(tier: ModelTier) -> str:
    llm = get_llm_service()
    if tier == "FAST":
        model = (llm.fast_model or "").strip()
        if not model:
            raise ModelRoutingError(
                "FAST model tier requested but LLM_FAST_MODEL is not configured"
            )
        return model
    model = (llm.strong_model or "").strip()
    if not model:
        raise ModelRoutingError(
            "STRONG model tier requested but LLM_STRONG_MODEL is not configured"
        )
    return model
