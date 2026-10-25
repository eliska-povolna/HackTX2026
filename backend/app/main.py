"""QuestMaster FastAPI application — Phase 1 + Phase 2 run execution."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.db import init_db
from app.executor import build_toyota_execution_plan, execute_run
from app.models import (
    CreateRunRequest,
    CreateRunResponse,
    DemoCustomerDetail,
    DemoCustomerSummary,
    DemoCustomersResponse,
    ErrorDetail,
    ErrorResponse,
    HealthResponse,
    PlanTeamRequest,
    PlanTeamResponse,
    RunStatus,
    RunStatusResponse,
    Template,
    TemplatesResponse,
)
from app.planner import plan_team as plan_team_orchestrator
from app.run_store import create_run_record, get_run
from app.tools.toyota import load_toyota_customers

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


@app.post("/api/runs", response_model=CreateRunResponse, status_code=202)
async def create_run(request: CreateRunRequest, background: BackgroundTasks) -> CreateRunResponse:
    # Validate customer exists
    customers = load_toyota_customers()
    if not any(c["id"] == request.context.customer_id for c in customers):
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

    plan = build_toyota_execution_plan(request.team)
    run = create_run_record(request, plan)
    background.add_task(execute_run, run.run_id, request)
    return CreateRunResponse(run_id=run.run_id, status=RunStatus.PLANNING)


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
