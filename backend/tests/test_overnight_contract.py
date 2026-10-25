"""API regression checks: unsupported work and user edits must fail honestly."""
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import BudgetPolicy
from app.planner import build_fallback_team
from app.team_normalize import ensure_team_runtime_config

MOBILITY = "Recommend the next mobility decision for one Toyota customer."
GOALS = [
    ("Build a recruiting team that finds candidates, emails them and schedules interviews.", "ATS"),
    ("Find the strongest Toyota customers to target for a new vehicle loan.", "Loan targeting"),
    ("Identify Toyota customers who currently need a mobility decision and recommend the best next step.", "Portfolio"),
]

@pytest.mark.parametrize("goal,reason", GOALS[:1])
def test_unsupported_plan_does_not_call_llm(monkeypatch, goal, reason):
    planner = AsyncMock(side_effect=AssertionError("Unsupported work must not call LLM"))
    monkeypatch.setattr("app.planner.get_llm_service", planner)
    with TestClient(app) as client:
        res = client.post("/api/teams/plan", json={"goal": goal, "context_source_ids": ["toyota_customer", "nessie_banking"]})
    assert res.status_code == 400, res.text
    assert res.json()["error"]["code"] == "NEEDS_SETUP"
    assert reason in res.json()["error"]["message"]
    planner.assert_not_called()

@pytest.mark.parametrize("goal,_", GOALS)
def test_unsupported_run_cannot_reuse_mobility_team(monkeypatch, goal, _):
    execute = AsyncMock()
    monkeypatch.setattr("app.main.execute_run", execute)
    with TestClient(app) as client:
        res = client.post("/api/runs", json={"goal": goal, "team": build_fallback_team("demo",5).model_dump(), "context": {"customer_id": "customer-stable"}})
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "NEEDS_SETUP"
    execute.assert_not_called()

@pytest.mark.parametrize("edit", ["tool", "scope", "actions", "integration"])
def test_removed_permission_is_denied_before_any_execution(monkeypatch, edit):
    team = build_fallback_team("demo",5)
    financial = next(a for a in team.agents if a.id == "financial-agent")
    if edit == "tool":
        financial.allowed_tools.remove("nessie_load_customer_financials")
    elif edit == "scope":
        financial.permissions.data_scopes.remove("nessie_raw_financial")
    elif edit == "actions":
        financial.permissions.actions = []
    else:
        financial.allowed_integrations = []
    before = team.model_dump()
    execute = AsyncMock()
    monkeypatch.setattr("app.main.execute_run", execute)
    with TestClient(app) as client:
        res = client.post("/api/runs",json={"goal": MOBILITY,"team": before,"context":{"customer_id":"customer-stable"}})
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "PERMISSION_DENIED"
    assert team.model_dump() == before
    execute.assert_not_called()


def test_zero_budgets_and_empty_actions_are_preserved():
    team = build_fallback_team("demo",5)
    team.policy.max_llm_calls = 0
    team.policy.max_llm_cost_usd = 0
    for a in team.agents:
        a.budget = BudgetPolicy(max_llm_calls=0,max_llm_cost_usd=0)
        a.permissions.actions = []
    normalized = ensure_team_runtime_config(team)
    assert normalized.policy.max_llm_calls == 0
    assert normalized.policy.max_llm_cost_usd == 0
    assert all(a.budget.max_llm_calls == 0 and a.budget.max_llm_cost_usd == 0 and not a.permissions.actions for a in normalized.agents)


def test_team_workflow_metadata_cannot_be_ignored():
    team = build_fallback_team("demo",5)
    team.policy.workflow_kind = "loan_opportunity"
    with TestClient(app) as client:
        res = client.post("/api/runs",json={"goal":"demo","team":team.model_dump(),"context":{"customer_id":"customer-stable"}})
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "NEEDS_SETUP"

