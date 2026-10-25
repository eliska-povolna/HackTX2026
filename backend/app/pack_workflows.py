"""Assemble teams + execution plans from the installed Toyota capability pack.

Different goals → different workflows from the SAME catalog — not separate scripts.
"""

from __future__ import annotations

import uuid
from typing import Optional

from app.job_analysis import JobAnalysis, JobScope, WorkflowKind
from app.models import (
    AgentDefinition,
    AgentPermissions,
    BudgetPolicy,
    ExecutionPlan,
    ExecutorType,
    RiskLevel,
    TaskDefinition,
    TeamDefinition,
    TeamPolicy,
)
from app.routing import route
from app.team_normalize import ensure_team_runtime_config


def _task(
    *,
    task_id: str,
    name: str,
    agent_id: str,
    action: str,
    agents: dict[str, AgentDefinition],
    depends_on: list[str],
    risk_level: RiskLevel = RiskLevel.medium,
) -> TaskDefinition:
    agent = agents.get(agent_id)
    decision = route(action, agent)
    return TaskDefinition(
        id=task_id,
        name=name,
        assigned_agent_id=agent_id,
        executor=decision.executor,
        action=action,
        depends_on=depends_on,
        risk_level=risk_level,
    )


def build_team_for_job(analysis: JobAnalysis, goal: str) -> TeamDefinition:
    """Minimize agents for the analyzed workflow."""
    style = "balanced"
    if analysis.workflow_kind == WorkflowKind.LOAN_OPPORTUNITY:
        agents = [
            AgentDefinition(
                id="financial-agent",
                name="Maya",
                role="Financial Context Analyst",
                purpose="Derive per-customer FinancialContext and affordability for loan ranking.",
                skills=["cashflow_analysis", "affordability", "financial_context", "portfolio"],
                instructions=(
                    "Load portfolio financials via approved tools. Emit only derived "
                    "FinancialContext fields downstream — never raw Nessie rows."
                ),
                allowed_tools=[
                    "load_toyota_portfolio",
                    "load_toyota_customer",
                    "nessie_load_customer_financials",
                    "build_financial_context",
                    "build_portfolio_financials",
                    "calculate_affordability",
                    "identify_financial_stress",
                    "calculate_emergency_runway",
                ],
                permissions=AgentPermissions(
                    data_scopes=["nessie_raw_financial", "financial_summary", "toyota_customer_profile"],
                    actions=["read", "calculate"],
                ),
                guardrails=["Do not expose raw transactions to downstream agents."],
                model_policy="auto",
                budget=BudgetPolicy(max_llm_cost_usd=0.05, max_llm_calls=1),
                allowed_integrations=["nessie"],
            ),
            AgentDefinition(
                id="decision-agent",
                name="Dana",
                role="Loan Shortlist Strategist",
                purpose="Rank candidates with CODE then explain the shortlist.",
                skills=["recommendation", "tradeoff_synthesis", "customer_guidance", "ranking"],
                instructions=(
                    "Use rank_customers on minimized summaries only. Explain shortlist "
                    "without requesting raw banking rows."
                ),
                allowed_tools=["rank_customers", "explain_shortlist", "run_evaluators"],
                permissions=AgentPermissions(
                    data_scopes=["financial_summary", "toyota_customer_profile", "vehicle_data"],
                    actions=["read", "synthesize", "recommend", "calculate"],
                ),
                guardrails=["Never access nessie_raw_financial."],
                model_policy="auto",
                budget=BudgetPolicy(max_llm_cost_usd=0.05, max_llm_calls=2),
                allowed_integrations=["meta"],
            ),
        ]
        team_name = "Vehicle Loan Opportunity Team"
        summary = "Ranks Toyota customers for new-vehicle loan targeting using Nessie-derived finance."
    elif analysis.scope == JobScope.PORTFOLIO:
        agents = [
            AgentDefinition(
                id="context-agent",
                name="Casey",
                role="Portfolio Context Agent",
                purpose="Load portfolio and apply deterministic mobility relevance filters.",
                skills=["customer_context", "signal_detection", "portfolio"],
                instructions="Use Toyota portfolio tools only. Prefer CODE relevance before finance.",
                allowed_tools=[
                    "load_toyota_portfolio",
                    "load_toyota_customer",
                    "evaluate_mobility_relevance",
                    "filter_mobility_portfolio",
                ],
                permissions=AgentPermissions(
                    data_scopes=[
                        "toyota_customer_profile",
                        "vehicle_data",
                        "lease_data",
                        "usage_data",
                    ],
                    actions=["read", "calculate"],
                ),
                guardrails=["Do not access raw Nessie financial data."],
                model_policy="deterministic_only",
                budget=BudgetPolicy(max_llm_cost_usd=0.01, max_llm_calls=0),
                allowed_integrations=[],
            ),
            AgentDefinition(
                id="financial-agent",
                name="Maya",
                role="Financial Context Analyst",
                purpose="Build financial summaries for relevant customers only.",
                skills=["cashflow_analysis", "affordability", "financial_context"],
                instructions="Minimize raw banking exposure. Emit FinancialContext only.",
                allowed_tools=[
                    "nessie_load_customer_financials",
                    "build_financial_context",
                    "build_portfolio_financials",
                    "calculate_affordability",
                    "identify_financial_stress",
                ],
                permissions=AgentPermissions(
                    data_scopes=["nessie_raw_financial", "financial_summary"],
                    actions=["read", "calculate"],
                ),
                guardrails=["Do not expose raw transactions downstream."],
                model_policy="auto",
                budget=BudgetPolicy(max_llm_cost_usd=0.05, max_llm_calls=1),
                allowed_integrations=["nessie"],
            ),
            AgentDefinition(
                id="decision-agent",
                name="Dana",
                role="Mobility Priority Strategist",
                purpose="Rank relevant customers and explain the shortlist.",
                skills=["recommendation", "ranking", "customer_guidance"],
                instructions="Rank with CODE; explain from summaries only.",
                allowed_tools=[
                    "score_mobility_options",
                    "rank_customers",
                    "explain_shortlist",
                ],
                permissions=AgentPermissions(
                    data_scopes=["financial_summary", "vehicle_data", "lease_data", "usage_data"],
                    actions=["read", "synthesize", "recommend", "calculate"],
                ),
                guardrails=["No raw Nessie access."],
                model_policy="auto",
                budget=BudgetPolicy(max_llm_cost_usd=0.05, max_llm_calls=2),
                allowed_integrations=["meta"],
            ),
        ]
        # suggested 2 — drop context if we want minimize; keep 3 for clear roles
        if analysis.suggested_agent_count <= 2:
            agents = [agents[1], agents[2]]  # financial + decision
            # financial also needs portfolio load
            agents[0].allowed_tools = list(
                dict.fromkeys(
                    [
                        *agents[0].allowed_tools,
                        "load_toyota_portfolio",
                        "evaluate_mobility_relevance",
                    "filter_mobility_portfolio",
                    ]
                )
            )
            agents[0].permissions.data_scopes = list(
                dict.fromkeys(
                    [
                        *agents[0].permissions.data_scopes,
                        "toyota_customer_profile",
                        "vehicle_data",
                        "lease_data",
                        "usage_data",
                    ]
                )
            )
        team_name = "Mobility Portfolio Decision Team"
        summary = "Filters Toyota customers needing a mobility decision, then ranks and explains."
    else:
        # Single-customer mobility — reuse classic 4-agent shape (existing validate grants)
        from app.planner import build_fallback_team

        team = build_fallback_team(goal, max_team_size=4, work_style=style)
        team.policy = team.policy or TeamPolicy()
        team.policy.workflow_kind = WorkflowKind.MOBILITY_DECISION.value
        team.policy.scope = JobScope.SINGLE_CUSTOMER.value
        return ensure_team_runtime_config(team)

    team = TeamDefinition(
        id=f"generated-team-{uuid.uuid4().hex[:8]}",
        team_name=team_name,
        summary=summary if not goal else f"{summary} Goal: {goal[:120]}",
        agents=agents,
        policy=TeamPolicy(
            max_llm_calls=6,
            max_llm_cost_usd=0.20,
            work_style=style,
            guardrails=[
                "Never expose raw customer financial data outside the financial boundary.",
                "Never include API keys or secrets in outputs.",
            ],
            workflow_kind=analysis.workflow_kind.value,
            scope=analysis.scope.value,
        ),
    )
    return ensure_team_runtime_config(team)


