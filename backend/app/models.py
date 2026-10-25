"""Pydantic models matching API_SPEC.md."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# --- Enums (API_SPEC §2) ---


class ExecutorType(str, Enum):
    CODE = "CODE"
    TOOL = "TOOL"
    LLM = "LLM"


class RunStatus(str, Enum):
    PLANNING = "PLANNING"
    RUNNING = "RUNNING"
    REVIEWING = "REVIEWING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    BUDGET_LIMIT = "BUDGET_LIMIT"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    NO_ACTION_NEEDED = "NO_ACTION_NEEDED"


class TaskStatus(str, Enum):
    WAITING = "WAITING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    BLOCKED = "BLOCKED"


class PermissionDecision(str, Enum):
    ALLOWED = "ALLOWED"
    DENIED = "DENIED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"


class DataSource(str, Enum):
    NESSIE_LIVE = "NESSIE_LIVE"
    NESSIE_FALLBACK = "NESSIE_FALLBACK"
    TOYOTA_SYNTHETIC = "TOYOTA_SYNTHETIC"
    LOCAL_CODE = "LOCAL_CODE"
    LLM = "LLM"
    CACHE = "CACHE"


class QualityMode(str, Enum):
    normal = "normal"
    strict = "strict"


class RiskLevel(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class PlannerSource(str, Enum):
    LLM = "LLM"
    FALLBACK = "FALLBACK"


class EvaluatorStatus(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


# --- Shared models (API_SPEC §3) ---


class BudgetPolicy(BaseModel):
    max_llm_cost_usd: float
    max_llm_calls: int


class AgentPermissions(BaseModel):
    data_scopes: list[str]
    actions: list[str]


class AgentDefinition(BaseModel):
    id: str
    name: str
    role: str
    purpose: str
    skills: list[str]
    allowed_tools: list[str]
    permissions: AgentPermissions
    budget: BudgetPolicy
    instructions: str = ""
    guardrails: list[str] = Field(default_factory=list)
    model_policy: str = "auto"
    estimated_cost_usd: float = 0.0
    # Named integration grants only (e.g. "nessie", "meta"). Secret values never stored here.
    allowed_integrations: list[str] = Field(default_factory=list)


class TeamPolicy(BaseModel):
    """Optional team-level ceiling and work style. Agents may be stricter."""

    max_llm_calls: int = 6
    max_llm_cost_usd: float = 0.20
    work_style: str = "balanced"  # fast | balanced | thorough
    guardrails: list[str] = Field(default_factory=list)
    # Assembled from installed capability pack (toyota mobility / loan).
    workflow_kind: str = "mobility_decision"
    scope: str = "single_customer"


class TeamDefinition(BaseModel):
    id: str
    team_name: str
    summary: str
    agents: list[AgentDefinition]
    policy: Optional[TeamPolicy] = None


class TaskDefinition(BaseModel):
    id: str
    name: str
    assigned_agent_id: str
    executor: ExecutorType
    action: str
    depends_on: list[str] = Field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.medium


class ExecutionPlan(BaseModel):
    tasks: list[TaskDefinition]


class FinancialContext(BaseModel):
    monthly_income: float
    fixed_expenses: float
    variable_spending: float
    monthly_free_cashflow: float
    cash_reserve: float
    safe_mobility_budget: float
    emergency_runway_months: float
    debt_burden_ratio: float
    financial_stress: str


class RecommendationOption(BaseModel):
    id: str
    label: str
    monthly_cost: float
    three_year_cost: float
    fit_score: int
    safe_for_budget: bool
    tradeoffs: list[str]


class NextStep(BaseModel):
    label: str
    action_type: str


class ToyotaRecommendation(BaseModel):
    recommended_action: str
    confidence: float
    headline: str
    summary: str
    reasons: list[str]
    assumptions: list[str]
    next_step: NextStep
    options: list[RecommendationOption] = Field(default_factory=list)


class AuditEvent(BaseModel):
    id: str
    timestamp: str
    run_id: str
    agent_id: Optional[str] = None
    event_type: str
    executor_type: Optional[ExecutorType] = None
    resource: Optional[str] = None
    result: Optional[str] = None
    message: str
    cost_usd: float = 0.0


class EvaluatorResult(BaseModel):
    name: str
    label: str
    status: EvaluatorStatus
    details: str


# --- Health (API_SPEC §4) ---


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str


class DemoSessionUser(BaseModel):
    id: str
    name: str
    organization: str
    role: str


class DemoSessionResponse(BaseModel):
    user: DemoSessionUser
    workspace_id: str


# --- Templates (API_SPEC §6) ---


class Template(BaseModel):
    id: str
    name: str
    description: str
    category: str
    is_demo: bool


class TemplatesResponse(BaseModel):
    templates: list[Template]


# --- Team planning (API_SPEC §7) ---


class PlanTeamRequest(BaseModel):
    goal: str
    max_team_size: int = 5
    budget_preference: str = "balanced"
    attachment_ids: list[str] = Field(default_factory=list)
    context_source_ids: list[str] = Field(default_factory=list)
    planning_answers: dict[str, str] = Field(default_factory=dict)


class JobAnalysisModel(BaseModel):
    responsibility: str
    scope: str = "single_customer"
    workflow_kind: str
    pack_id: Optional[str] = None
    required_work: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    required_connections: list[str] = Field(default_factory=list)
    work_steps: list[dict] = Field(default_factory=list)
    missing_capabilities: list[str] = Field(default_factory=list)
    follow_up_questions: list[str] = Field(default_factory=list)
    execution_status: str = "READY"
    suggested_agent_count: int = 0
    notes: list[str] = Field(default_factory=list)


class PlanTeamResponse(BaseModel):
    team: Optional[TeamDefinition] = None
    planner_source: PlannerSource
    fallback_used: bool
    job_analysis: Optional[JobAnalysisModel] = None
    execution_status: str = "READY"


class SaveTeamRequest(BaseModel):
    team: TeamDefinition
    goal: str = ""


class SavedTeamSummary(BaseModel):
    id: str
    name: str
    purpose: str
    member_ids: list[str]
    estimated_cost_usd: float = 0.0
    last_run_status: str = "NEVER"


class SavedTeamsResponse(BaseModel):
    teams: list[SavedTeamSummary]


class SavedTeamDetailResponse(BaseModel):
    summary: SavedTeamSummary
    team: TeamDefinition


class AttachmentMeta(BaseModel):
    id: str
    filename: str
    content_type: str
    size_bytes: int
    created_at: str
    has_text_excerpt: bool


class AttachmentListResponse(BaseModel):
    attachments: list[AttachmentMeta]


# --- Demo customers (API_SPEC §8) ---


class DemoCustomerSummary(BaseModel):
    id: str
    name: str
    vehicle: str
    scenario_label: str
    description: str


class DemoCustomersResponse(BaseModel):
    customers: list[DemoCustomerSummary]


class VehicleInfo(BaseModel):
    year: int
    make: str
    model: str
    mileage: int


class ContractInfo(BaseModel):
    type: str
    months_remaining: int
    monthly_payment: float
    buyout_amount: float


class UsageInfo(BaseModel):
    monthly_miles_current: int
    monthly_miles_six_months_ago: int
    change_percent: float


class DemoCustomerDetail(BaseModel):
    id: str
    name: str
    vehicle: VehicleInfo
    contract: ContractInfo
    usage: UsageInfo
    nessie_customer_id: str


# --- Errors (API_SPEC §15) ---


class ErrorDetail(BaseModel):
    code: str
    message: str
    recoverable: bool = True


class ErrorResponse(BaseModel):
    error: ErrorDetail


# --- Runs (API_SPEC §9–11) ---


class RunContext(BaseModel):
    customer_id: str = ""
    attachment_ids: list[str] = Field(default_factory=list)
    context_source_ids: list[str] = Field(default_factory=list)


class CreateRunRequest(BaseModel):
    goal: str
    team: TeamDefinition
    context: RunContext
    quality_mode: QualityMode = QualityMode.normal
    attachment_ids: list[str] = Field(default_factory=list)
    context_source_ids: list[str] = Field(default_factory=list)


class CreateRunResponse(BaseModel):
    run_id: str
    status: RunStatus


class RunListItem(BaseModel):
    run_id: str
    goal: str
    team_name: str
    status: RunStatus
    result: Optional[str] = None
    date: str
    duration_ms: Optional[int] = None
    estimated_ai_cost_usd: float = 0.0
    llm_calls: int = 0
    llm_calls_avoided: int = 0


class RunsListResponse(BaseModel):
    runs: list[RunListItem]


class IntegrationStatusItem(BaseModel):
    id: str
    label: str
    status: str


class IntegrationsResponse(BaseModel):
    integrations: list[IntegrationStatusItem]


class AuditEventListItem(BaseModel):
    id: str
    timestamp: str
    run_id: str
    agent_id: Optional[str] = None
    event_type: str
    executor_type: Optional[str] = None
    resource: Optional[str] = None
    result: Optional[str] = None
    message: str
    cost_usd: float = 0.0


class AuditEventsResponse(BaseModel):
    events: list[AuditEventListItem]


class RunTaskState(BaseModel):
    id: str
    name: str
    assigned_agent_id: str
    executor: ExecutorType
    action: str
    status: TaskStatus
    activity_summary: str = ""
    tool: Optional[str] = None
    duration_ms: Optional[int] = None
    cost_usd: float = 0.0
    data_source: Optional[str] = None
    depends_on: list[str] = Field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.medium


class RunMetrics(BaseModel):
    llm_calls: int = 0
    llm_calls_avoided: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_ai_cost_usd: float = 0.0
    estimated_baseline_cost_usd: float = 0.0
    estimated_cost_saved_usd: float = 0.0
    code_tasks: int = 0
    tool_tasks: int = 0
    llm_tasks: int = 0
    routing_decisions: int = 0
    agent_llm_calls: dict[str, int] = Field(default_factory=dict)
    agent_llm_cost: dict[str, float] = Field(default_factory=dict)
    work_style: str = "balanced"
    last_model_tier: Optional[str] = None
    model_tiers_used: list[str] = Field(default_factory=list)


class ContextSourceItem(BaseModel):
    id: str
    type: str
    label: str
    status: str
    dismissible: bool = True
    description: str = ""
    executor_hint: Optional[str] = None
    attachment_id: Optional[str] = None


class ContextSourcesResponse(BaseModel):
    sources: list[ContextSourceItem]


class MobilityTriggerRequest(BaseModel):
    customer_id: str
    start_run_if_relevant: bool = False
    team_id: Optional[str] = None


class MobilityTriggerResponse(BaseModel):
    customer_id: str
    relevant: bool
    reasons: list[str]
    run_id: Optional[str] = None


class OutputItem(BaseModel):
    id: str
    title: str
    agent_id: Optional[str] = None
    type: str
    summary: str
    content: str
    timestamp: str
    cost_usd: float = 0.0
    source_type: str = "CODE"


class PortfolioResult(BaseModel):
    purpose: str
    candidates: list[dict] = Field(default_factory=list)
    skipped_customers: list[dict] = Field(default_factory=list)
    narrative: str = ""


class RunStatusResponse(BaseModel):
    run_id: str
    status: RunStatus
    goal: str
    created_at: str
    updated_at: str
    plan: ExecutionPlan
    tasks: list[RunTaskState]
    metrics: RunMetrics
    financial_context: Optional[FinancialContext] = None
    recommendation: Optional[ToyotaRecommendation] = None
    portfolio_result: Optional[PortfolioResult] = None
    outputs: list[OutputItem] = Field(default_factory=list)
    evaluators: list[EvaluatorResult] = Field(default_factory=list)
    audit_events: list[AuditEvent] = Field(default_factory=list)
