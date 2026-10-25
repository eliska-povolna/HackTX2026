"""Fail closed when a responsibility has no end-to-end runtime implementation.

Catalog entries and draft DAG builders alone are not executable capabilities.
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
    elif analysis.workflow_kind == WorkflowKind.LOAN_OPPORTUNITY:
        message = (
            "Loan targeting needs portfolio execution, ranking and shortlist synthesis. "
            "These are not connected to the run engine yet. The installed runnable demo "
            "currently supports a mobility decision for one selected Toyota customer."
        )
    elif analysis.scope == JobScope.PORTFOLIO:
        message = (
            "Portfolio execution is not connected to the run engine yet. "
            "Ask for a mobility decision for one selected Toyota customer instead."
        )
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
    if policy and (policy.workflow_kind != "mobility_decision" or policy.scope != "single_customer"):
        raise HTTPException(status_code=400, detail={
            "code": "NEEDS_SETUP",
            "message": "This team's workflow is not implemented by the live run engine. Only single-customer mobility decisions can run.",
            "recoverable": True,
        })
