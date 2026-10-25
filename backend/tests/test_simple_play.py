"""Connector-free simple_play workflow (e.g. rock-paper-scissors)."""

from unittest.mock import AsyncMock
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.main import app
from app.models import AgentDefinition, AgentPermissions, BudgetPolicy, TeamDefinition, TeamPolicy
from app.simple_play import align_simple_play_team, build_simple_play_plan


RPS = "Assemble agents that play one round of rock-paper-scissors against each other and declare a winner."


def test_rps_goal_classifies_as_simple_play():
    from app.job_analysis import analyze_job

    analysis = analyze_job(RPS)
    assert analysis.execution_status.value == "READY"
    assert analysis.workflow_kind.value == "simple_play"


def test_recruiting_still_needs_setup():
    from app.job_analysis import analyze_job

    analysis = analyze_job(
        "Build a recruiting team that finds candidates, emails them and schedules interviews."
    )
    assert analysis.execution_status.value == "NEEDS_SETUP"
    assert "ATS" in analysis.missing_capabilities


def test_simple_play_plan_and_run(monkeypatch):
    monkeypatch.setenv("META_API_KEY", "")
    import app.services.llm_service as service

    service._llm_service = None
    team = TeamDefinition(
        id="play-team",
        team_name="Play Party",
        summary="RPS",
        agents=[
            AgentDefinition(
                id="host",
                name="Host",
                role="Game Host",
                purpose="Host a short game",
                skills=["games"],
                allowed_tools=[],
                permissions=AgentPermissions(data_scopes=[], actions=[]),
                budget=BudgetPolicy(max_llm_calls=2, max_llm_cost_usd=0.05),
            )
        ],
        policy=TeamPolicy(work_style="fast"),
    )
    planner = SimpleNamespace(plan_team=AsyncMock(return_value=team))
    monkeypatch.setattr("app.planner.get_llm_service", lambda: planner)

    with TestClient(app) as client:
        planned = client.post("/api/teams/plan", json={"goal": RPS, "max_team_size": 3})
        assert planned.status_code == 200, planned.text
        body = planned.json()
        assert body["team"]["policy"]["workflow_kind"] == "simple_play"
        assert any("simple_play" in (a.get("allowed_tools") or []) for a in body["team"]["agents"])

        started = client.post(
            "/api/runs",
            json={
                "goal": RPS,
                "team": body["team"],
                "context": {"customer_id": ""},
                "quality_mode": "normal",
            },
        )
        assert started.status_code == 202, started.text
        result = client.get("/api/runs/" + started.json()["run_id"]).json()
    assert result["status"] == "COMPLETED"
    assert result["metrics"]["llm_calls"] >= 0
    assert any(t["action"] == "simple_play" for t in result["tasks"])
    assert result["tasks"][0]["status"] == "COMPLETED"


def test_build_simple_play_plan_routes_to_llm():
    team = align_simple_play_team(
        TeamDefinition(
            id="t",
            team_name="t",
            summary="",
            agents=[],
            policy=TeamPolicy(),
        )
    )
    plan = build_simple_play_plan(team)
    assert len(plan.tasks) == 1
    assert plan.tasks[0].action == "simple_play"
    assert plan.tasks[0].executor.value == "LLM"
