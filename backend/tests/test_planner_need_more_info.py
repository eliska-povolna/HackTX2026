"""Planner rejects underspecified goals with NEED_MORE_INFO."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.models import PlanTeamRequest
from app.planner import plan_team


@pytest.mark.asyncio
async def test_short_goal_needs_more_info():
    with pytest.raises(HTTPException) as exc:
        await plan_team(PlanTeamRequest(goal="help me"))
    assert exc.value.status_code == 400
    detail = exc.value.detail
    assert isinstance(detail, dict)
    assert detail["code"] == "NEED_MORE_INFO"
