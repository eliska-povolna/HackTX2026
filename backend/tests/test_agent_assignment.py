"""Role assignment, budgets normalization, NO_ACTION customer."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.agent_assignment import AgentAssignmentError, assign_toyota_roles
from app.executor import build_toyota_execution_plan
from app.main import app
from app.models import (
    AgentDefinition,
    AgentPermissions,
    BudgetPolicy,
    TeamDefinition,
    TeamPolicy,
)
from app.planner import build_fallback_team
from app.team_normalize import ensure_team_runtime_config
from app.tools.deterministic import evaluate_mobility_relevance
from app.tools.toyota import load_toyota_customer


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _agent(aid: str, role: str, skills: list[str], tools: list[str], **kwargs) -> AgentDefinition:
    return AgentDefinition(
        id=aid,
        name=aid,
        role=role,
        purpose=role,
        skills=skills,
        allowed_tools=tools,
        permissions=AgentPermissions(
            data_scopes=kwargs.get(
                "scopes",
                ["financial_summary", "toyota_customer_profile", "vehicle_data", "lease_data"],
            ),
            actions=kwargs.get("actions", ["read", "calculate", "synthesize", "recommend"]),
        ),
        budget=kwargs.get("budget")
        or BudgetPolicy(max_llm_cost_usd=0.05, max_llm_calls=2),
        allowed_integrations=kwargs.get("integrations", []),
    )


def test_assign_spreads_llm_style_ids():
    agents = [
        _agent(
            "agent_toyota_context",
            "Toyota Context",
            ["customer_context"],
            ["load_toyota_customer", "evaluate_mobility_relevance"],
        ),
        _agent(
            "agent_financial_profiler",
            "Financial Profiler",
            ["cashflow_analysis", "financial_context"],
            ["nessie_load_customer_financials", "build_financial_context"],
            scopes=["nessie_raw_financial", "financial_summary"],
            integrations=["nessie"],
        ),
        _agent(
            "agent_affordability_risk",
            "Affordability Risk",
            ["affordability", "risk_check"],
            [],
            scopes=["financial_summary"],
            actions=["read", "calculate"],
        ),
        _agent(
            "agent_decision_strategist",
            "Decision Strategist",
            ["recommendation", "tradeoff_synthesis"],
            ["synthesize_recommendation", "run_evaluators"],
            integrations=["meta"],
        ),
        _agent(
            "agent_vehicle_fit",
            "Vehicle Fit",
            ["vehicle_fit", "mobility_options"],
            ["score_mobility_options"],
        ),
    ]
    roles = assign_toyota_roles(agents)
    assert roles["context"] == "agent_toyota_context"
    assert roles["financial"] == "agent_financial_profiler"
    assert roles["decision"] == "agent_decision_strategist"
    assert roles["vehicle"] == "agent_vehicle_fit"
    assert roles.get("affordability") == "agent_affordability_risk"
    assert len({roles["context"], roles["financial"], roles["vehicle"], roles["decision"]}) == 4


def test_assign_fails_when_all_would_collapse():
    # Three agents with no distinguishing signals → collapse detection
    agents = [
        _agent("a1", "Specialist", [], []),
        _agent("a2", "Specialist", [], []),
        _agent("a3", "Specialist", [], []),
    ]
    with pytest.raises(AgentAssignmentError):
        assign_toyota_roles(agents)


def test_plan_uses_distinct_agents_for_llm_style_team():
    team = TeamDefinition(
        id="t1",
        team_name="Toyota Mobility Decision Team",
        summary="test",
        agents=[
            _agent(
                "agent_toyota_context",
                "Context",
                ["customer_context"],
                ["load_toyota_customer", "evaluate_mobility_relevance"],
            ),
            _agent(
                "agent_financial_profiler",
                "Financial",
                ["financial_context"],
                ["nessie_load_customer_financials", "build_financial_context"],
                scopes=["nessie_raw_financial", "financial_summary"],
                integrations=["nessie"],
            ),
            _agent(
                "agent_affordability_risk",
                "Affordability Risk Analyst",
                ["affordability", "risk_check"],
                [],
                scopes=["financial_summary"],
                actions=["read", "calculate"],
            ),
            _agent(
                "agent_vehicle_analyst",
                "Vehicle Analyst",
                ["vehicle_fit", "mobility_options"],
                ["score_mobility_options"],
            ),
            _agent(
                "agent_decision_strategist",
                "Decision Strategist",
                ["recommendation"],
                ["synthesize_recommendation", "run_evaluators"],
                integrations=["meta"],
            ),
        ],
        policy=TeamPolicy(),
    )
    plan = build_toyota_execution_plan(team)
    by_action = {t.action: t.assigned_agent_id for t in plan.tasks}
    assert by_action["load_toyota_customer"] == "agent_toyota_context"
    assert by_action["nessie_load_customer_financials"] == "agent_financial_profiler"
    assert by_action["score_mobility_options"] == "agent_vehicle_analyst"
    assert by_action["synthesize_recommendation"] == "agent_decision_strategist"
    assert by_action["check_affordability_signals"] == "agent_affordability_risk"
    # Not all tasks on financial profiler
    assert len(set(by_action.values())) >= 4


def test_ensure_budgets_always_present():
    team = build_fallback_team("x", 5)
    # Simulate missing policy
    team.policy = None
    fixed = ensure_team_runtime_config(team)
    assert fixed.policy is not None
    assert fixed.policy.max_llm_calls > 0
    for a in fixed.agents:
        assert a.budget.max_llm_calls > 0
        assert a.budget.max_llm_cost_usd > 0


def test_no_action_customer_relevance():
    c = load_toyota_customer("customer-no-action")
    result = evaluate_mobility_relevance(c)
    assert result["relevant"] is False


def _wait_run(client, run_id: str, timeout: float = 60.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/runs/{run_id}").json()
        if body["status"] in {"COMPLETED", "FAILED", "BUDGET_LIMIT", "NO_ACTION_NEEDED"}:
            return body
        time.sleep(0.2)
    raise AssertionError("timeout")


def test_no_action_customer_run(client, monkeypatch):
    monkeypatch.setenv("META_API_KEY", "")
    import app.services.llm_service as llm_mod

    llm_mod._llm_service = None
    team = build_fallback_team("demo", 5).model_dump()
    r = client.post(
        "/api/runs",
        json={
            "goal": "demo",
            "team": team,
            "context": {"customer_id": "customer-no-action"},
            "quality_mode": "normal",
        },
    )
    assert r.status_code == 202
    body = _wait_run(client, r.json()["run_id"])
    assert body["status"] == "NO_ACTION_NEEDED"
    assert body["metrics"]["llm_calls"] == 0
    assert body["recommendation"] is None
