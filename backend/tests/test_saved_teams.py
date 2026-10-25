"""Saved teams CRUD (Tiger or in-memory)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.planner import build_fallback_team
from app.team_store import clear_memory_for_tests


def setup_function() -> None:
    clear_memory_for_tests()


def test_save_list_get_delete_team():
    team = build_fallback_team("demo goal", 3)
    with TestClient(app) as client:
        created = client.post(
            "/api/teams",
            json={"team": team.model_dump(), "goal": "demo goal"},
        )
        assert created.status_code == 201
        body = created.json()
        assert body["id"] == team.id
        assert body["name"] == team.team_name
        assert body["member_ids"]
        assert body["last_run_status"] == "NEVER"

        listed = client.get("/api/teams")
        assert listed.status_code == 200
        assert any(t["id"] == team.id for t in listed.json()["teams"])

        detail = client.get(f"/api/teams/{team.id}")
        assert detail.status_code == 200
        assert detail.json()["team"]["id"] == team.id
        assert len(detail.json()["team"]["agents"]) == 3

        deleted = client.delete(f"/api/teams/{team.id}")
        assert deleted.status_code == 204
        assert client.get(f"/api/teams/{team.id}").status_code == 404


def test_save_team_requires_agents():
    team = build_fallback_team("x", 2).model_dump()
    team["agents"] = []
    with TestClient(app) as client:
        r = client.post("/api/teams", json={"team": team, "goal": "x"})
    assert r.status_code == 400
