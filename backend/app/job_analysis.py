"""Job analysis before agent creation — match goals to the installed capability pack."""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

from app.capability_catalog import NOT_INSTALLED, TOYOTA_PACK_ID


class ExecutionStatus(str, Enum):
    READY = "READY"
    NEEDS_SETUP = "NEEDS_SETUP"
    NEED_MORE_INFO = "NEED_MORE_INFO"


class WorkflowKind(str, Enum):
    MOBILITY_DECISION = "mobility_decision"
    LOAN_OPPORTUNITY = "loan_opportunity"
    UNSUPPORTED = "unsupported"
    AMBIGUOUS = "ambiguous"


class JobScope(str, Enum):
    SINGLE_CUSTOMER = "single_customer"
    PORTFOLIO = "portfolio"


class WorkStepSpec(BaseModel):
    id: str
    kind: str  # NO_ACTION | CODE | TOOL | AGENT
    action: str
    purpose: str


class JobAnalysis(BaseModel):
    responsibility: str
    scope: JobScope = JobScope.SINGLE_CUSTOMER
    workflow_kind: WorkflowKind
    pack_id: Optional[str] = None
    required_work: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    required_connections: list[str] = Field(default_factory=list)
    work_steps: list[WorkStepSpec] = Field(default_factory=list)
    missing_capabilities: list[str] = Field(default_factory=list)
    follow_up_questions: list[str] = Field(default_factory=list)
    execution_status: ExecutionStatus = ExecutionStatus.READY
    suggested_agent_count: int = 0
    notes: list[str] = Field(default_factory=list)


def _blob(goal: str) -> str:
    return (goal or "").strip().lower()


def _detect_unsupported(goal: str) -> list[str]:
    """Return missing capability labels when the goal needs packs we do not have."""
    g = _blob(goal)
    missing: list[str] = []
    recruiting = any(t in g for t in ("recruit", "hiring", "interview", "ats", "resume", "hr team")) or ("candidate" in g and "loan" not in g and "vehicle" not in g)
    if recruiting:
        missing.append("ATS")
    if any(t in g for t in ("send email", "email them", "email candidates", "emails them", "outreach email")):
        missing.append("email")
    if any(t in g for t in ("schedule interview", "schedules interview", "calendar", "schedules interviews")):
        missing.append("calendar")
    if any(t in g for t in ("slack", "jira", "salesforce crm", "shopify")):
        missing.append("requested external SaaS connector (not installed)")
    return missing


def _toyota_sources_enabled(context_source_ids: Optional[list[str]]) -> bool:
    ids = {str(s).lower() for s in (context_source_ids or [])}
    return any(
        s.startswith("toyota") or s in {"nessie_banking", "nessie", "vehicle_data", "lease_data", "usage_data"}
        for s in ids
    )


def _detect_workflow(
    goal: str,
    answers: dict[str, str],
    *,
    context_source_ids: Optional[list[str]] = None,
) -> WorkflowKind:
    g = _blob(goal)
    intent = (answers.get("intent") or answers.get("workflow") or "").lower()

    if intent in {"loan", "loan_opportunity", "lending"}:
        return WorkflowKind.LOAN_OPPORTUNITY
    if intent in {"mobility", "mobility_decision", "lease"}:
        return WorkflowKind.MOBILITY_DECISION

    loan_hits = sum(
        1
        for t in (
            "loan",
            "lending",
            "finance a new",
            "new vehicle loan",
            "creditworthy",
            "target for a",
            "strongest customers",
            "shortlist",
            "rank customers",
            "portfolio lend",
            "loan opportunit",
        )
        if t in g
    )
    mobility_hits = sum(
        1
        for t in (
            "mobility",
            "lease",
            "buyout",
            "return the vehicle",
            "return or",
            "buy, return",
            "buy or return",
            "next step",
            "ownership",
            "decision window",
            "who currently need",
            "recommend the best next",
            "next mobility",
            "smartest next",
            "buy current",
            "upgrade their vehicle",
            "vehicle decision",
            "contract",
            "usage",
        )
        if t in g
    )

    if loan_hits >= 1 and mobility_hits == 0:
        return WorkflowKind.LOAN_OPPORTUNITY
    if mobility_hits >= 1 and loan_hits == 0:
        return WorkflowKind.MOBILITY_DECISION
    if loan_hits >= 1 and mobility_hits >= 1:
        return WorkflowKind.AMBIGUOUS
    # Demo UI enables Toyota/Nessie sources for the installed pack. Prefer the
    # runnable mobility workflow over a dead-end "unsupported" assemble error.
    if _toyota_sources_enabled(context_source_ids) and (
        "toyota" in g or "customer" in g or "vehicle" in g or "decide" in g or "recommend" in g
    ):
        return WorkflowKind.MOBILITY_DECISION
    if "toyota" in g and ("customer" in g or "vehicle" in g or "mobility" in g):
        return WorkflowKind.MOBILITY_DECISION
    if "toyota" in g or "customer" in g or "vehicle" in g:
        return WorkflowKind.AMBIGUOUS
    return WorkflowKind.UNSUPPORTED


