"""In-memory run state with optional PostgreSQL persistence."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from app.db import db_available, session_scope
from app.db_models import AuditLogRow, OutputRow, RunRow, TaskEventRow, TeamRow
from app.models import (
    AuditEvent,
    CreateRunRequest,
    ExecutionPlan,
    OutputItem,
    RunMetrics,
    RunStatus,
    RunStatusResponse,
    RunTaskState,
    TaskStatus,
)

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_runs: dict[str, RunStatusResponse] = {}


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def create_run_record(request: CreateRunRequest, plan: ExecutionPlan) -> RunStatusResponse:
    run_id = f"run-{uuid4().hex[:8]}"
    now = _utcnow()
    tasks = [
        RunTaskState(
            id=t.id,
            name=t.name,
            assigned_agent_id=t.assigned_agent_id,
            executor=t.executor,
            action=t.action,
            status=TaskStatus.WAITING,
            activity_summary="Waiting to start.",
            tool=None,
            duration_ms=None,
            cost_usd=0.0,
            data_source=None,
            depends_on=list(t.depends_on),
            risk_level=t.risk_level,
        )
        for t in plan.tasks
    ]
    run = RunStatusResponse(
        run_id=run_id,
        status=RunStatus.PLANNING,
        goal=request.goal,
        created_at=now,
        updated_at=now,
        plan=plan,
        tasks=tasks,
        metrics=RunMetrics(),
        financial_context=None,
        recommendation=None,
        outputs=[],
        evaluators=[],
        audit_events=[],
    )
    with _lock:
        _runs[run_id] = run

    _persist_create(request, run)
    return run


def get_run(run_id: str) -> Optional[RunStatusResponse]:
    with _lock:
        run = _runs.get(run_id)
        if run is not None:
            return run.model_copy(deep=True)
    # try DB hydrate if memory miss
    if db_available:
        try:
            with session_scope() as session:
                if session is None:
                    return None
                row = session.get(RunRow, run_id)
                if row is None:
                    return None
                # minimal hydrate — prefer memory for live polls
                return None
        except Exception as exc:
            logger.debug("DB get_run skipped: %s", exc)
    return None


def update_run(run_id: str, mutator) -> Optional[RunStatusResponse]:
    with _lock:
        run = _runs.get(run_id)
        if run is None:
            return None
        mutator(run)
        run.updated_at = _utcnow()
        snapshot = run.model_copy(deep=True)
    _persist_update(snapshot)
    return snapshot


def append_audit(run_id: str, event: AuditEvent) -> None:
    def _mut(run: RunStatusResponse) -> None:
        run.audit_events.append(event)

    update_run(run_id, _mut)
    _persist_audit(event)


def append_output(run_id: str, output: OutputItem) -> None:
    def _mut(run: RunStatusResponse) -> None:
        run.outputs.append(output)

    update_run(run_id, _mut)
    persist_output_for_run(run_id, output)


def record_task_event(
    *,
    run_id: str,
    agent_id: Optional[str],
    task_id: Optional[str],
    executor_type: Optional[str],
    event_type: str,
    latency_ms: Optional[int],
    cost_usd: float,
    success: bool,
) -> None:
    if not db_available:
        return
    try:
        with session_scope() as session:
            if session is None:
                return
            session.add(
                TaskEventRow(
                    timestamp=datetime.now(timezone.utc),
                    run_id=run_id,
                    agent_id=agent_id,
                    task_id=task_id,
                    executor_type=executor_type,
                    event_type=event_type,
                    latency_ms=latency_ms,
                    cost_usd=cost_usd,
                    success=1 if success else 0,
                )
            )
    except Exception as exc:
        logger.debug("task_event persist skipped: %s", exc)


def _persist_create(request: CreateRunRequest, run: RunStatusResponse) -> None:
    if not db_available:
        return
    try:
        with session_scope() as session:
            if session is None:
                return
            session.merge(
                TeamRow(
                    id=request.team.id,
                    workspace_id="toyota-demo",
                    name=request.team.team_name,
                    goal=request.goal,
                    agents_json=request.team.model_dump(),
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
            )
            session.merge(
                RunRow(
                    id=run.run_id,
                    workspace_id="toyota-demo",
                    team_id=request.team.id,
                    goal=request.goal,
                    customer_id=request.context.customer_id,
                    status=run.status.value,
                    execution_plan_json=run.plan.model_dump(),
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
            )
    except Exception as exc:
        logger.warning("persist create failed: %s", exc)


def _persist_update(run: RunStatusResponse) -> None:
    if not db_available:
        return
    try:
        with session_scope() as session:
            if session is None:
                return
            row = session.get(RunRow, run.run_id)
            if row is None:
                return
            row.status = run.status.value
            row.execution_plan_json = run.plan.model_dump()
            row.financial_context_json = (
                run.financial_context.model_dump() if run.financial_context else None
            )
            row.recommendation_json = (
                run.recommendation.model_dump() if run.recommendation else None
            )
            row.metrics_json = run.metrics.model_dump()
            row.updated_at = datetime.now(timezone.utc)
            if run.status in {
                RunStatus.COMPLETED,
                RunStatus.FAILED,
                RunStatus.BUDGET_LIMIT,
            }:
                row.completed_at = datetime.now(timezone.utc)
    except Exception as exc:
        logger.debug("persist update failed: %s", exc)


def _persist_audit(event: AuditEvent) -> None:
    if not db_available:
        return
    try:
        with session_scope() as session:
            if session is None:
                return
            session.add(
                AuditLogRow(
                    id=event.id,
                    timestamp=datetime.now(timezone.utc),
                    run_id=event.run_id,
                    agent_id=event.agent_id,
                    event_type=event.event_type,
                    executor_type=event.executor_type.value if event.executor_type else None,
                    resource=event.resource,
                    result=event.result,
                    message=event.message,
                    cost_usd=event.cost_usd,
                )
            )
    except Exception as exc:
        logger.debug("persist audit failed: %s", exc)


def persist_output_for_run(run_id: str, output: OutputItem) -> None:
    if not db_available:
        return
    try:
        with session_scope() as session:
            if session is None:
                return
            session.add(
                OutputRow(
                    id=output.id,
                    run_id=run_id,
                    agent_id=output.agent_id,
                    type=output.type,
                    title=output.title,
                    summary=output.summary,
                    content=output.content,
                    source_type=output.source_type,
                    cost_usd=output.cost_usd,
                    created_at=datetime.now(timezone.utc),
                )
            )
    except Exception as exc:
        logger.debug("persist output failed: %s", exc)
