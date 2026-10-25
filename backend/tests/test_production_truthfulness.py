"""Production must not silently substitute fixtures / fake LLM success."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import (
    DataSource,
    ExecutionPlan,
    RunListItem,
    RunMetrics,
    RunStatus,
    RunStatusResponse,
)
from app.tools.nessie import NessieClient
import app.run_store as run_store


def test_production_nessie_raises_without_silent_fixture(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("NESSIE_FALLBACK_ENABLED", "false")
    client = NessieClient()
    client.api_key = ""
    client.clear_run_cache()
    with pytest.raises(Exception):
        client.load_customer_financials("b8470c8a-6fde-495f-b1f1-792c771b0709")


def test_dev_nessie_may_use_fixture(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("NESSIE_FALLBACK_ENABLED", "true")
    client = NessieClient()
    client.api_key = ""
    client.clear_run_cache()
    data, source = client.load_customer_financials("nessie-demo-001")
    assert source == DataSource.NESSIE_FALLBACK
    assert data["accounts"]


def test_plan_team_errors_when_unconfigured_in_any_mode(monkeypatch):
    monkeypatch.setenv("META_API_KEY", "")
    monkeypatch.setenv("APP_ENV", "production")
    import app.services.llm_service as llm_mod

    llm_mod._llm_service = None
    with TestClient(app) as client:
        r = client.post(
            "/api/teams/plan",
            json={
                "goal": "Recommend the next mobility decision for one Toyota customer",
                "max_team_size": 3,
                "budget_preference": "balanced",
            },
        )
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "LLM_UNAVAILABLE"


def test_list_runs_summary_fields_and_dedupe(monkeypatch):
    run = RunStatusResponse(
        run_id="run-mem1",
        status=RunStatus.COMPLETED,
        goal="g",
        created_at="2026-10-25T10:00:00Z",
        updated_at="2026-10-25T10:01:00Z",
        plan=ExecutionPlan(tasks=[]),
        tasks=[],
        metrics=RunMetrics(llm_calls=2, llm_calls_avoided=5, estimated_ai_cost_usd=0.01),
        recommendation=None,
    )
    run_store._runs["run-mem1"] = run
    run_store._run_team_names["run-mem1"] = "Mem Team"

    def fake_db_list():
        return [
            RunListItem(
                run_id="run-mem1",
                goal="old",
                team_name="DB Team",
                status=RunStatus.COMPLETED,
                result="BUY_CURRENT_VEHICLE",
                date="2026-10-25T09:00:00Z",
                duration_ms=1000,
                estimated_ai_cost_usd=0.02,
                llm_calls=9,
                llm_calls_avoided=1,
            ),
            RunListItem(
                run_id="run-db1",
                goal="persisted",
                team_name="Persisted Team",
                status=RunStatus.COMPLETED,
                result="RETURN_VEHICLE",
                date="2026-10-25T08:00:00Z",
                duration_ms=2000,
                estimated_ai_cost_usd=0.03,
                llm_calls=1,
                llm_calls_avoided=4,
            ),
        ]

    monkeypatch.setattr(run_store, "_list_runs_from_db", fake_db_list)
    try:
        items = run_store.list_runs()
        by_id = {i.run_id: i for i in items}
        assert by_id["run-mem1"].team_name == "Mem Team"
        assert by_id["run-mem1"].llm_calls == 2
        assert by_id["run-db1"].result == "RETURN_VEHICLE"
        assert by_id["run-db1"].date == "2026-10-25T08:00:00Z"
        assert by_id["run-db1"].llm_calls_avoided == 4
    finally:
        run_store._runs.pop("run-mem1", None)
        run_store._run_team_names.pop("run-mem1", None)


def test_audit_events_endpoint_empty_ok():
    with TestClient(app) as client:
        r = client.get("/api/audit-events?limit=10")
    assert r.status_code == 200
    assert "events" in r.json()
    assert isinstance(r.json()["events"], list)
