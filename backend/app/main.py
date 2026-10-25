"""QuestMaster FastAPI application — Phase 1 + Phase 2 run execution."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import app.db as db
from app.attachments import (
    delete_attachment,
    get_attachment,
    list_attachments,
    save_upload,
)
from app.db import init_db
from app.context_sources import catalog as context_sources_catalog
from app.executor import build_toyota_execution_plan, execute_run
from app.models import (
    AttachmentListResponse,
    AttachmentMeta,
    AuditEventsResponse,
    ContextSourceItem,
    ContextSourcesResponse,
    CreateRunRequest,
    CreateRunResponse,
    DemoCustomerDetail,
    DemoCustomerSummary,
    DemoCustomersResponse,
    DemoSessionResponse,
    DemoSessionUser,
    ErrorDetail,
    ErrorResponse,
    HealthResponse,
    IntegrationStatusItem,
    IntegrationsResponse,
    MobilityTriggerRequest,
    MobilityTriggerResponse,
    PlanTeamRequest,
    PlanTeamResponse,
    QualityMode,
    RunContext,
    RunStatus,
    RunStatusResponse,
    RunsListResponse,
    SavedTeamDetailResponse,
    SavedTeamSummary,
    SavedTeamsResponse,
    SaveTeamRequest,
    Template,
    TemplatesResponse,
)
from app.permissions import PermissionDenied
from app.planner import build_fallback_team, plan_team as plan_team_orchestrator
from app.run_store import (
    create_run_record,
    get_run,
    get_run_audit_events,
    list_audit_events,
    list_runs,
)
from app.services.llm_service import get_llm_service
from app.team_store import delete_saved_team, get_saved_team, list_saved_teams, save_team
from app.tools.deterministic import evaluate_mobility_relevance
from app.tools.nessie import get_nessie_client
from app.tools.toyota import load_toyota_customer, load_toyota_customers

# Repo-root .env (HackTX2026/.env), not backend/.env
_REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_REPO_ROOT / ".env")
load_dotenv()

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="QuestMaster API", version="0.2.0", lifespan=lifespan)

_cors_origins = [
    o.strip()
    for o in (os.getenv("CORS_ORIGINS") or "http://localhost:5173").split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _error(status_code: int, code: str, message: str, recoverable: bool = True) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorDetail(code=code, message=message, recoverable=recoverable)
    )
    return JSONResponse(status_code=status_code, content=body.model_dump())


@app.exception_handler(HTTPException)
async def http_exception_handler(_request: Request, exc: HTTPException):
    if isinstance(exc.detail, dict) and "code" in exc.detail:
        return _error(
            exc.status_code,
            exc.detail["code"],
            exc.detail.get("message", str(exc.detail)),
            exc.detail.get("recoverable", True),
        )
    return _error(exc.status_code, "INVALID_REQUEST", str(exc.detail))


@app.get("/api/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        service="questmaster-api",
        version="0.2.0",
    )


@app.post("/api/demo-session", response_model=DemoSessionResponse)
async def demo_session() -> DemoSessionResponse:
    """Hackathon demo session — no real auth."""
    return DemoSessionResponse(
        user=DemoSessionUser(
            id=os.getenv("DEFAULT_DEMO_USER_ID") or "demo-user",
            name="Alex Morgan",
            organization="Toyota Financial Services",
            role="Team Builder",
        ),
        workspace_id=os.getenv("DEFAULT_WORKSPACE_ID") or "toyota-demo",
    )


@app.get("/api/templates", response_model=TemplatesResponse)
async def templates() -> TemplatesResponse:
    return TemplatesResponse(
        templates=[
            Template(
                id="toyota-mobility",
                name="Toyota Mobility Decision Team",
                description="Proactively identifies the customer's smartest mobility decision.",
                category="Mobility",
                is_demo=True,
            ),
            Template(
                id="customer-support",
                name="Customer Support Team",
                description="Example reusable business template.",
                category="Operations",
                is_demo=False,
            ),
        ]
    )


@app.get("/api/demo/customers", response_model=DemoCustomersResponse)
async def list_demo_customers() -> DemoCustomersResponse:
    customers = load_toyota_customers()
    summaries = []
    for c in customers:
        vehicle = c["vehicle"]
        vehicle_label = f"{vehicle['year']} {vehicle['make']} {vehicle['model']}"
        summaries.append(
            DemoCustomerSummary(
                id=c["id"],
                name=c["name"],
                vehicle=vehicle_label,
                scenario_label=c["scenario_label"],
                description=c["description"],
            )
        )
    return DemoCustomersResponse(customers=summaries)


@app.get("/api/demo/customers/{customer_id}", response_model=DemoCustomerDetail)
async def get_demo_customer(customer_id: str) -> DemoCustomerDetail:
    customers = load_toyota_customers()
    match = next((c for c in customers if c["id"] == customer_id), None)
    if match is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "NOT_FOUND",
                "message": f"Customer '{customer_id}' not found.",
                "recoverable": True,
            },
        )
    return DemoCustomerDetail(
        id=match["id"],
        name=match["name"],
        vehicle=match["vehicle"],
        contract=match["contract"],
        usage=match["usage"],
        nessie_customer_id=match["nessie_customer_id"],
    )


@app.post("/api/teams/plan", response_model=PlanTeamResponse)
async def plan_team(request: PlanTeamRequest) -> PlanTeamResponse:
    return await plan_team_orchestrator(request)


@app.get("/api/teams", response_model=SavedTeamsResponse)
async def list_teams() -> SavedTeamsResponse:
    """List teams persisted in Tiger (or in-memory when DB is unavailable)."""
    return SavedTeamsResponse(teams=list_saved_teams())


@app.post("/api/teams", response_model=SavedTeamSummary, status_code=201)
async def create_or_update_team(request: SaveTeamRequest) -> SavedTeamSummary:
    if not request.team.agents:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_REQUEST",
                "message": "Team must include at least one agent.",
                "recoverable": True,
            },
        )
    return save_team(request.team, goal=request.goal)


@app.get("/api/teams/{team_id}", response_model=SavedTeamDetailResponse)
async def get_team(team_id: str) -> SavedTeamDetailResponse:
    found = get_saved_team(team_id)
    if found is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "NOT_FOUND",
                "message": f"Team '{team_id}' not found.",
                "recoverable": True,
            },
        )
    summary, team = found
    return SavedTeamDetailResponse(summary=summary, team=team)


@app.delete("/api/teams/{team_id}", status_code=204)
async def remove_team(team_id: str) -> None:
    if not delete_saved_team(team_id):
        raise HTTPException(
            status_code=404,
            detail={
                "code": "NOT_FOUND",
                "message": f"Team '{team_id}' not found.",
                "recoverable": True,
            },
        )
    return None


def _attachment_meta(rec) -> AttachmentMeta:
    return AttachmentMeta(
        id=rec.id,
        filename=rec.filename,
        content_type=rec.content_type,
        size_bytes=rec.size_bytes,
        created_at=rec.created_at,
        has_text_excerpt=bool(rec.text_excerpt),
    )


@app.post("/api/attachments", response_model=AttachmentMeta, status_code=201)
async def upload_attachment(file: UploadFile = File(...)) -> AttachmentMeta:
    """Accept a demo context file for planning/runs. Returns metadata only (no raw bytes)."""
    data = await file.read()
    try:
        rec = save_upload(
            filename=file.filename or "upload.bin",
            content_type=file.content_type or "application/octet-stream",
            data=data,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_REQUEST",
                "message": str(exc),
                "recoverable": True,
            },
        ) from exc
    return _attachment_meta(rec)


@app.get("/api/attachments", response_model=AttachmentListResponse)
async def list_uploaded_attachments() -> AttachmentListResponse:
    return AttachmentListResponse(
        attachments=[_attachment_meta(r) for r in list_attachments()]
    )


@app.get("/api/attachments/{attachment_id}", response_model=AttachmentMeta)
async def get_uploaded_attachment(attachment_id: str) -> AttachmentMeta:
    rec = get_attachment(attachment_id)
    if rec is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "NOT_FOUND",
                "message": f"Attachment '{attachment_id}' not found.",
                "recoverable": True,
            },
        )
    return _attachment_meta(rec)


@app.delete("/api/attachments/{attachment_id}", status_code=204)
async def remove_attachment(attachment_id: str) -> None:
    if not delete_attachment(attachment_id):
        raise HTTPException(
            status_code=404,
            detail={
                "code": "NOT_FOUND",
                "message": f"Attachment '{attachment_id}' not found.",
                "recoverable": True,
            },
        )
    return None


@app.get("/api/context-sources", response_model=ContextSourcesResponse)
async def list_context_sources() -> ContextSourcesResponse:
    """Built-in + uploaded file context sources (no secrets)."""
    return ContextSourcesResponse(
        sources=[ContextSourceItem.model_validate(s) for s in context_sources_catalog()]
    )


@app.post("/api/triggers/mobility/evaluate", response_model=MobilityTriggerResponse)
async def mobility_trigger_evaluate(
    request: MobilityTriggerRequest,
    background: BackgroundTasks,
) -> MobilityTriggerResponse:
    """
    Trigger-ready relevance check for the reusable Toyota Mobility Decision Team.

    Intended integration point for a future scheduled scan, CRM event, or lease-maturity
    webhook. This prototype does NOT run a continuous monitor or cron worker.
    """
    try:
        toyota = load_toyota_customer(request.customer_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "NOT_FOUND",
                "message": f"Customer '{request.customer_id}' not found.",
                "recoverable": True,
            },
        ) from exc

    relevance = evaluate_mobility_relevance(toyota)
    run_id: str | None = None

    if request.start_run_if_relevant and relevance.get("relevant"):
        team = None
        if request.team_id:
            found = get_saved_team(request.team_id)
            if found is None:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "code": "NOT_FOUND",
                        "message": f"Team '{request.team_id}' not found.",
                        "recoverable": True,
                    },
                )
            team = found[1]
        else:
            team = build_fallback_team(
                "Triggered mobility decision evaluation",
                max_team_size=5,
            )

        run_req = CreateRunRequest(
            goal="Triggered mobility decision evaluation",
            team=team,
            context=RunContext(customer_id=request.customer_id),
            quality_mode=QualityMode.normal,
        )
        try:
            plan = build_toyota_execution_plan(run_req.team)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail={"code": "INVALID_REQUEST", "message": str(exc), "recoverable": True},
            ) from exc
        run = create_run_record(run_req, plan)
        background.add_task(execute_run, run.run_id, run_req)
        run_id = run.run_id

    return MobilityTriggerResponse(
        customer_id=request.customer_id,
        relevant=bool(relevance.get("relevant")),
        reasons=list(relevance.get("reasons") or []),
        run_id=run_id,
    )


@app.post("/api/runs", response_model=CreateRunResponse, status_code=202)
async def create_run(request: CreateRunRequest, background: BackgroundTasks) -> CreateRunResponse:
    # Validate customer exists
    customers = load_toyota_customers()
    if not (request.team.policy and request.team.policy.scope == "portfolio") and not any(c["id"] == request.context.customer_id for c in customers):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_REQUEST",
                "message": "Customer ID is required and must be a known demo customer.",
                "recoverable": True,
            },
        )
    if not request.team.agents:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_REQUEST",
                "message": "Team must include at least one agent.",
                "recoverable": True,
            },
        )

    from app.workflow_support import require_supported_responsibility, require_supported_team

    analysis = require_supported_responsibility(request.goal)
    require_supported_team(request.team)
    policy = request.team.policy
    if analysis.execution_status.value == "READY" and policy and (
        policy.workflow_kind != analysis.workflow_kind.value or policy.scope != analysis.scope.value
    ):
        raise HTTPException(status_code=400, detail={"code": "NEEDS_SETUP", "message": "The responsibility changed. Assemble a team for the new workflow before running.", "recoverable": True})

    # Merge top-level + context attachment ids; reject unknown ids.
    att_ids = list(
        dict.fromkeys([*(request.attachment_ids or []), *(request.context.attachment_ids or [])])
    )
    missing = [aid for aid in att_ids if get_attachment(aid) is None]
    if missing:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_REQUEST",
                "message": f"Unknown attachment id(s): {', '.join(missing)}",
                "recoverable": True,
            },
        )
    if att_ids:
        request.context.attachment_ids = att_ids
        request.attachment_ids = att_ids

    source_ids = list(
        dict.fromkeys(
            [*(request.context_source_ids or []), *(request.context.context_source_ids or [])]
        )
    )
    if source_ids:
        request.context_source_ids = source_ids
        request.context.context_source_ids = source_ids

    from app.executor import mobility_relevant
    from app.team_normalize import ensure_team_runtime_config

    request.team = ensure_team_runtime_config(request.team)
    try:
        from app.pack_workflows import build_pack_execution_plan
        portfolio = request.team.policy and request.team.policy.scope == "portfolio"
        if portfolio:
            plan = build_pack_execution_plan(request.team, context_source_ids=source_ids or None)
        else:
            plan = build_toyota_execution_plan(request.team, context_source_ids=source_ids or None, include_downstream=mobility_relevant(request.context.customer_id))
    except PermissionDenied as exc:
        raise HTTPException(status_code=400, detail={"code": "PERMISSION_DENIED", "message": exc.message, "recoverable": True}) from exc
    except (ValueError, Exception) as exc:
        from app.routing import RoutingError

        if isinstance(exc, (ValueError, RoutingError)):
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "INVALID_REQUEST",
                    "message": str(exc),
                    "recoverable": True,
                },
            ) from exc
        raise

    from app.permissions import validate_plan_permissions

    agents = {a.id: a for a in request.team.agents}
    perm_err = validate_plan_permissions(agents, plan.tasks)
    if perm_err is not None:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "PERMISSION_DENIED",
                "message": perm_err.message,
                "recoverable": True,
            },
        )

    from app.permissions import validate_task_permissions

    try:
        for task in plan.tasks:
            validate_task_permissions(agents[task.assigned_agent_id], task)
    except PermissionDenied as exc:
        raise HTTPException(status_code=400, detail={
            "code": "PERMISSION_DENIED", "message": exc.message, "recoverable": True,
        }) from exc

    run = create_run_record(request, plan)
    background.add_task(execute_run, run.run_id, request)
    return CreateRunResponse(run_id=run.run_id, status=RunStatus.PLANNING)


@app.get("/api/runs", response_model=RunsListResponse)
async def list_run_history() -> RunsListResponse:
    return RunsListResponse(runs=list_runs())


@app.get("/api/runs/{run_id}", response_model=RunStatusResponse)
async def get_run_status(run_id: str) -> RunStatusResponse:
    run = get_run(run_id)
    if run is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "NOT_FOUND",
                "message": f"Run '{run_id}' not found.",
                "recoverable": True,
            },
        )
    return run


@app.get("/api/runs/{run_id}/audit", response_model=AuditEventsResponse)
async def get_run_audit(run_id: str) -> AuditEventsResponse:
    events = get_run_audit_events(run_id)
    if events is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "NOT_FOUND",
                "message": f"Run '{run_id}' not found.",
                "recoverable": True,
            },
        )
    return AuditEventsResponse(events=events)


@app.get("/api/audit-events", response_model=AuditEventsResponse)
async def audit_events(limit: int = 200) -> AuditEventsResponse:
    """Recent audit events across runs (Tiger-backed when DB is available)."""
    return AuditEventsResponse(events=list_audit_events(limit=limit))


@app.get("/api/integrations", response_model=IntegrationsResponse)
async def integrations_status() -> IntegrationsResponse:
    """Non-secret connection status for Settings. Never returns credentials."""
    llm = get_llm_service()
    llm_status = "CONNECTED" if llm.is_configured() else "UNCONFIGURED"

    nessie = get_nessie_client()
    # Key present → configured for live calls. Absence → UNCONFIGURED (never fake CONNECTED).
    nessie_status = "CONNECTED" if bool(nessie.api_key) else "UNCONFIGURED"

    tiger_status = "CONNECTED" if db.db_available else "UNCONFIGURED"
    return IntegrationsResponse(
        integrations=[
            IntegrationStatusItem(id="llm", label="AI Model Provider", status=llm_status),
            IntegrationStatusItem(
                id="nessie", label="Capital One Nessie", status=nessie_status
            ),
            IntegrationStatusItem(id="vultr", label="Vultr", status="HOSTED"),
            IntegrationStatusItem(id="tiger", label="Tiger Data", status=tiger_status),
        ]
    )
