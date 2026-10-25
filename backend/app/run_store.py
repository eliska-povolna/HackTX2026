"""In-memory run state with optional PostgreSQL persistence."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

import app.db as db
from app.db_models import AuditLogRow, OutputRow, RunRow, TaskEventRow, TeamRow
from app.models import (
    AuditEvent,
    AuditEventListItem,
    CreateRunRequest,
    ExecutionPlan,
    ExecutorType,
    FinancialContext,
    OutputItem,
    RunListItem,
    RunMetrics,
    RunStatus,
    RunStatusResponse,
    RunTaskState,
    TaskStatus,
    ToyotaRecommendation,
)

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_runs: dict[str, RunStatusResponse] = {}
_run_team_names: dict[str, str] = {}


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
        _run_team_names[run_id] = request.team.team_name

    _persist_create(request, run)
    return run


def get_run(run_id: str) -> Optional[RunStatusResponse]:
    with _lock:
        run = _runs.get(run_id)
        if run is not None:
            return run.model_copy(deep=True)
    return _hydrate_run_from_db(run_id)


def get_run_audit_events(run_id: str) -> Optional[list[AuditEventListItem]]:
    """Return audit events for one run, or None if the run does not exist."""
    if not _run_exists(run_id):
        return None

    if db.db_available:
        try:
            with db.session_scope() as session:
                if session is not None:
                    rows = (
                        session.query(AuditLogRow)
                        .filter(AuditLogRow.run_id == run_id)
                        .order_by(AuditLogRow.timestamp.desc())
                        .limit(500)
                        .all()
                    )
                    return [_audit_item_from_row(row) for row in rows]
        except Exception as exc:
            logger.warning("get_run_audit_events DB failed: %s", exc)

    events: list[AuditEventListItem] = []
    with _lock:
        run = _runs.get(run_id)
        if run is not None:
            for ev in run.audit_events:
                events.append(
                    AuditEventListItem(
                        id=ev.id,
                        timestamp=ev.timestamp,
                        run_id=ev.run_id,
                        agent_id=ev.agent_id,
                        event_type=ev.event_type,
                        executor_type=ev.executor_type.value if ev.executor_type else None,
                        resource=ev.resource,
                        result=ev.result,
                        message=ev.message,
                        cost_usd=ev.cost_usd,
                    )
                )
    events.sort(key=lambda e: e.timestamp, reverse=True)
    return events


def list_runs() -> list[RunListItem]:
    """Merge in-memory + Tiger runs (memory wins on duplicate). Newest first."""
    by_id: dict[str, RunListItem] = {}

    # Persisted first, then overlay memory so live polls win.
    for item in _list_runs_from_db():
        by_id[item.run_id] = item

    with _lock:
        memory_items = [
            _summary_from_memory(run, _run_team_names.get(run.run_id, "Team"))
            for run in _runs.values()
        ]
    for item in memory_items:
        by_id[item.run_id] = item

    return sorted(by_id.values(), key=lambda r: r.date, reverse=True)


def list_audit_events(*, limit: int = 200) -> list[AuditEventListItem]:
    """Recent audit events from Tiger (and in-memory runs if DB unavailable)."""
    limit = max(1, min(int(limit), 500))
    if db.db_available:
        try:
            with db.session_scope() as session:
                if session is not None:
                    rows = (
                        session.query(AuditLogRow)
                        .order_by(AuditLogRow.timestamp.desc())
                        .limit(limit)
                        .all()
                    )
                    return [_audit_item_from_row(row) for row in rows]
        except Exception as exc:
            logger.warning("list_audit_events DB failed: %s", exc)

    # Fallback: flatten in-memory audits only (dev/tests without Tiger).
    events: list[AuditEventListItem] = []
    with _lock:
        for run in _runs.values():
            for ev in run.audit_events:
                events.append(
                    AuditEventListItem(
                        id=ev.id,
                        timestamp=ev.timestamp,
                        run_id=ev.run_id,
                        agent_id=ev.agent_id,
                        event_type=ev.event_type,
                        executor_type=ev.executor_type.value if ev.executor_type else None,
                        resource=ev.resource,
                        result=ev.result,
                        message=ev.message,
                        cost_usd=ev.cost_usd,
                    )
                )
    events.sort(key=lambda e: e.timestamp, reverse=True)
    return events[:limit]


def _summary_from_memory(run: RunStatusResponse, team_name: str) -> RunListItem:
    result = None
    if run.recommendation and run.recommendation.recommended_action:
        result = run.recommendation.recommended_action
    return RunListItem(
        run_id=run.run_id,
        goal=run.goal,
        team_name=team_name,
        status=run.status,
        result=result,
        date=run.created_at,
        duration_ms=_duration_ms(run),
        estimated_ai_cost_usd=run.metrics.estimated_ai_cost_usd,
        llm_calls=run.metrics.llm_calls,
        llm_calls_avoided=run.metrics.llm_calls_avoided,
    )


def _list_runs_from_db() -> list[RunListItem]:
    if not db.db_available:
        return []
    try:
        with db.session_scope() as session:
            if session is None:
                return []
            rows = session.query(RunRow).order_by(RunRow.created_at.desc()).limit(200).all()
            team_ids = {r.team_id for r in rows if r.team_id}
            teams: dict[str, TeamRow] = {}
            if team_ids:
                for t in session.query(TeamRow).filter(TeamRow.id.in_(team_ids)).all():
                    teams[t.id] = t
            items: list[RunListItem] = []
            for row in rows:
                team_name = "Team"
                if row.team_id and row.team_id in teams:
                    team_name = teams[row.team_id].name or team_name
                metrics = row.metrics_json or {}
                rec = row.recommendation_json or {}
                result = rec.get("recommended_action") if isinstance(rec, dict) else None
                created = row.created_at
                if created is not None and created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                date = (
                    created.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    if created
                    else _utcnow()
                )
                duration_ms = None
                if row.completed_at and row.created_at:
                    duration_ms = max(
                        0,
                        int((row.completed_at - row.created_at).total_seconds() * 1000),
                    )
                try:
                    status = RunStatus(row.status)
                except ValueError:
                    status = RunStatus.FAILED
                items.append(
                    RunListItem(
                        run_id=row.id,
                        goal=row.goal or "",
                        team_name=team_name,
                        status=status,
                        result=result if result else None,
                        date=date,
                        duration_ms=duration_ms,
                        estimated_ai_cost_usd=float(metrics.get("estimated_ai_cost_usd") or 0.0),
                        llm_calls=int(metrics.get("llm_calls") or 0),
                        llm_calls_avoided=int(metrics.get("llm_calls_avoided") or 0),
                    )
                )
            return items
    except Exception as exc:
        logger.warning("list_runs from DB failed: %s", exc)
        return []


def _audit_item_from_row(row: AuditLogRow) -> AuditEventListItem:
    ts = row.timestamp
    if ts is not None and ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    timestamp = (
        ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if ts else _utcnow()
    )
    return AuditEventListItem(
        id=row.id,
        timestamp=timestamp,
        run_id=row.run_id,
        agent_id=row.agent_id,
        event_type=row.event_type,
        executor_type=row.executor_type,
        resource=row.resource,
        result=row.result,
        message=row.message or "",
        cost_usd=float(row.cost_usd or 0.0),
    )


def _run_exists(run_id: str) -> bool:
    with _lock:
        if run_id in _runs:
            return True
    if not db.db_available:
        return False
    try:
        with db.session_scope() as session:
            if session is None:
                return False
            return session.get(RunRow, run_id) is not None
    except Exception:
        return False


def _hydrate_run_from_db(run_id: str) -> Optional[RunStatusResponse]:
    if not db.db_available:
        return None
    try:
        with db.session_scope() as session:
            if session is None:
                return None
            row = session.get(RunRow, run_id)
            if row is None:
                return None

            plan_raw = row.execution_plan_json or {"tasks": []}
            try:
                plan = ExecutionPlan.model_validate(plan_raw)
            except Exception:
                plan = ExecutionPlan(tasks=[])

            tasks: list[RunTaskState] = []
            for t in plan.tasks:
                tasks.append(
                    RunTaskState(
                        id=t.id,
                        name=t.name,
                        assigned_agent_id=t.assigned_agent_id,
                        executor=t.executor,
                        action=t.action,
                        status=TaskStatus.COMPLETED
                        if row.status == RunStatus.COMPLETED.value
                        else TaskStatus.WAITING,
                        depends_on=list(t.depends_on),
                        risk_level=t.risk_level,
                        activity_summary="Restored from persisted run.",
                    )
                )

            metrics = RunMetrics()
            if isinstance(row.metrics_json, dict):
                try:
                    metrics = RunMetrics.model_validate(row.metrics_json)
                except Exception:
                    pass

            financial = None
            if isinstance(row.financial_context_json, dict):
                try:
                    financial = FinancialContext.model_validate(row.financial_context_json)
                except Exception:
                    pass

            recommendation = None
            if isinstance(row.recommendation_json, dict):
                try:
                    recommendation = ToyotaRecommendation.model_validate(
                        row.recommendation_json
                    )
                except Exception:
                    pass

            outputs: list[OutputItem] = []
            for o in (
                session.query(OutputRow)
                .filter(OutputRow.run_id == run_id)
                .order_by(OutputRow.created_at.asc())
                .all()
            ):
                created = o.created_at
                if created is not None and created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                ts = (
                    created.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    if created
                    else _utcnow()
                )
                outputs.append(
                    OutputItem(
                        id=o.id,
                        title=o.title,
                        agent_id=o.agent_id,
                        type=o.type,
                        summary=o.summary or "",
                        content=o.content or "",
                        timestamp=ts,
                        cost_usd=float(o.cost_usd or 0.0),
                        source_type=o.source_type or "CODE",
                    )
                )

            audit_events: list[AuditEvent] = []
            for a in (
                session.query(AuditLogRow)
                .filter(AuditLogRow.run_id == run_id)
                .order_by(AuditLogRow.timestamp.asc())
                .all()
            ):
                ts = a.timestamp
                if ts is not None and ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                timestamp = (
                    ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    if ts
                    else _utcnow()
                )
                executor = None
                if a.executor_type:
                    try:
                        executor = ExecutorType(a.executor_type)
                    except ValueError:
                        executor = None
                audit_events.append(
                    AuditEvent(
                        id=a.id,
                        timestamp=timestamp,
                        run_id=a.run_id,
                        agent_id=a.agent_id,
                        event_type=a.event_type,
                        executor_type=executor,
                        resource=a.resource,
                        result=a.result,
                        message=a.message or "",
                        cost_usd=float(a.cost_usd or 0.0),
                    )
                )

            created = row.created_at
            updated = row.updated_at or row.created_at
            if created is not None and created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if updated is not None and updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            try:
                status = RunStatus(row.status)
            except ValueError:
                status = RunStatus.FAILED

            return RunStatusResponse(
                run_id=row.id,
                status=status,
                goal=row.goal or "",
                created_at=(
                    created.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    if created
                    else _utcnow()
                ),
                updated_at=(
                    updated.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    if updated
                    else _utcnow()
                ),
                plan=plan,
                tasks=tasks,
                metrics=metrics,
                financial_context=financial,
                recommendation=recommendation,
                outputs=outputs,
                evaluators=[],  # evaluators are not persisted separately yet
                audit_events=audit_events,
            )
    except Exception as exc:
        logger.warning("hydrate run %s failed: %s", run_id, exc)
        return None


def _duration_ms(run: RunStatusResponse) -> Optional[int]:
    task_ms = [t.duration_ms for t in run.tasks if t.duration_ms is not None]
    if task_ms:
        return int(sum(task_ms))
    try:
        created = datetime.strptime(run.created_at, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
        updated = datetime.strptime(run.updated_at, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
        return max(0, int((updated - created).total_seconds() * 1000))
    except ValueError:
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
    if not db.db_available:
        return
    try:
        with db.session_scope() as session:
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
    if not db.db_available:
        return
    try:
        with db.session_scope() as session:
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
    if not db.db_available:
        return
    try:
        with db.session_scope() as session:
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
    if not db.db_available:
        return
    try:
        with db.session_scope() as session:
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
    if not db.db_available:
        return
    try:
        with db.session_scope() as session:
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