def _detect_scope(goal: str, answers: dict[str, str]) -> JobScope:
    explicit = (answers.get("scope") or "").lower()
    if explicit in {"portfolio", "all", "many"}:
        return JobScope.PORTFOLIO
    if explicit in {"single", "one", "single_customer"}:
        return JobScope.SINGLE_CUSTOMER
    g = _blob(goal)
    if any(
        t in g
        for t in (
            "customers who",
            "portfolio",
            "all customers",
            "rank",
            "strongest customers",
            "identify toyota customers",
            "find the strongest",
            "which customers",
        )
    ):
        return JobScope.PORTFOLIO
    return JobScope.SINGLE_CUSTOMER


def _mobility_steps(scope: JobScope) -> list[WorkStepSpec]:
    steps: list[WorkStepSpec] = []
    if scope == JobScope.PORTFOLIO:
        steps.append(
            WorkStepSpec(
                id="load-portfolio",
                kind="TOOL",
                action="load_toyota_portfolio",
                purpose="Load installed Toyota demo customers",
            )
        )
        steps.append(
            WorkStepSpec(
                id="filter-relevant",
                kind="CODE",
                action="evaluate_mobility_relevance",
                purpose="Deterministic relevance filter per customer (may yield NO_ACTION)",
            )
        )
    else:
        steps.append(
            WorkStepSpec(
                id="load-customer",
                kind="TOOL",
                action="load_toyota_customer",
                purpose="Load one Toyota customer context",
            )
        )
        steps.append(
            WorkStepSpec(
                id="relevance",
                kind="CODE",
                action="evaluate_mobility_relevance",
                purpose="Cheap relevance gate before Nessie/LLM",
            )
        )
    steps.extend(
        [
            WorkStepSpec(
                id="nessie",
                kind="TOOL",
                action="nessie_load_customer_financials",
                purpose="Fetch banking data when relevant (named grant only)",
            ),
            WorkStepSpec(
                id="financial",
                kind="CODE",
                action="build_financial_context",
                purpose="Derive FinancialContext — hide raw rows",
            ),
            WorkStepSpec(
                id="afford",
                kind="CODE",
                action="calculate_affordability",
                purpose="Affordability / stress signals",
            ),
            WorkStepSpec(
                id="score",
                kind="CODE",
                action="score_mobility_options",
                purpose="Score buy/upgrade/return/flexible",
            ),
        ]
    )
    if scope == JobScope.PORTFOLIO:
        steps.append(
            WorkStepSpec(
                id="rank",
                kind="CODE",
                action="rank_customers",
                purpose="Rank customers needing a mobility decision",
            )
        )
        steps.append(
            WorkStepSpec(
                id="explain",
                kind="AGENT",
                action="explain_shortlist",
                purpose="Short LLM narrative over ranked summaries only",
            )
        )
    else:
        steps.append(
            WorkStepSpec(
                id="synthesize",
                kind="AGENT",
                action="synthesize_recommendation",
                purpose="Customer-facing recommendation from structured summaries",
            )
        )
    return steps


def _loan_steps(scope: JobScope) -> list[WorkStepSpec]:
    steps = [
        WorkStepSpec(
            id="load-portfolio",
            kind="TOOL",
            action="load_toyota_portfolio",
            purpose="Load Toyota customer portfolio",
        ),
        WorkStepSpec(
            id="nessie",
            kind="TOOL",
            action="nessie_load_customer_financials",
            purpose="Financial data per customer (derived only downstream)",
        ),
        WorkStepSpec(
            id="financial",
            kind="CODE",
            action="build_financial_context",
            purpose="FinancialContext per customer",
        ),
        WorkStepSpec(
            id="afford",
            kind="CODE",
            action="calculate_affordability",
            purpose="Loan affordability / risk signals",
        ),
        WorkStepSpec(
            id="stress",
            kind="CODE",
            action="identify_financial_stress",
            purpose="Classify stress for ranking",
        ),
        WorkStepSpec(
            id="rank",
            kind="CODE",
            action="rank_customers",
            purpose="Rank strongest loan candidates (CODE before LLM)",
        ),
        WorkStepSpec(
            id="explain",
            kind="AGENT",
            action="explain_shortlist",
            purpose="Explain shortlist from minimized summaries",
        ),
    ]
    if scope == JobScope.SINGLE_CUSTOMER:
        # Still portfolio functions but focused narrative
        steps[0] = WorkStepSpec(
            id="load-customer",
            kind="TOOL",
            action="load_toyota_customer",
            purpose="Load one customer for loan fitness",
        )
    return steps


