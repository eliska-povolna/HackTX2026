"""Test defaults: do not hang on unreachable Tiger Cloud from local/CI."""

from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def _disable_remote_db_for_tests():
    """Unit/integration tests use in-memory run state unless a test opts in."""
    saved = {
        "TIMESCALE_SERVICE_URL": os.environ.pop("TIMESCALE_SERVICE_URL", None),
        "DATABASE_URL": os.environ.pop("DATABASE_URL", None),
    }
    yield
    for key, value in saved.items():
        if value is not None:
            os.environ[key] = value
