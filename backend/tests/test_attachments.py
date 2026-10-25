"""Attachment upload + planning id validation."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.attachments import clear_all_for_tests
from app.main import app
from app.services.llm_service import _coerce_team_dict


def setup_function() -> None:
    clear_all_for_tests()


def test_upload_list_get_delete_attachment():
    with TestClient(app) as client:
        files = {"file": ("brief.txt", b"Customer wants lower monthly cost.", "text/plain")}
        r = client.post("/api/attachments", files=files)
        assert r.status_code == 201
        meta = r.json()
        assert meta["filename"] == "brief.txt"
        assert meta["has_text_excerpt"] is True
        aid = meta["id"]

        listed = client.get("/api/attachments")
        assert listed.status_code == 200
        assert any(a["id"] == aid for a in listed.json()["attachments"])

        got = client.get(f"/api/attachments/{aid}")
        assert got.status_code == 200
        assert got.json()["id"] == aid

        deleted = client.delete(f"/api/attachments/{aid}")
        assert deleted.status_code == 204
        assert client.get(f"/api/attachments/{aid}").status_code == 404


def test_plan_rejects_unknown_attachment_id(monkeypatch):
    monkeypatch.setenv("META_API_KEY", "")
    import app.services.llm_service as llm_mod

    llm_mod._llm_service = None
    with TestClient(app) as client:
        r = client.post(
            "/api/teams/plan",
            json={
                "goal": "x",
                "max_team_size": 3,
                "budget_preference": "balanced",
                "attachment_ids": ["att-missing"],
            },
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "INVALID_REQUEST"


def test_upload_rejects_oversized():
    with TestClient(app) as client:
        big = b"x" * (2 * 1024 * 1024 + 10)
        r = client.post("/api/attachments", files={"file": ("big.bin", big, "application/octet-stream")})
        assert r.status_code == 400


def test_coerce_team_dict_fills_budget_and_permissions():
    raw = {
        "team_name": "Crew",
        "summary": "s",
        "agents": [
            {
                "name": "A",
                "role": "Scout",
                "purpose": "Look around",
                "skills": "research",
            }
        ],
    }
    fixed = _coerce_team_dict(raw, max_team_size=5)
    agent = fixed["agents"][0]
    assert agent["id"]
    assert agent["skills"] == ["research"]
    assert "max_llm_cost_usd" in agent["budget"]
    assert "data_scopes" in agent["permissions"]
