"""ExecutionRouter, agent budgets, relevance gate, and mobility trigger API."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.cost_optimizer import RunCostTracker
from app.main import app
from app.models import (
    AgentDefinition,
    AgentPermissions,
    BudgetPolicy,
    ExecutorType,
)
from app.planner import build_fallback_team
from app.routing import RoutingError, route
from app.tools.deterministic import evaluate_mobility_relevance
from app.tools.toyota import load_toyota_customer


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _agent(*, model_policy: str = "auto", max_calls: int = 2, max_cost: float = 0.05) -> AgentDefinition:
    return AgentDefinition(
        id="decision-agent",
        name="Dana",
        role="Decision Specialist",
        purpose="test",
        skills=[],
        allowed_tools=["synthesize_recommendation"],
        permissions=AgentPermissions(data_scopes=["financial_summary"], actions=["read"]),
        budget=BudgetPolicy(max_llm_cost_usd=max_cost, max_llm_calls=max_calls),
        model_policy=model_policy,
    )


def test_router_toyota_actions():
    agent = _agent()
    assert route("build_financial_context", agent).executor == ExecutorType.CODE
    assert route("score_mobility_options", agent).executor == ExecutorType.CODE
    assert route("nessie_load_customer_financials", agent).executor == ExecutorType.TOOL
    assert route("synthesize_recommendation", agent).executor == ExecutorType.LLM
    assert route("evaluate_mobility_relevance", agent).executor == ExecutorType.CODE


def test_router_unknown_action_fails():
    with pytest.raises(RoutingError, match="Unsupported action"):
        route("invent_something", _agent())


def test_router_deterministic_only_blocks_llm():
    with pytest.raises(RoutingError, match="deterministic_only"):
        route("synthesize_recommendation", _agent(model_policy="deterministic_only"))


def test_plan_uses_router_executors():
    from app.executor import build_toyota_execution_plan

    team = build_fallback_team("demo", 5)
    plan = build_toyota_execution_plan(team)
    by_action = {t.action: t.executor for t in plan.tasks}
    assert by_action["build_financial_context"] == ExecutorType.CODE
    assert by_action["score_mobility_options"] == ExecutorType.CODE
    assert by_action["nessie_load_customer_financials"] == ExecutorType.TOOL
    assert by_action["synthesize_recommendation"] == ExecutorType.LLM
    assert by_action["evaluate_mobility_relevance"] == ExecutorType.CODE
    assert "evaluate-mobility-relevance" in {t.id for t in plan.tasks}


def test_agent_and_global_budget_gates():
    costs = RunCostTracker(max_llm_calls=10, max_estimated_cost_usd=1.0)
    agent = _agent(max_calls=1, max_cost=0.01)
    assert costs.can_afford_llm(0.004, agent=agent)
    costs.record_llm(cost_usd=0.004, agent_id=agent.id)
    assert not costs.can_afford_llm(0.004, agent=agent)

    costs2 = RunCostTracker(max_llm_calls=1, max_estimated_cost_usd=1.0)
    agent2 = _agent(max_calls=5, max_cost=1.0)
    assert costs2.can_afford_llm(0.004, agent=agent2)
    costs2.record_llm(cost_usd=0.004, agent_id=agent2.id)
    assert not costs2.can_afford_llm(0.004, agent=agent2)

    costs3 = RunCostTracker(max_llm_calls=10, max_estimated_cost_usd=1.0)
    agent3 = _agent(max_calls=5, max_cost=0.003)
    assert not costs3.can_afford_llm(0.004, agent=agent3)


def test_demo_customers_are_relevant():
    for cid in ("customer-stable", "customer-stressed"):
        result = evaluate_mobility_relevance(load_toyota_customer(cid))
        assert result["relevant"] is True
        assert result["reasons"]


def test_synthetic_non_relevant():
    result = evaluate_mobility_relevance(
        {
            "contract": {"months_remaining": 24},
            "usage": {"change_percent": 2.0},
        }
    )
    assert result["relevant"] is False


def test_force_not_relevant_flag():
    toyota = load_toyota_customer("customer-stable")
    toyota = {**toyota, "force_not_relevant": True}
    assert evaluate_mobility_relevance(toyota)["relevant"] is False


def _wait_run(client, run_id: str, timeout: float = 60.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/api/runs/{run_id}")
        assert r.status_code == 200
        body = r.json()
        if body["status"] in {"COMPLETED", "FAILED", "BUDGET_LIMIT", "NO_ACTION_NEEDED"}:
            return body
        time.sleep(0.2)
    raise AssertionError("Run did not finish in time")


def test_non_relevant_run_stops_before_nessie_llm(client, monkeypatch):
    monkeypatch.setenv("META_API_KEY", "")
    import app.services.llm_service as llm_mod

    llm_mod._llm_service = None

    original = load_toyota_customer

    def _not_relevant(customer_id: str):
        data = original(customer_id)
        return {**data, "force_not_relevant": True}

    monkeypatch.setattr("app.executor.load_toyota_customer", _not_relevant)

    team = build_fallback_team("demo", 5).model_dump()
    r = client.post(
        "/api/runs",
        json={
            "goal": "demo",
            "team": team,
            "context": {"customer_id": "customer-stable"},
            "quality_mode": "normal",
        },
    )
    assert r.status_code == 202
    body = _wait_run(client, r.json()["run_id"])
    assert body["status"] == "NO_ACTION_NEEDED"
    assert body["recommendation"] is None
    assert body["metrics"]["llm_calls"] == 0
    actions = {t["action"]: t["status"] for t in body["tasks"]}
    assert actions["evaluate_mobility_relevance"] == "COMPLETED"
    assert actions.get("nessie_load_customer_financials") == "SKIPPED"
    assert actions.get("synthesize_recommendation") == "SKIPPED"

    audit = client.get(f"/api/runs/{body['run_id']}/audit").json()["events"]
    types = {e["event_type"] for e in audit}
    assert "ROUTE_DECISION" in types
    assert "RELEVANCE_CHECK" in types
    assert not any(
        e["event_type"] == "TOOL_CALL" and "nessie" in (e.get("resource") or "")
        for e in audit
    )
    assert not any(e["event_type"] == "LLM_CALL" for e in audit)


def test_context_sources_endpoint(client):
    r = client.get("/api/context-sources")
    assert r.status_code == 200
    ids = [s["id"] for s in r.json()["sources"]]
    assert "toyota_customer" in ids
    assert "nessie_banking" in ids


def test_mobility_trigger_relevant(client):
    r = client.post(
        "/api/triggers/mobility/evaluate",
        json={"customer_id": "customer-stable", "start_run_if_relevant": False},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["customer_id"] == "customer-stable"
    assert body["relevant"] is True
    assert body["reasons"]
    assert body["run_id"] is None


def test_mobility_trigger_start_run(client, monkeypatch):
    monkeypatch.setenv("META_API_KEY", "")
    import app.services.llm_service as llm_mod

    llm_mod._llm_service = None

    r = client.post(
        "/api/triggers/mobility/evaluate",
        json={"customer_id": "customer-stable", "start_run_if_relevant": True},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["relevant"] is True
    assert body["run_id"]
    run = _wait_run(client, body["run_id"])
    assert run["status"] == "COMPLETED"
    assert run["recommendation"]["recommended_action"] == "BUY_CURRENT_VEHICLE"


def test_same_team_both_customers(client, monkeypatch):
    monkeypatch.setenv("META_API_KEY", "")
    import app.services.llm_service as llm_mod

    llm_mod._llm_service = None
    team = build_fallback_team("demo", 5)
    team_name = team.team_name
    payload = team.model_dump()

    def start(cid: str):
        r = client.post(
            "/api/runs",
            json={
                "goal": "demo",
                "team": payload,
                "context": {"customer_id": cid},
                "quality_mode": "normal",
            },
        )
        assert r.status_code == 202
        return _wait_run(client, r.json()["run_id"])

    a = start("customer-stable")
    b = start("customer-stressed")
    assert a["recommendation"]["recommended_action"] == "BUY_CURRENT_VEHICLE"
    assert b["recommendation"]["recommended_action"] == "RETURN_VEHICLE"
    assert a["recommendation"]["recommended_action"] != b["recommendation"]["recommended_action"]
    # Same reusable team definition (name); customer context differs
    assert team_name == "Toyota Mobility Decision Team"

    audit = client.get(f"/api/runs/{a['run_id']}/audit").json()["events"]
    assert any(e["event_type"] == "ROUTE_DECISION" for e in audit)
    assert any(e["event_type"] == "DATA_MINIMIZATION" for e in audit)
    # Reviewer skip reason
    assert any(
        "normal-risk run" in (e.get("message") or "") for e in audit
    )
