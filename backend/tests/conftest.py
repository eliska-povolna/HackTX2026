"""Test defaults: in-memory DB, fixtures allowed, deterministic synthesis allowed."""

from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def _test_runtime_defaults():
    """Unit/integration tests use in-memory run state + explicit fixture paths."""
    saved = {
        "TIMESCALE_SERVICE_URL": os.environ.pop("TIMESCALE_SERVICE_URL", None),
        "DATABASE_URL": os.environ.pop("DATABASE_URL", None),
        "APP_ENV": os.environ.get("APP_ENV"),
        "LLM_FAST_MODEL": os.environ.get("LLM_FAST_MODEL"),
        "LLM_STRONG_MODEL": os.environ.get("LLM_STRONG_MODEL"),
        "NESSIE_FALLBACK_ENABLED": os.environ.get("NESSIE_FALLBACK_ENABLED"),
        "LLM_ALLOW_DETERMINISTIC_SYNTHESIS": os.environ.get(
            "LLM_ALLOW_DETERMINISTIC_SYNTHESIS"
        ),
    }
    os.environ["APP_ENV"] = "test"
    os.environ["LLM_FAST_MODEL"] = "test-fast"
    os.environ["LLM_STRONG_MODEL"] = "test-strong"
    os.environ["NESSIE_FALLBACK_ENABLED"] = "true"
    os.environ["LLM_ALLOW_DETERMINISTIC_SYNTHESIS"] = "true"
    yield
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