def build_pack_execution_plan(
    team: TeamDefinition,
    *,
    context_source_ids: Optional[list[str]] = None,
) -> ExecutionPlan:
    """Dispatch to mobility single-customer DAG or portfolio/loan plans."""
    from app.context_sources import nessie_enabled, resolve_enabled_ids, toyota_enabled
    from app.executor import build_toyota_execution_plan

    policy = team.policy or TeamPolicy()
    kind = (policy.workflow_kind or WorkflowKind.MOBILITY_DECISION.value).lower()
    scope = (policy.scope or JobScope.SINGLE_CUSTOMER.value).lower()

    if kind == WorkflowKind.MOBILITY_DECISION.value and scope == JobScope.SINGLE_CUSTOMER.value:
        return build_toyota_execution_plan(team, context_source_ids=context_source_ids)

    enabled = resolve_enabled_ids(context_source_ids)
    if not toyota_enabled(enabled):
        raise ValueError(
            "Context source 'toyota_customer' is required for the Toyota capability pack."
        )

    team = ensure_team_runtime_config(team)
    agents = {a.id: a for a in team.agents}
    if not nessie_enabled(enabled):
        raise ValueError("Nessie banking context is required for portfolio financial decisions.")

    def assignee(action):
        from app.permissions import PermissionDenied, validate_task_permissions
        matches = []
        denied = []
        for agent in team.agents:
            if action == "explain_shortlist" and "nessie_raw_financial" in agent.permissions.data_scopes:
                continue
            try:
                validate_task_permissions(agent, action)
                matches.append(agent.id)
            except PermissionDenied as exc:
                from app.permissions import ACTION_REQUIREMENTS
                overlap = len(set(agent.allowed_tools) & set(ACTION_REQUIREMENTS[action].tools))
                denied.append((-overlap, agent.id, exc))
        if not matches:
            if denied:
                raise sorted(denied, key=lambda row: (row[0], row[1]))[0][2]
            raise ValueError(f"No agent is authorized for '{action}'.")
        return sorted(matches)[0]

    financial_id = assignee("build_portfolio_financials")
    decision_id = assignee("explain_shortlist")
    context_id = assignee("load_toyota_portfolio")
    if "nessie_raw_financial" in agents[decision_id].permissions.data_scopes:
        raise ValueError("The explanation agent cannot hold raw banking scope; review its plan-time grants.")
    if financial_id == decision_id:
        raise ValueError("Financial raw-data work and shortlist explanation require separate agents.")
    if kind == WorkflowKind.LOAN_OPPORTUNITY.value:
        tasks = [
            _task(
                task_id="load-portfolio",
                name="Load Toyota portfolio",
                agent_id=context_id,
                action="load_toyota_portfolio",
                agents=agents,
                depends_on=[],
                risk_level=RiskLevel.low,
            ),
        ]
        if nessie_enabled(enabled):
            tasks.append(
                _task(
                    task_id="portfolio-financials",
                    name="Build portfolio financial summaries",
                    agent_id=financial_id,
                    action="build_portfolio_financials",
                    agents=agents,
                    depends_on=["load-portfolio"],
                    risk_level=RiskLevel.medium,
                )
            )
            dep = ["portfolio-financials"]
        else:
            dep = ["load-portfolio"]
        tasks.extend(
            [
                _task(
                    task_id="rank-loan-candidates",
                    name="Rank loan candidates",
                    agent_id=assignee("rank_customers"),
                    action="rank_customers",
                    agents=agents,
                    depends_on=dep,
                    risk_level=RiskLevel.medium,
                ),
                _task(
                    task_id="explain-shortlist",
                    name="Explain loan shortlist",
                    agent_id=decision_id,
                    action="explain_shortlist",
                    agents=agents,
                    depends_on=["rank-loan-candidates"],
                    risk_level=RiskLevel.medium,
                ),
            ]
        )
        return ExecutionPlan(tasks=tasks)

    # Portfolio mobility
    tasks = [
        _task(
            task_id="load-portfolio",
            name="Load Toyota portfolio",
            agent_id=context_id,
            action="load_toyota_portfolio",
            agents=agents,
            depends_on=[],
            risk_level=RiskLevel.low,
        ),
        _task(
            task_id="filter-relevant",
            name="Filter mobility-relevant customers",
            agent_id=assignee("filter_mobility_portfolio"),
            action="filter_mobility_portfolio",
            agents=agents,
            depends_on=["load-portfolio"],
            risk_level=RiskLevel.low,
        ),
    ]
    if nessie_enabled(enabled):
        tasks.append(
            _task(
                task_id="portfolio-financials",
                name="Build financial summaries for relevant customers",
                agent_id=financial_id,
                action="build_portfolio_financials",
                agents=agents,
                depends_on=["filter-relevant"],
                risk_level=RiskLevel.medium,
            )
        )
        dep = ["portfolio-financials"]
    else:
        dep = ["filter-relevant"]
    tasks.extend(
        [
            _task(
                task_id="rank-mobility",
                name="Rank mobility priorities",
                agent_id=assignee("rank_customers"),
                action="rank_customers",
                agents=agents,
                depends_on=dep,
                risk_level=RiskLevel.medium,
            ),
            _task(
                task_id="explain-shortlist",
                name="Explain mobility shortlist",
                agent_id=decision_id,
                action="explain_shortlist",
                agents=agents,
                depends_on=["rank-mobility"],
                risk_level=RiskLevel.medium,
            ),
        ]
    )
    return ExecutionPlan(tasks=tasks)
