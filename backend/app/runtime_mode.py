"""Environment-aware truthfulness switches for production vs test/dev."""

from __future__ import annotations

import os


def app_env() -> str:
    return (os.getenv("APP_ENV") or "development").strip().lower()


def is_production() -> bool:
    return app_env() in {"production", "prod", "live"}


def _flag(name: str) -> str | None:
    raw = os.getenv(name)
    if raw is None:
        return None
    raw = raw.strip()
    return raw if raw else None


def _truthy(raw: str) -> bool:
    return raw.lower() in {"1", "true", "yes", "on"}


def nessie_fallback_enabled() -> bool:
    """Fixtures only when explicitly enabled, or by default outside production."""
    raw = _flag("NESSIE_FALLBACK_ENABLED")
    if raw is not None:
        return _truthy(raw)
    return not is_production()


def llm_deterministic_synthesis_allowed() -> bool:
    """Allow CODE seed recommendation only in explicit non-production / test modes."""
    raw = _flag("LLM_ALLOW_DETERMINISTIC_SYNTHESIS")
    if raw is not None:
        return _truthy(raw)
    return not is_production()