def analyze_job(
    goal: str,
    *,
    context_source_ids: Optional[list[str]] = None,
    answers: Optional[dict[str, str]] = None,
) -> JobAnalysis:
    answers = {k: str(v) for k, v in (answers or {}).items()}
    goal = (goal or "").strip()
    missing_external = _detect_unsupported(goal)

    if missing_external:
        return JobAnalysis(
            responsibility=goal or "Unsupported responsibility",
            workflow_kind=WorkflowKind.UNSUPPORTED,
            pack_id=None,
            required_work=["design recruiting/outreach workflow"],
            required_capabilities=[m.lower() for m in missing_external],
            required_connections=[m.lower() for m in missing_external],
            missing_capabilities=missing_external,
            follow_up_questions=[],
            execution_status=ExecutionStatus.NEEDS_SETUP,
            suggested_agent_count=0,
            notes=[
                "Responsibility can be described, but this environment lacks the required pack.",
                "No fake Run will be started.",
            ],
        )

    workflow = _detect_workflow(goal, answers, context_source_ids=context_source_ids)
    scope = _detect_scope(goal, answers)

    if workflow == WorkflowKind.AMBIGUOUS:
        # With Toyota context selected, assemble the default single-customer
        # mobility team instead of blocking the Create form on follow-ups.
        if _toyota_sources_enabled(context_source_ids):
            workflow = WorkflowKind.MOBILITY_DECISION
            scope = JobScope.SINGLE_CUSTOMER
        else:
            questions = [
                "Is this a mobility / lease decision, or a vehicle-loan targeting opportunity?",
            ]
            if "scope" not in answers:
                questions.append("Should this evaluate one customer or the full Toyota demo portfolio?")
            if len(questions) < 3:
                questions.append("Should financial affordability from Nessie be required?")
            return JobAnalysis(
                responsibility=goal,
                scope=scope,
                workflow_kind=WorkflowKind.AMBIGUOUS,
                pack_id=TOYOTA_PACK_ID,
                follow_up_questions=questions[:4],
                execution_status=ExecutionStatus.NEED_MORE_INFO,
                suggested_agent_count=0,
                notes=["Ambiguous goal — answer follow-ups before assembling agents."],
            )

    if workflow == WorkflowKind.UNSUPPORTED:
        return JobAnalysis(
            responsibility=goal,
            workflow_kind=WorkflowKind.UNSUPPORTED,
            missing_capabilities=[
                "Toyota mobility decision pack or vehicle-loan targeting pack",
            ],
            follow_up_questions=[
                "Rephrase as a Toyota mobility / lease decision, or a vehicle-loan shortlist.",
                "Enable Toyota customer context, then use the sample mobility goal.",
            ],
            execution_status=ExecutionStatus.NEEDS_SETUP,
            notes=["Goal does not match the installed Toyota Mobility pack."],
        )

    if workflow == WorkflowKind.MOBILITY_DECISION:
        steps = _mobility_steps(scope)
        agent_count = 3 if scope == JobScope.PORTFOLIO else 4
        # Minimize: portfolio narrative needs fewer specialists
        if scope == JobScope.PORTFOLIO:
            agent_count = 2
        return JobAnalysis(
            responsibility=goal,
            scope=scope,
            workflow_kind=workflow,
            pack_id=TOYOTA_PACK_ID,
            required_work=[
                "Detect customers needing a mobility decision",
                "Derive financial safety context when relevant",
                "Score options and recommend next step",
            ],
            required_capabilities=[
                "toyota_customer_profiles",
                "vehicle_context",
                "contract_lease_context",
                "usage_data",
                "nessie_banking",
                "fn_evaluate_mobility_relevance",
                "fn_build_financial_context",
                "fn_score_mobility_options",
                "ai_synthesize_recommendation"
                if scope == JobScope.SINGLE_CUSTOMER
                else "ai_explain_shortlist",
            ],
            required_connections=["toyota_customer_profiles", "nessie_banking"],
            work_steps=steps,
            missing_capabilities=[],
            follow_up_questions=[],
            execution_status=ExecutionStatus.READY,
            suggested_agent_count=agent_count,
            notes=[
                "CODE relevance gate can stop at NO_ACTION before Nessie/LLM.",
                "Agents only where judgment adds value (synthesis / shortlist).",
            ],
        )

    # loan_opportunity
    steps = _loan_steps(JobScope.PORTFOLIO if scope == JobScope.PORTFOLIO else JobScope.PORTFOLIO)
    return JobAnalysis(
        responsibility=goal,
        scope=JobScope.PORTFOLIO,  # loan targeting is portfolio-native
        workflow_kind=WorkflowKind.LOAN_OPPORTUNITY,
        pack_id=TOYOTA_PACK_ID,
        required_work=[
            "Load portfolio",
            "Build financial summaries",
            "Rank loan candidates deterministically",
            "Explain shortlist",
        ],
        required_capabilities=[
            "toyota_customer_profiles",
            "nessie_banking",
            "fn_build_financial_context",
            "fn_calculate_affordability",
            "fn_identify_financial_stress",
            "fn_rank_customers",
            "ai_explain_shortlist",
        ],
        required_connections=["toyota_customer_profiles", "nessie_banking"],
        work_steps=steps,
        missing_capabilities=[],
        follow_up_questions=[],
        execution_status=ExecutionStatus.READY,
        suggested_agent_count=2,
        notes=[
            "Ranking is CODE; LLM only sees minimized candidate summaries.",
            "Same Toyota/Nessie pack as mobility — different assembled workflow.",
        ],
    )


def parse_planning_answers(raw: Optional[dict[str, Any]]) -> dict[str, str]:
    if not raw:
        return {}
    return {str(k): str(v) for k, v in raw.items() if v is not None}
