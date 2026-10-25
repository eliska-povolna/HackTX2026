"""Plan-time workflow validation, capability alignment, work-step kinds."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.executor import build_toyota_execution_plan
from app.main import app
from app.models import (
    AgentDefinition,
    AgentPermissions,
    BudgetPolicy,
    TeamDefinition,
    TeamPolicy,
)
from app.permissions import validate_plan_permissions
from app.planner import build_fallback_team
from app.agent_assignment import assign_toyota_roles as assign_roles
from app.workflow_validate import (
    WorkStepKind,
    validate_team_against_workflow,
    work_step_kind,
)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _weak_llm_team() -> TeamDefinition:
    """LLM-shaped team: roles match, but permissions are incomplete."""
    return TeamDefinition(
        id="llm-weak",
        team_name="Mobility",
        summary="weak",
        policy=TeamPolicy(work_style="balanced", max_llm_calls=6, max_llm_cost_usd=0.2),
        agents=[
            AgentDefinition(
                id="agent_toyota_context",
                name="Casey",
                role="Context Agent",
                purpose="Load customer signals",
                skills=["customer_context"],
                allowed_tools=["load_toyota_customer"],  # missing evaluate
                permissions=AgentPermissions(data_scopes=[], actions=["read"]),
                budget=BudgetPolicy(max_llm_cost_usd=0.02, max_llm_calls=1),
            ),
            AgentDefinition(
                id="agent_financial_profiler",
                name="Maya",
                role="Financial Profiler",
                purpose="Banking",
                skills=["cashflow_analysis", "financial_context"],
                allowed_tools=[],  # missing nessie
                permissions=AgentPermissions(data_scopes=[], actions=["read"]),
                budget=BudgetPolicy(max_llm_cost_usd=0.05, max_llm_calls=1),
            ),
            AgentDefinition(
                id="agent_vehicle_fit",
                name="Riley",
                role="Vehicle Analyst",
                purpose="Score options",
                skills=["vehicle_fit", "mobility_options"],
                allowed_tools=[],
                permissions=AgentPermissions(data_scopes=[], actions=["read"]),
                budget=BudgetPolicy(max_llm_cost_usd=0.03, max_llm_calls=1),
            ),
            AgentDefinition(
                id="agent_decision_strategist",
                name="Dana",
                role="Decision Strategist",
                purpose="Recommend",
                skills=["recommendation", "tradeoff_synthesis"],
                allowed_tools=[],
                permissions=AgentPermissions(data_scopes=[], actions=["read"]),
                budget=BudgetPolicy(max_llm_cost_usd=0.05, max_llm_calls=2),
                model_policy="auto",
            ),
        ],
    )


def test_work_step_kinds_prefer_code_and_tool_over_agent():
    assert work_step_kind("evaluate_mobility_relevance") == WorkStepKind.CODE
    assert work_step_kind("build_financial_context") == WorkStepKind.CODE
    assert work_step_kind("score_mobility_options") == WorkStepKind.CODE
    assert work_step_kind("nessie_load_customer_financials") == WorkStepKind.TOOL
    assert work_step_kind("synthesize_recommendation") == WorkStepKind.AGENT
    assert work_step_kind("review_recommendation") == WorkStepKind.AGENT


def test_weak_team_fails_without_repair():
    team = _weak_llm_team()
    result = validate_team_against_workflow(team, repair=False)
    assert not result.ok
    assert result.status == "NEEDS_SETUP"
    assert result.errors


def test_plan_time_repair_makes_weak_team_runnable():
    team = _weak_llm_team()
    result = validate_team_against_workflow(team, repair=True)
    assert result.ok, result.errors
    assert result.repaired
    assert result.status == "READY_TO_RUN"
    plan = build_toyota_execution_plan(result.team)
    err = validate_plan_permissions({a.id: a for a in result.team.agents}, plan.tasks)
    assert err is None


def test_reordered_agents_still_assign_and_validate():
    team = _weak_llm_team()
    team.agents = list(reversed(team.agents))
    result = validate_team_against_workflow(team, repair=True)
    assert result.ok, result.errors
    roles = assign_roles(result.team.agents)
    assert roles["context"] == "agent_toyota_context"
    assert roles["financial"] == "agent_financial_profiler"
    assert roles["vehicle"] == "agent_vehicle_fit"
    assert roles["decision"] == "agent_decision_strategist"


def test_runtime_does_not_repair_stripped_permissions(client, monkeypatch):
    monkeypatch.setenv("META_API_KEY", "")
    import app.services.llm_service as llm_mod

    llm_mod._llm_service = None

    team = build_fallback_team("demo", 5)
    # Plan-time style team that is valid, then user strips Nessie at run time
    for a in team.agents:
        if a.id == "financial-agent":
            a.allowed_tools = [t for t in a.allowed_tools if "nessie" not in t]
            a.allowed_integrations = []

    # Unrepaired validation still denies. create_run aligns the Toyota pack
    # before execution so the submitted copy can run; the caller's object is not mutated.
    result = validate_team_against_workflow(team, repair=False)
    assert not result.ok
    stripped = next(a.allowed_tools for a in team.agents if a.id == "financial-agent")

    r = client.post(
        "/api/runs",
        json={
            "goal": "demo",
            "team": team.model_dump(),
            "context": {"customer_id": "customer-stable"},
            "quality_mode": "normal",
        },
    )
    assert r.status_code == 202, r.text
    assert next(a.allowed_tools for a in team.agents if a.id == "financial-agent") == stripped


def _wait_run(client, run_id: str, timeout: float = 60.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/runs/{run_id}").json()
        if body["status"] in {"COMPLETED", "FAILED", "BUDGET_LIMIT", "NO_ACTION_NEEDED"}:
            return body
        time.sleep(0.2)
    raise AssertionError("timeout")


def test_repaired_llm_team_runs_all_customers(client, monkeypatch):
    monkeypatch.setenv("META_API_KEY", "")
    import app.services.llm_service as llm_mod

    llm_mod._llm_service = None

    result = validate_team_against_workflow(_weak_llm_team(), repair=True)
    assert result.ok, result.errors
    team = result.team

    for customer_id, expected_status, expected_action in [
        ("customer-stable", "COMPLETED", "BUY_CURRENT_VEHICLE"),
        ("customer-stressed", "COMPLETED", "RETURN_VEHICLE"),
        ("customer-no-action", "NO_ACTION_NEEDED", None),
    ]:
        r = client.post(
            "/api/runs",
            json={
                "goal": "mobility decision",
                "team": team.model_dump(),
                "context": {"customer_id": customer_id},
                "quality_mode": "normal",
            },
        )
        assert r.status_code == 202, r.text
        body = _wait_run(client, r.json()["run_id"])
        assert body["status"] == expected_status, (customer_id, body["status"])
        if expected_action:
            assert body["recommendation"]["recommended_action"] == expected_action
        else:
            assert body.get("recommendation") in (None, {})
            # No Nessie / LLM on no-action
            assert body["metrics"]["llm_calls"] == 0
            assert body["metrics"]["tool_tasks"] == 0 or all(
                t["status"] == "SKIPPED" or "nessie" not in (t.get("action") or "")
                for t in body.get("tasks", [])
                if "nessie" in (t.get("action") or "")
            )
            nessie_ran = [
                t
                for t in body.get("tasks", [])
                if t.get("action") == "nessie_load_customer_financials"
                and t.get("status") == "COMPLETED"
            ]
            assert nessie_ran == []


def test_recommendation_not_forced_by_customer_id():
    """Scoring uses financial context, not customer_id branches."""
    from app.tools.deterministic import score_mobility_options
    from app.models import FinancialContext
    from app.tools.toyota import load_toyota_customer

    toyota = load_toyota_customer("customer-stable")
    # Same Toyota vehicle profile with HIGH stress finance → must not force BUY
    high = FinancialContext(
        monthly_income=4000,
        fixed_expenses=3200,
        variable_spending=600,
        cash_reserve=800,
        monthly_free_cashflow=200,
        safe_mobility_budget=250,
        emergency_runway_months=0.8,
        debt_burden_ratio=0.4,
        financial_stress="HIGH",
    )
    low = FinancialContext(
        monthly_income=7000,
        fixed_expenses=2500,
        variable_spending=800,
        cash_reserve=12000,
        monthly_free_cashflow=2500,
        safe_mobility_budget=700,
        emergency_runway_months=6.0,
        debt_burden_ratio=0.1,
        financial_stress="LOW",
    )
    high_rec = score_mobility_options(toyota, high)["recommendation_seed"]["recommended_action"]
    low_rec = score_mobility_options(toyota, low)["recommendation_seed"]["recommended_action"]
    assert high_rec != "BUY_CURRENT_VEHICLE"
    assert low_rec == "BUY_CURRENT_VEHICLE"
    assert "customer-stable" not in str(high_rec)


def test_scope_alias_and_empty_actions():
    from app.models import AgentDefinition, AgentPermissions, BudgetPolicy, TeamDefinition, TeamPolicy
    from app.team_normalize import ensure_team_runtime_config

    team = TeamDefinition(
        id="alias",
        team_name="Alias",
        summary="",
        policy=TeamPolicy(),
        agents=[
            AgentDefinition(
                id="fin",
                name="Fin",
                role="Financial",
                purpose="p",
                skills=[],
                allowed_tools=["nessie_load_customer_financials"],
                permissions=AgentPermissions(
                    data_scopes=["customer_financials", "bank_accounts"],
                    actions=[],
                ),
                budget=BudgetPolicy(max_llm_cost_usd=0.05, max_llm_calls=1),
            )
        ],
    )
    normalized = ensure_team_runtime_config(team)
    agent = normalized.agents[0]
    assert "nessie_raw_financial" in agent.permissions.data_scopes
    assert "customer_financials" not in agent.permissions.data_scopes
    assert agent.permissions.actions == ["read"]
    # Original object is not rewritten in place.
    assert team.agents[0].permissions.actions == []


def test_weak_team_runs_without_manual_repair(client, monkeypatch):
    monkeypatch.setenv("META_API_KEY", "")
    import app.services.llm_service as llm_mod

    llm_mod._llm_service = None
    team = _weak_llm_team()
    for customer_id, expected_status in [
        ("customer-stable", "COMPLETED"),
        ("customer-stressed", "COMPLETED"),
        ("customer-no-action", "NO_ACTION_NEEDED"),
    ]:
        r = client.post(
            "/api/runs",
            json={
                "goal": "mobility decision",
                "team": team.model_dump(),
                "context": {"customer_id": customer_id},
                "quality_mode": "normal",
            },
        )
        assert r.status_code == 202, (customer_id, r.text)
        body = _wait_run(client, r.json()["run_id"])
        assert body["status"] == expected_status, (customer_id, body["status"], body.get("recommendation"))
        if expected_status == "NO_ACTION_NEEDED":
            assert body["metrics"]["llm_calls"] == 0
            assert not any(
                t.get("action") == "nessie_load_customer_financials" and t.get("status") == "COMPLETED"
                for t in body.get("tasks", [])
            )
