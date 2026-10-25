"""Integrations endpoint + run_store DB flag regression."""

from __future__ import annotations

from contextlib import contextmanager

from fastapi.testclient import TestClient

from app.main import app
from app.models import (
    CreateRunRequest,
    ExecutionPlan,
    ExecutorType,
    RiskLevel,
    RunContext,
    TaskDefinition,
)
from app.planner import build_fallback_team
import app.run_store as run_store


def test_integrations_no_secrets(monkeypatch):
    monkeypatch.setenv("META_API_KEY", "")
    monkeypatch.setenv("NESSIE_API_KEY", "")
    import app.services.llm_service as llm_mod
    import app.tools.nessie as nessie_mod

    llm_mod._llm_service = None
    nessie_mod._nessie = None

    with TestClient(app) as client:
        r = client.get("/api/integrations")
    assert r.status_code == 200
    body = r.json()
    ids = {i["id"]: i for i in body["integrations"]}
    assert ids["llm"]["label"] == "AI Model Provider"
    assert ids["nessie"]["label"] == "Capital One Nessie"
    assert ids["vultr"]["status"] == "HOSTED"
    assert ids["tiger"]["id"] == "tiger"

    blob = str(body).lower()
    for secretish in ("api_key", "password", "postgresql://", "secret", "meta_api"):
        assert secretish not in blob


def test_list_runs_from_memory():
    with TestClient(app) as client:
        team = build_fallback_team("demo", 5).model_dump()
        created = client.post(
            "/api/runs",
            json={
                "goal": "Determine this customer's smartest next mobility decision.",
                "team": team,
                "context": {"customer_id": "customer-stable"},
                "quality_mode": "normal",
            },
        )
        assert created.status_code == 202
        run_id = created.json()["run_id"]

        listed = client.get("/api/runs")
        assert listed.status_code == 200
        runs = listed.json()["runs"]
        assert any(r["run_id"] == run_id for r in runs)
        match = next(r for r in runs if r["run_id"] == run_id)
        assert match["goal"]
        assert match["team_name"]
        assert "status" in match


def test_run_store_sees_db_available_after_init():
    """Regression: `from app.db import db_available` would freeze False forever."""
    import app.db as db_mod

    db_mod._engine = None
    db_mod._SessionLocal = None
    db_mod.db_available = False
    assert run_store.db.db_available is False

    db_mod.db_available = True
    assert run_store.db.db_available is True

    called = {"persist": False}

    @contextmanager
    def fake_session_scope():
        called["persist"] = True
        yield None

    original = run_store.db.session_scope
    run_store.db.session_scope = fake_session_scope  # type: ignore[assignment]
    try:
        team = build_fallback_team("demo", 5)
        plan = ExecutionPlan(
            tasks=[
                TaskDefinition(
                    id="t1",
                    name="n",
                    assigned_agent_id=team.agents[0].id,
                    executor=ExecutorType.CODE,
                    action="x",
                    depends_on=[],
                    risk_level=RiskLevel.low,
                )
            ],
        )
        req = CreateRunRequest(
            goal="g",
            team=team,
            context=RunContext(customer_id="customer-stable"),
        )
        run_store.create_run_record(req, plan)
        assert called["persist"] is True
    finally:
        run_store.db.session_scope = original  # type: ignore[assignment]
        db_mod.db_available = False
