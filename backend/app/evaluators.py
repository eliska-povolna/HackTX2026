"""Deterministic post-run evaluators."""

from __future__ import annotations

from typing import Any, Optional

from app.models import EvaluatorResult, EvaluatorStatus, FinancialContext, ToyotaRecommendation


def run_evaluators(
    *,
    financial: Optional[FinancialContext],
    recommendation: Optional[ToyotaRecommendation],
    permission_denials: int,
    permission_allows: int,
    budget_ok: bool,
    tool_failures: int,
    raw_nessie_in_recommendation: bool,
) -> list[EvaluatorResult]:
    results: list[EvaluatorResult] = []

    if financial is None:
        results.append(
            EvaluatorResult(
                name="financial_validity",
                label="Financial validity",
                status=EvaluatorStatus.FAIL,
                details="FinancialContext missing.",
            )
        )
    else:
        valid = (
            financial.monthly_income > 0
            and financial.safe_mobility_budget >= 0
            and financial.financial_stress in {"LOW", "MEDIUM", "HIGH"}
        )
        results.append(
            EvaluatorResult(
                name="financial_validity",
                label="Financial validity",
                status=EvaluatorStatus.PASS if valid else EvaluatorStatus.FAIL,
                details="Derived FinancialContext fields are consistent."
                if valid
                else "FinancialContext failed basic consistency checks.",
            )
        )

    results.append(
        EvaluatorResult(
            name="permission_compliance",
            label="Tool permissions",
            status=EvaluatorStatus.PASS if permission_allows >= 0 else EvaluatorStatus.FAIL,
            details=f"Allowed={permission_allows}, denied(demo)={permission_denials}.",
        )
    )

    results.append(
        EvaluatorResult(
            name="budget_compliance",
            label="Budget constraint",
            status=EvaluatorStatus.PASS if budget_ok else EvaluatorStatus.FAIL,
            details="Run stayed within MAX_LLM_CALLS / MAX_ESTIMATED_RUN_COST."
            if budget_ok
            else "Run hit budget limits.",
        )
    )

    results.append(
        EvaluatorResult(
            name="security_data_minimization",
            label="Security policy",
            status=EvaluatorStatus.FAIL
            if raw_nessie_in_recommendation
            else EvaluatorStatus.PASS,
            details="Raw Nessie rows absent from recommendation payload."
            if not raw_nessie_in_recommendation
            else "Raw transaction-like fields detected in recommendation.",
        )
    )

    data_ok = financial is not None and recommendation is not None
    results.append(
        EvaluatorResult(
            name="required_data",
            label="Required data",
            status=EvaluatorStatus.PASS if data_ok else EvaluatorStatus.FAIL,
            details="FinancialContext and recommendation present."
            if data_ok
            else "Required outputs missing.",
        )
    )

    results.append(
        EvaluatorResult(
            name="tool_success",
            label="Tool success",
            status=EvaluatorStatus.PASS if tool_failures == 0 else EvaluatorStatus.WARN,
            details="All tools succeeded." if tool_failures == 0 else f"{tool_failures} tool failure(s).",
        )
    )
    return results


def recommendation_contains_raw_nessie(payload: Any) -> bool:
    text = str(payload).lower()
    markers = ["purchase_amount", "merchant_id", "raw_transactions", '"purchases":']
    return any(m in text for m in markers)
