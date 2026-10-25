from unittest.mock import AsyncMock
from types import SimpleNamespace
import json

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.job_analysis import analyze_job
from app.pack_workflows import build_team_for_job, build_pack_execution_plan

MOBILITY = "Identify Toyota customers who currently need a mobility decision and recommend the best next step."
LOAN = "Find the strongest Toyota customers to target for a new vehicle loan."


def run(client, goal, team):
    res = client.post(
        "/api/runs",
        json={
            "goal": goal,
            "team": team.model_dump(),
            "context": {"customer_id": "customer-stable"},
        },
    )
    assert res.status_code == 202, res.text
    return client.get("/api/runs/" + res.json()["run_id"]).json()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("META_API_KEY", "")
    import app.services.llm_service as service

    service._llm_service = None
    with TestClient(app) as c:
        yield c


def test_distinct_portfolio_workflows_and_relevance(client):
    results = []
    for goal in [MOBILITY, LOAN]:
        team = build_team_for_job(analyze_job(goal), goal)
        team.agents.reverse()
        result = run(client, goal, team)
        assert result["status"] == "COMPLETED", result["audit_events"][-1]
        assert result["recommendation"] is None
        assert result["portfolio_result"]["narrative"]
        results.append(result)
    mobility, loan = results
    assert mobility["portfolio_result"]["purpose"] == "mobility"
    assert loan["portfolio_result"]["purpose"] == "loan"
    assert len(mobility["portfolio_result"]["candidates"]) == 5
    assert len(loan["portfolio_result"]["candidates"]) == 7
    assert loan["portfolio_result"]["candidates"][0]["name"] == "Priya Shah"
    assert {
        c["customer_id"] for c in mobility["portfolio_result"]["skipped_customers"]
    } == {"customer-no-action", "customer-mid-lease"}
    assert [t["action"] for t in mobility["tasks"]] != [
        t["action"] for t in loan["tasks"]
    ]
    assert all(
        c["recommended_action"] for c in mobility["portfolio_result"]["candidates"]
    )
    assert all(
        c["recommended_action"] is None for c in loan["portfolio_result"]["candidates"]
    )
    assert (
        mobility["portfolio_result"]["candidates"][0]["customer_id"]
        != loan["portfolio_result"]["candidates"][0]["customer_id"]
    )
    assert not any(
        "customer-no-action: financial" in e["message"]
        for e in mobility["audit_events"]
    )