@pytest.mark.parametrize("ceiling", ["team", "agent"])
def test_zero_budget_blocks_llm_at_api_boundary(monkeypatch, ceiling):
    monkeypatch.setenv("META_API_KEY", "")
    import app.services.llm_service as service
    service._llm_service = None
    team = build_fallback_team("demo",5)
    if ceiling == "team":
        team.policy.max_llm_calls = 0
    else:
        next(a for a in team.agents if a.id == "decision-agent").budget.max_llm_cost_usd = 0
    with TestClient(app) as client:
        started = client.post("/api/runs",json={"goal":MOBILITY,"team":team.model_dump(),"context":{"customer_id":"customer-stable"}})
        assert started.status_code == 202, started.text
        result = client.get("/api/runs/" + started.json()["run_id"]).json()
    assert result["status"] == "BUDGET_LIMIT"
    assert result["metrics"]["llm_calls"] == 0
    assert not any(e["event_type"] == "LLM_CALL" for e in result["audit_events"])


def test_no_action_never_loads_nessie_or_calls_llm(monkeypatch):
    nessie = AsyncMock()
    llm = AsyncMock()
    monkeypatch.setattr("app.tools.nessie.NessieClient.load_customer_financials", nessie)
    monkeypatch.setattr("app.executor._llm_synthesize", llm)
    with TestClient(app) as client:
        started = client.post("/api/runs",json={"goal":MOBILITY,"team":build_fallback_team("demo",5).model_dump(),"context":{"customer_id":"customer-no-action"}})
        assert started.status_code == 202
        result = client.get("/api/runs/" + started.json()["run_id"]).json()
    assert result["status"] == "NO_ACTION_NEEDED"
    assert len(result["tasks"]) == 2
    nessie.assert_not_called()
    llm.assert_not_called()


def test_generated_team_survives_reordering_and_executes(monkeypatch):
    from tests.test_workflow_validate import _weak_llm_team
    from types import SimpleNamespace
    weak = _weak_llm_team()
    weak.agents.reverse()
    planner = SimpleNamespace(plan_team=AsyncMock(return_value=weak))
    monkeypatch.setattr("app.planner.get_llm_service", lambda: planner)
    monkeypatch.setenv("META_API_KEY", "")
    import app.services.llm_service as service
    service._llm_service = None
    with TestClient(app) as client:
        planned = client.post("/api/teams/plan",json={"goal":MOBILITY})
        assert planned.status_code == 200, planned.text
        team = planned.json()["team"]
        started = client.post("/api/runs",json={"goal":MOBILITY,"team":team,"context":{"customer_id":"customer-stable"}})
        assert started.status_code == 202, started.text
        result = client.get("/api/runs/" + started.json()["run_id"]).json()
    assert result["status"] == "COMPLETED"
    assert result["recommendation"]["recommended_action"] == "BUY_CURRENT_VEHICLE"


@pytest.mark.asyncio
async def test_llm_request_contains_only_derived_financial_context(monkeypatch):
    import json
    from types import SimpleNamespace
    from app.executor import _llm_synthesize
    from app.tools.nessie import NessieClient
    from app.tools.financial import build_financial_context
    from app.tools.toyota import load_toyota_customer
    from app.tools.deterministic import score_mobility_options
    banking = NessieClient()
    banking.api_key = ""
    raw, _ = banking.load_customer_financials("nessie-demo-001")
    financial = build_financial_context(raw)
    seed = score_mobility_options(load_toyota_customer("customer-stable"), financial)["recommendation_seed"]
    post = AsyncMock(return_value={"choices":[{"message":{"content":json.dumps(seed)}}]})
    service = SimpleNamespace(is_configured=lambda:True,meta_api_key="",_post_chat_completion=post)
    monkeypatch.setattr("app.executor.get_llm_service", lambda:service)
    team = build_fallback_team("demo",5)
    await _llm_synthesize(agent=next(a for a in team.agents if a.id=="decision-agent"),policy=team.policy,financial=financial,seed=seed,goal=MOBILITY,model_name="test-fast")
    user = json.loads(post.call_args.kwargs["payload"]["messages"][1]["content"])
    assert set(user) == {"goal","financial_context","seed_recommendation"}
    assert user["financial_context"] == financial.model_dump()
    assert not any(k in user["financial_context"] for k in ("accounts","purchases","transactions","customer_id"))
