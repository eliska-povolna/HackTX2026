"""ModelRouter, TeamPolicy budgets, permissions, prompts, work style."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.cost_optimizer import RunCostTracker
from app.main import app
from app.model_router import ModelRoutingError, choose_model
from app.models import (
    AgentDefinition,
    AgentPermissions,
    BudgetPolicy,
    TaskDefinition,
    TeamPolicy,
    RiskLevel,
    ExecutorType,
)
from app.permissions import PermissionDenied, validate_task_permissions
from app.planner import build_fallback_team
from app.prompts import build_agent_system_prompt
from app.services import llm_service as llm_mod


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _agent(**kwargs) -> AgentDefinition:
    base = dict(
        id="decision-agent",
        name="Dana",
        role="Decision Specialist",
        purpose="Produce a clear mobility recommendation from approved derived data.",
        skills=["recommendation", "tradeoff_synthesis", "customer_guidance"],
        allowed_tools=["synthesize_recommendation", "run_evaluators"],
        permissions=AgentPermissions(
            data_scopes=["financial_summary", "vehicle_data", "lease_data"],
            actions=["read", "synthesize", "recommend"],
        ),
        budget=BudgetPolicy(max_llm_cost_usd=0.05, max_llm_calls=2),
        instructions="Use financial_summary only. Never request raw banking rows.",
        guardrails=["Do not access nessie_raw_financial"],
        model_policy="auto",
        allowed_integrations=["meta"],
    )
    base.update(kwargs)
    return AgentDefinition(**base)


def test_model_router_synthesis_fast_vs_thorough(monkeypatch):
    svc = llm_mod.LLMService()
    svc.fast_model = "fast-model-x"
    svc.strong_model = "strong-model-y"
    monkeypatch.setattr(llm_mod, "_llm_service", svc)

    fast = choose_model(task_kind="synthesis", work_style="fast")
    assert fast.tier == "FAST"
    assert fast.model == "fast-model-x"

    balanced = choose_model(task_kind="synthesis", work_style="balanced")
    assert balanced.tier == "FAST"

    thorough = choose_model(task_kind="synthesis", work_style="thorough")
    assert thorough.tier == "STRONG"
    assert thorough.model == "strong-model-y"

    review = choose_model(task_kind="review", work_style="fast")
    assert review.tier == "STRONG"

    plan_fast = choose_model(task_kind="planning", work_style="fast")
    assert plan_fast.tier == "FAST"
    plan_bal = choose_model(task_kind="planning", work_style="balanced")
    assert plan_bal.tier == "STRONG"


def test_model_router_missing_tier_fails(monkeypatch):
    svc = llm_mod.LLMService()
    svc.fast_model = ""
    svc.strong_model = "strong-only"
    monkeypatch.setattr(llm_mod, "_llm_service", svc)
    with pytest.raises(ModelRoutingError, match="FAST"):
        choose_model(task_kind="synthesis", work_style="fast")


def test_permission_missing_tool_denied():
    agent = _agent(allowed_tools=["synthesize_recommendation"])  # no nessie
    with pytest.raises(PermissionDenied, match="allowed_tools"):
        validate_task_permissions(
            agent,
            TaskDefinition(
                id="t",
                name="n",
                assigned_agent_id=agent.id,
                executor=ExecutorType.TOOL,
                action="nessie_load_customer_financials",
                risk_level=RiskLevel.medium,
            ),
            require_integrations=False,
        )


def test_permission_missing_scope_denied():
    agent = _agent(
        allowed_tools=["nessie_load_customer_financials"],
        permissions=AgentPermissions(data_scopes=["financial_summary"], actions=["read"]),
        allowed_integrations=["nessie"],
    )
    with pytest.raises(PermissionDenied, match="data_scope"):
        validate_task_permissions(
            agent,
            "nessie_load_customer_financials",
            require_integrations=False,
        )


def test_permission_missing_action_denied():
    agent = _agent(
        permissions=AgentPermissions(
            data_scopes=["financial_summary", "vehicle_data", "lease_data"],
            actions=["read"],  # no synthesize/recommend
        )
    )
    with pytest.raises(PermissionDenied, match="actions"):
        validate_task_permissions(agent, "synthesize_recommendation", require_integrations=False)


def test_runtime_does_not_mutate_agent_permissions():
    team = build_fallback_team("demo", 5)
    agent = next(a for a in team.agents if a.id == "decision-agent")
    before_tools = list(agent.allowed_tools)
    before_scopes = list(agent.permissions.data_scopes)
    # Plan build must not broaden decision agent to include nessie
    from app.executor import build_toyota_execution_plan

    plan = build_toyota_execution_plan(team)
    assert "nessie_load_customer_financials" not in agent.allowed_tools
    assert agent.allowed_tools == before_tools
    assert agent.permissions.data_scopes == before_scopes
    assert any(t.action == "synthesize_recommendation" for t in plan.tasks)


def test_system_prompt_contains_config_not_secrets():
    policy = TeamPolicy(
        work_style="balanced",
        guardrails=["Never expose raw customer financial data"],
    )
    agent = _agent()
    prompt = build_agent_system_prompt(
        agent,
        policy,
        task_instruction="Synthesize the customer-facing recommendation.",
    )
    assert "Dana" in prompt
    assert "Decision Specialist" in prompt
    assert "recommendation" in prompt
    assert "financial_summary" in prompt
    assert "Never expose raw customer financial data" in prompt
    assert "Do not access nessie_raw_financial" in prompt
    assert "META_API_KEY" not in prompt
    assert "NESSIE_API_KEY" not in prompt
    assert "sk-" not in prompt
    assert "Bearer" not in prompt
    assert "purchases" not in prompt.lower() or "raw" in prompt.lower()


def test_team_and_agent_budgets_enforced():
    costs = RunCostTracker.from_team_policy(
        TeamPolicy(max_llm_calls=1, max_llm_cost_usd=0.01, work_style="balanced")
    )
    agent = _agent(budget=BudgetPolicy(max_llm_cost_usd=1.0, max_llm_calls=5))
    assert costs.can_afford_llm(0.004, agent=agent)
    costs.record_llm(0.004, agent_id=agent.id)
    assert not costs.can_afford_llm(0.004, agent=agent)  # team call limit

    costs2 = RunCostTracker(max_llm_calls=10, max_estimated_cost_usd=1.0)
    costs2.team_max_llm_calls = 10
    costs2.team_max_llm_cost_usd = 1.0
    agent2 = _agent(budget=BudgetPolicy(max_llm_cost_usd=0.003, max_llm_calls=5))
    assert not costs2.can_afford_llm(0.004, agent=agent2)


def test_work_style_reviewer_policy():
    costs = RunCostTracker()
    assert not costs.should_run_reviewer(
        work_style="fast", quality_mode="normal", high_risk=False, has_reviewer=True
    )
    assert not costs.should_run_reviewer(
        work_style="balanced", quality_mode="normal", high_risk=False, has_reviewer=True
    )
    assert costs.should_run_reviewer(
        work_style="thorough", quality_mode="normal", high_risk=False, has_reviewer=True
    )
    assert costs.should_run_reviewer(
        work_style="fast", quality_mode="strict", high_risk=False, has_reviewer=True
    )
    assert not costs.should_run_reviewer(
        work_style="thorough", quality_mode="normal", high_risk=False, has_reviewer=False
    )


def _wait_run(client, run_id: str, timeout: float = 60.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/api/runs/{run_id}")
        assert r.status_code == 200
        body = r.json()
        if body["status"] in {"COMPLETED", "FAILED", "BUDGET_LIMIT", "NO_ACTION_NEEDED"}:
            return body
        time.sleep(0.2)
    raise AssertionError("timeout")


def test_toyota_regression_and_thorough_review(client, monkeypatch):
    monkeypatch.setenv("META_API_KEY", "")
    llm_mod._llm_service = None

    team = build_fallback_team("demo", 5, work_style="balanced")
    r = client.post(
        "/api/runs",
        json={
            "goal": "demo",
            "team": team.model_dump(),
            "context": {"customer_id": "customer-stable"},
            "quality_mode": "normal",
        },
    )
    assert r.status_code == 202
    body = _wait_run(client, r.json()["run_id"])
    assert body["status"] == "COMPLETED"
    assert body["recommendation"]["recommended_action"] == "BUY_CURRENT_VEHICLE"
    assert body["metrics"]["work_style"] == "balanced"
    audit = client.get(f"/api/runs/{body['run_id']}/audit").json()["events"]
    assert any(e["event_type"] == "ROUTE_DECISION" for e in audit)
    assert any("Reviewer skipped" in (e.get("message") or "") for e in audit)

    thorough = build_fallback_team("demo", 5, work_style="thorough")
    r2 = client.post(
        "/api/runs",
        json={
            "goal": "demo",
            "team": thorough.model_dump(),
            "context": {"customer_id": "customer-stressed"},
            "quality_mode": "normal",
        },
    )
    assert r2.status_code == 202
    body2 = _wait_run(client, r2.json()["run_id"])
    assert body2["status"] == "COMPLETED"
    assert body2["recommendation"]["recommended_action"] == "RETURN_VEHICLE"
    assert body2["metrics"]["work_style"] == "thorough"
    audit2 = client.get(f"/api/runs/{body2['run_id']}/audit").json()["events"]
    assert any(
        e.get("resource") == "review_recommendation"
        or "Reviewer" in (e.get("message") or "")
        or e.get("event_type") == "CODE_FALLBACK"
        for e in audit2
    )
    # Reviewer path should have executed (deterministic PASS in test mode)
    assert any(
        (e.get("resource") == "review_recommendation")
        or ("deterministic review" in (e.get("message") or "").lower())
        for e in audit2
    )


def test_denied_agent_cannot_run_nessie(client, monkeypatch):
    monkeypatch.setenv("META_API_KEY", "")
    llm_mod._llm_service = None
    team = build_fallback_team("demo", 5)
    # Strip financial agent Nessie tool — plan validation must fail
    for a in team.agents:
        if a.id == "financial-agent":
            a.allowed_tools = [t for t in a.allowed_tools if "nessie" not in t]
    r = client.post(
        "/api/runs",
        json={
            "goal": "demo",
            "team": team.model_dump(),
            "context": {"customer_id": "customer-stable"},
            "quality_mode": "normal",
        },
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "PERMISSION_DENIED"
