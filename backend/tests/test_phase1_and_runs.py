"""Phase 1 regression + Phase 2 full run."""

import os
import time

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.planner import build_fallback_team


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_templates(client):
    r = client.get("/api/templates")
    assert r.status_code == 200
    ids = [t["id"] for t in r.json()["templates"]]
    assert "toyota-mobility" in ids


def test_demo_customers(client):
    r = client.get("/api/demo/customers")
    assert r.status_code == 200
    assert len(r.json()["customers"]) == 2


def test_plan_team_errors_when_llm_unavailable(client, monkeypatch):
    monkeypatch.setenv("META_API_KEY", "")
    # Reset LLM singleton
    import app.services.llm_service as llm_mod

    llm_mod._llm_service = None
    r = client.post(
        "/api/teams/plan",
        json={"goal": "Toyota next move", "max_team_size": 5, "budget_preference": "balanced"},
    )
    assert r.status_code == 503
    body = r.json()
    assert body["error"]["code"] == "LLM_UNAVAILABLE"
    assert body["error"]["recoverable"] is True


def test_database_init_without_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("TIMESCALE_SERVICE_URL", raising=False)
    from app import db as db_mod

    db_mod._engine = None
    db_mod._SessionLocal = None
    db_mod.db_available = False
    ok = db_mod.init_db()
    assert ok is False
    assert db_mod.db_available is False


def _wait_run(client, run_id: str, timeout: float = 60.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/api/runs/{run_id}")
        assert r.status_code == 200
        body = r.json()
        if body["status"] in {"COMPLETED", "FAILED", "BUDGET_LIMIT"}:
            return body
        time.sleep(0.2)
    raise AssertionError("Run did not finish in time")


def test_full_run_stable_vs_stressed(client, monkeypatch):
    monkeypatch.setenv("META_API_KEY", "")
    import app.services.llm_service as llm_mod

    llm_mod._llm_service = None

    team = build_fallback_team("demo", 5).model_dump()

    def start(customer_id: str):
        r = client.post(
            "/api/runs",
            json={
                "goal": "Determine this customer's smartest next mobility decision.",
                "team": team,
                "context": {"customer_id": customer_id},
                "quality_mode": "normal",
            },
        )
        assert r.status_code == 202
        return r.json()["run_id"]

    run_a = _wait_run(client, start("customer-stable"))
    run_b = _wait_run(client, start("customer-stressed"))

    assert run_a["status"] == "COMPLETED"
    assert run_b["status"] == "COMPLETED"
    assert run_a["financial_context"]["financial_stress"] == "LOW"
    assert run_b["financial_context"]["financial_stress"] == "HIGH"
    assert run_a["recommendation"]["recommended_action"] == "BUY_CURRENT_VEHICLE"
    assert run_b["recommendation"]["recommended_action"] != run_a["recommendation"]["recommended_action"]

    # No raw Nessie in recommendation payload
    rec_text = str(run_a["recommendation"]).lower()
    assert "purchases" not in rec_text
    assert "merchant_id" not in rec_text

    # Audit contains permission denial demo + tool/code style events.
    # In test mode without META key, synthesis uses CODE_FALLBACK (not fake LLM_CALL).
    events = {e["event_type"] for e in run_a["audit_events"]}
    assert "PERMISSION_CHECK" in events
    assert "TOOL_CALL" in events
    assert "DATA_MINIMIZATION" in events
    assert "CODE_FALLBACK" in events or "LLM_CALL" in events
    assert any(e["result"] == "DENIED" for e in run_a["audit_events"])
    # Without a real META key, successful LLM_CALL must not be faked.
    if not (os.getenv("META_API_KEY") or "").strip():
        assert "CODE_FALLBACK" in events
        assert "LLM_CALL" not in events

    assert run_a["metrics"]["code_tasks"] >= 2
    assert run_a["metrics"]["tool_tasks"] >= 2
    assert run_a["evaluators"]
