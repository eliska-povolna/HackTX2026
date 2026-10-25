"""Persist user-saved teams in Tiger / PostgreSQL (teams table)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

import app.db as db
from app.db_models import RunRow, TeamRow
from app.models import SavedTeamSummary, TeamDefinition

logger = logging.getLogger(__name__)

# In-memory mirror when Tiger is unavailable (dev/tests).
_memory: dict[str, SavedTeamSummary] = {}
_memory_teams: dict[str, TeamDefinition] = {}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _summary_from_team(
    team: TeamDefinition,
    *,
    goal: str = "",
    last_run_status: str = "NEVER",
) -> SavedTeamSummary:
    cost = sum(float(a.estimated_cost_usd or 0.0) for a in team.agents)
    return SavedTeamSummary(
        id=team.id,
        name=team.team_name,
        purpose=(goal or team.summary or "")[:500],
        member_ids=[a.id for a in team.agents],
        estimated_cost_usd=round(cost, 6),
        last_run_status=last_run_status,
    )


def save_team(team: TeamDefinition, *, goal: str = "") -> SavedTeamSummary:
    if not team.id:
        team = team.model_copy(update={"id": f"team-{uuid4().hex[:10]}"})
    summary = _summary_from_team(team, goal=goal, last_run_status="NEVER")

    if db.db_available:
        try:
            with db.session_scope() as session:
                if session is not None:
                    existing = session.get(TeamRow, team.id)
                    now = _utcnow()
                    if existing is None:
                        session.add(
                            TeamRow(
                                id=team.id,
                                workspace_id="toyota-demo",
                                name=team.team_name,
                                goal=goal or team.summary or "",
                                agents_json=team.model_dump(),
                                created_at=now,
                                updated_at=now,
                            )
                        )
                    else:
                        existing.workspace_id = "toyota-demo"
                        existing.name = team.team_name
                        existing.goal = goal or team.summary or existing.goal
                        existing.agents_json = team.model_dump()
                        existing.updated_at = now
                    # latest run status for this team
                    latest = (
                        session.query(RunRow)
                        .filter(RunRow.team_id == team.id)
                        .order_by(RunRow.created_at.desc())
                        .first()
                    )
                    if latest is not None:
                        summary = summary.model_copy(
                            update={"last_run_status": latest.status}
                        )
                    return summary
        except Exception as exc:
            logger.warning("save_team DB failed: %s", exc)

    _memory[team.id] = summary
    _memory_teams[team.id] = team
    return summary


def list_saved_teams() -> list[SavedTeamSummary]:
    if db.db_available:
        try:
            with db.session_scope() as session:
                if session is not None:
                    rows = (
                        session.query(TeamRow)
                        .filter(TeamRow.workspace_id != "run-only")
                        .order_by(TeamRow.updated_at.desc())
                        .limit(100)
                        .all()
                    )
                    out: list[SavedTeamSummary] = []
                    for row in rows:
                        team = _team_from_row(row)
                        if team is None:
                            continue
                        latest = (
                            session.query(RunRow)
                            .filter(RunRow.team_id == row.id)
                            .order_by(RunRow.created_at.desc())
                            .first()
                        )
                        status = latest.status if latest else "NEVER"
                        out.append(
                            _summary_from_team(
                                team, goal=row.goal or "", last_run_status=status
                            )
                        )
                    return out
        except Exception as exc:
            logger.warning("list_saved_teams DB failed: %s", exc)

    return sorted(_memory.values(), key=lambda t: t.name)


def get_saved_team(team_id: str) -> Optional[tuple[SavedTeamSummary, TeamDefinition]]:
    if db.db_available:
        try:
            with db.session_scope() as session:
                if session is not None:
                    row = session.get(TeamRow, team_id)
                    if row is None or row.workspace_id == "run-only":
                        return None
                    team = _team_from_row(row)
                    if team is None:
                        return None
                    latest = (
                        session.query(RunRow)
                        .filter(RunRow.team_id == team_id)
                        .order_by(RunRow.created_at.desc())
                        .first()
                    )
                    status = latest.status if latest else "NEVER"
                    return (
                        _summary_from_team(
                            team, goal=row.goal or "", last_run_status=status
                        ),
                        team,
                    )
        except Exception as exc:
            logger.warning("get_saved_team DB failed: %s", exc)

    if team_id in _memory and team_id in _memory_teams:
        return _memory[team_id], _memory_teams[team_id]
    return None


def delete_saved_team(team_id: str) -> bool:
    if db.db_available:
        try:
            with db.session_scope() as session:
                if session is not None:
                    row = session.get(TeamRow, team_id)
                    if row is None:
                        return False
                    session.delete(row)
                    _memory.pop(team_id, None)
                    _memory_teams.pop(team_id, None)
                    return True
        except Exception as exc:
            logger.warning("delete_saved_team DB failed: %s", exc)
            return False

    existed = team_id in _memory
    _memory.pop(team_id, None)
    _memory_teams.pop(team_id, None)
    return existed


def _team_from_row(row: TeamRow) -> Optional[TeamDefinition]:
    raw = row.agents_json
    if not isinstance(raw, dict):
        return None
    # agents_json may be full TeamDefinition dump or just agents list historically
    try:
        if "agents" in raw and "team_name" in raw:
            return TeamDefinition.model_validate(raw)
        if "agents" in raw:
            return TeamDefinition.model_validate(
                {
                    "id": row.id,
                    "team_name": row.name,
                    "summary": row.goal or "",
                    "agents": raw.get("agents") or [],
                }
            )
    except Exception as exc:
        logger.debug("team row parse failed: %s", exc)
    return None


def clear_memory_for_tests() -> None:
    _memory.clear()
    _memory_teams.clear()
