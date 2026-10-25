"""Fail closed when a responsibility has no end-to-end runtime implementation.

Only responsibilities with a connected end-to-end executor may run.
Keep this boundary independent of planner output and context-source selection.
"""
from fastapi import HTTPException

from app.job_analysis import ExecutionStatus, JobScope, WorkflowKind, analyze_job


def require_supported_responsibility(
    goal: str,
    *,
    planning: bool = False,
    answers=None,
    context_source_ids=None,
):
    analysis = analyze_job(goal, answers=answers, context_source_ids=context_source_ids)
    message = None
    if analysis.execution_status == ExecutionStatus.NEEDS_SETUP:
        # Legacy runs may have short labels ("demo"). Planning must classify
        # every responsibility; runtime still rejects explicit unsupported work.
        if planning or (goal or "").strip().lower() not in {"", "demo"}:
            missing = ", ".join(analysis.missing_capabilities) or "required connectors"
            message = (
                f"Missing capabilities: {missing}. "
                "This demo assembles Toyota mobility or loan-targeting teams only."
            )
    elif analysis.execution_status == ExecutionStatus.NEED_MORE_INFO:
        raise HTTPException(status_code=400, detail={
            "code": "NEED_MORE_INFO",
            "message": " ".join(analysis.follow_up_questions),
            "recoverable": True,
        })
    if message:
        raise HTTPException(status_code=400, detail={
            "code": "NEEDS_SETUP", "message": message,
            "missing_capabilities": analysis.missing_capabilities,
            "job_analysis": analysis.model_dump(mode="json"),
            "recoverable": True,
        })
    return analysis


def require_supported_team(team):
    policy = team.policy
    if not policy:
        return
    kind = (policy.workflow_kind or "").strip()
    scope = (policy.scope or "").strip()
    ok = (
        (kind == "simple_play" and scope in {"single_customer", "portfolio", ""})
        or (kind == "mobility_decision" and scope in {"single_customer", "portfolio"})
        or (kind == "loan_opportunity" and scope == "portfolio")
    )
    if not ok:
        raise HTTPException(status_code=400, detail={
            "code": "NEEDS_SETUP", "message": "No installed workflow matches this team configuration.", "recoverable": True,
        })
