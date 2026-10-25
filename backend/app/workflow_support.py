"""Fail closed when a responsibility has no end-to-end runtime implementation.

Only responsibilities with a connected end-to-end executor may run.
Keep this boundary independent of planner output and context-source selection.
"""
from fastapi import HTTPException

from app.job_analysis import ExecutionStatus, JobScope, WorkflowKind, analyze_job


def require_supported_responsibility(goal: str, *, planning: bool = False, answers=None):
    analysis = analyze_job(goal, answers=answers)
    message = None
    if analysis.execution_status == ExecutionStatus.NEEDS_SETUP:
        # Legacy runs may have short labels ("demo"). Planning must classify
        # every responsibility; runtime still rejects explicit unsupported work.
        if planning or (goal or "").strip().lower() not in {"", "demo"}:
            message = "Missing capabilities: " + ", ".join(analysis.missing_capabilities)
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
    if policy and (policy.workflow_kind not in {"mobility_decision", "loan_opportunity"} or policy.scope not in {"single_customer", "portfolio"} or (policy.workflow_kind == "loan_opportunity" and policy.scope != "portfolio")):
        raise HTTPException(status_code=400, detail={
            "code": "NEEDS_SETUP", "message": "No installed workflow matches this team configuration.", "recoverable": True,
        })