@pytest.mark.parametrize("grant", ["tool", "scope", "integration"])
def test_portfolio_permission_edits_are_not_repaired(client, grant):
    team = build_team_for_job(analyze_job(LOAN), LOAN)
    financial = next(
        a for a in team.agents if "nessie_raw_financial" in a.permissions.data_scopes
    )
    if grant == "tool":
        financial.allowed_tools.remove("nessie_load_customer_financials")
    elif grant == "scope":
        financial.permissions.data_scopes.remove("nessie_raw_financial")
    else:
        financial.allowed_integrations = []
    before = team.model_dump()
    res = client.post(
        "/api/runs",
        json={
            "goal": LOAN,
            "team": before,
            "context": {"customer_id": "customer-stable"},
        },
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "PERMISSION_DENIED"
    assert team.model_dump() == before


def test_portfolio_empty_relevance_stops_before_banking_and_model(client, monkeypatch):
    from app.tools.toyota import load_toyota_customer

    monkeypatch.setattr(
        "app.portfolio.load_toyota_customers",
        lambda: [load_toyota_customer("customer-no-action")],
    )
    banking = AsyncMock(side_effect=AssertionError("No banking on no-action"))
    monkeypatch.setattr(
        "app.tools.nessie.NessieClient.load_customer_financials", banking
    )
    res = run(client, MOBILITY, build_team_for_job(analyze_job(MOBILITY), MOBILITY))
    assert res["status"] == "NO_ACTION_NEEDED"
    assert res["metrics"]["llm_calls"] == 0
    assert len(res["portfolio_result"]["skipped_customers"]) == 1
    banking.assert_not_called()


def test_portfolio_requires_financial_connection(client):
    team = build_team_for_job(analyze_job(LOAN), LOAN)
    res = client.post(
        "/api/runs",
        json={
            "goal": LOAN,
            "team": team.model_dump(),
            "context": {"customer_id": "customer-stable"},
            "context_source_ids": ["toyota_customer"],
        },
    )
    assert res.status_code == 400
    assert "Nessie" in res.json()["error"]["message"]


def test_portfolio_plan_and_run_use_arbitrary_agent_ids(client, monkeypatch):
    team = build_team_for_job(analyze_job(LOAN), LOAN)
    for i, agent in enumerate(team.agents):
        agent.id = f"custom-{i}"
    team.agents.reverse()
    monkeypatch.setattr(
        "app.planner.get_llm_service",
        lambda: SimpleNamespace(plan_team=AsyncMock(return_value=team)),
    )
    planned = client.post("/api/teams/plan", json={"goal": LOAN})
    assert planned.status_code == 200, planned.text
    assert planned.json()["team"]["policy"]["workflow_kind"] == "loan_opportunity"
    assert len(planned.json()["team"]["agents"]) == 2
    finance = next(a for a in planned.json()["team"]["agents"] if "build_portfolio_financials" in a["allowed_tools"])
    assert "filter_mobility_portfolio" not in finance["allowed_tools"]
    assert "full Toyota demo portfolio" in finance["purpose"]
    assert planned.json()["job_analysis"]["scope"] == "portfolio"
    from app.models import TeamDefinition

    result = run(client, LOAN, TeamDefinition.model_validate(planned.json()["team"]))
    assert result["status"] == "COMPLETED"


def test_zero_shortlist_budget_blocks_real_model(client, monkeypatch):
    team = build_team_for_job(analyze_job(LOAN), LOAN)
    team.policy.max_llm_calls = 0
    result = run(client, LOAN, team)
    assert result["status"] == "BUDGET_LIMIT"
    assert result["metrics"]["llm_calls"] == 0


def test_actual_shortlist_model_payload_is_minimized(client, monkeypatch):
    post = AsyncMock(
        return_value={
            "choices": [{"message": {"content": "Derived shortlist explanation."}}]
        }
    )
    service = SimpleNamespace(
        is_configured=lambda: True,
        meta_api_key="",
        fast_model="test-fast",
        strong_model="test-strong",
        _post_chat_completion=post,
    )
    monkeypatch.setattr("app.services.llm_service.get_llm_service", lambda: service)
    monkeypatch.setattr("app.model_router.get_llm_service", lambda: service)
    result = run(client, LOAN, build_team_for_job(analyze_job(LOAN), LOAN))
    assert result["status"] == "COMPLETED"
    assert result["metrics"]["llm_calls"] == 1
    payload = json.loads(post.call_args.kwargs["payload"]["messages"][1]["content"])
    assert set(payload) == {"purpose", "ranked_summaries"}
    assert len(payload["ranked_summaries"]) == 7
    assert all(row["name"] for row in payload["ranked_summaries"])
    serialized = json.dumps(payload)
    for forbidden in [
        "purchases",
        "transactions",
        "account_id",
        "nessie_customer_id",
        "monthly_income",
        "fixed_expenses",
    ]:
        assert forbidden not in serialized
    assert any(e["event_type"] == "LLM_CALL" for e in result["audit_events"])


def test_portfolio_result_survives_database_hydration(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.db_models import Base
    import app.db as db
    import app.run_store as store

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(db, "_SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(db, "db_available", True)
    monkeypatch.setattr("app.main.init_db", lambda: True)
    monkeypatch.setenv("META_API_KEY", "")
    import app.services.llm_service as service

    service._llm_service = None
    with TestClient(app) as client:
        result = run(client, LOAN, build_team_for_job(analyze_job(LOAN), LOAN))
        assert result["status"] == "COMPLETED"
        run_id = result["run_id"]
        store._runs.pop(run_id, None)
        store._run_team_names.pop(run_id, None)
        restored = client.get("/api/runs/" + run_id).json()
        assert restored["portfolio_result"] == result["portfolio_result"]
        assert restored["recommendation"] is None
        assert restored["metrics"] == result["metrics"]
    engine.dispose()


def test_recruiting_for_toyota_is_still_unsupported(client):
    res = client.post(
        "/api/teams/plan",
        json={"goal": "Recruit Toyota mechanics, email them and schedule interviews."},
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "NEEDS_SETUP"
    assert all(
        name in res.json()["error"]["message"] for name in ["ATS", "email", "calendar"]
    )


def test_portfolio_evaluator_rejects_bad_scores_and_raw_fields():
    from app.portfolio import validate_portfolio_result
    from app.models import PortfolioResult

    result = PortfolioResult(
        purpose="loan",
        candidates=[{"customer_id": "demo", "score": 120, "transactions": []}],
    )
    checks = validate_portfolio_result(result, ["demo"])
    assert all(c.status.value == "FAIL" for c in checks)


def test_equal_loan_scores_are_ordered_by_financial_headroom_not_input_order():
    from app.tools.deterministic import rank_customers
    def row(identity,budget):
        return {"customer_id":identity,"name":identity,"financial":{"affordability_score":100,"loan_candidate_fit":True,"safe_mobility_budget":budget,"emergency_runway_months":4,"monthly_free_cashflow":2000}}
    lower,higher=row("first-in-input",500),row("second-in-input",900)
    assert rank_customers([lower,higher])["top_ids"][0]=="second-in-input"
    assert rank_customers([higher,lower])["top_ids"][0]=="second-in-input"
