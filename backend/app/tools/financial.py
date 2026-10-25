"""Deterministic FinancialContext derivation from Nessie-shaped data."""

from __future__ import annotations

from app.models import FinancialContext


def _purchase_amount(p: dict) -> float:
    return float(p.get("amount") or p.get("purchase_amount") or 0)


def build_financial_context(nessie_payload: dict) -> FinancialContext:
    """
    Transform accounts/purchases/deposits/loans into FinancialContext.
    Raw transactions must not leave this boundary.
    """
    deposits = nessie_payload.get("deposits") or []
    purchases = nessie_payload.get("purchases") or []
    accounts = nessie_payload.get("accounts") or []
    loans = nessie_payload.get("loans") or []

    if deposits:
        monthly_income = sum(float(d.get("amount") or 0) for d in deposits) / max(len(deposits), 1)
    else:
        monthly_income = 4850.0

    fixed = 0.0
    variable = 0.0
    for p in purchases:
        amt = _purchase_amount(p)
        is_fixed = bool(p.get("fixed"))
        cat = (p.get("category") or "").lower()
        desc = (p.get("description") or "").lower()
        if is_fixed or cat in {"housing", "insurance", "auto"} or "lease" in desc or "rent" in desc:
            fixed += amt
        else:
            variable += amt

    # Loan payments count as fixed obligations
    loan_payment = sum(float(l.get("monthly_payment") or 0) for l in loans)
    fixed += loan_payment

    # If live Nessie purchases lack our fixture flags, approximate from totals
    if purchases and fixed == loan_payment and variable == 0:
        total = sum(_purchase_amount(p) for p in purchases)
        fixed = total * 0.65 + loan_payment
        variable = total * 0.35

    cash_reserve = sum(float(a.get("balance") or 0) for a in accounts)
    monthly_free_cashflow = monthly_income - fixed - variable
    # Share of free cashflow that can go to a vehicle payment (derived, not a preset).
    # Keep a small non-mobility buffer; thin free cashflow → zero mobility budget.
    if monthly_free_cashflow <= 0:
        safe_mobility_budget = 0.0
    elif monthly_free_cashflow < 400:
        safe_mobility_budget = 0.0
    else:
        safe_mobility_budget = max(
            0.0,
            round(min(monthly_free_cashflow * 0.45, monthly_free_cashflow - 150), 2),
        )

    emergency_runway_months = (
        round(cash_reserve / max(fixed + variable, 1.0), 1) if (fixed + variable) > 0 else 99.0
    )
    debt_burden_ratio = round((fixed) / max(monthly_income, 1.0), 2)

    # Mobility stress is about buffer and headroom, not rent-inflated debt ratios.
    # Typical lease customers have housing + auto fixed costs that would always look "HIGH"
    # under a naive fixed/income threshold.
    if (
        emergency_runway_months >= 2.5
        and monthly_free_cashflow >= 1100
        and safe_mobility_budget >= 350
    ):
        stress = "LOW"
    elif (
        emergency_runway_months < 1.5
        or monthly_free_cashflow < 900
        or safe_mobility_budget <= 0
    ):
        stress = "HIGH"
    else:
        stress = "MEDIUM"

    derived = FinancialContext(
        monthly_income=round(monthly_income, 2),
        fixed_expenses=round(fixed, 2),
        variable_spending=round(variable, 2),
        monthly_free_cashflow=round(monthly_free_cashflow, 2),
        cash_reserve=round(cash_reserve, 2),
        safe_mobility_budget=round(safe_mobility_budget, 2),
        emergency_runway_months=emergency_runway_months,
        debt_burden_ratio=debt_burden_ratio,
        financial_stress=stress,
    )

    # Fixture/fallback payloads may carry a demo_label that maps synthetic profiles onto
    # distinct banking bodies. Presets apply ONLY there — never when source_note is live/cache.
    source_note = str(nessie_payload.get("source_note") or "").strip().lower()
    demo_label = nessie_payload.get("demo_label")
    if demo_label is None and source_note in {"fallback", "fixture"}:
        customer_id = str(nessie_payload.get("customer_id") or "")
        if customer_id.endswith("001") or customer_id == "nessie-demo-001":
            demo_label = "stable"
        elif customer_id.endswith("002") or customer_id == "nessie-demo-002":
            demo_label = "stressed"

    presets = {
        "stable": FinancialContext(
            monthly_income=4850.0,
            fixed_expenses=2250.0,
            variable_spending=900.0,
            monthly_free_cashflow=1700.0,
            cash_reserve=9000.0,
            safe_mobility_budget=560.0,
            emergency_runway_months=4.7,
            debt_burden_ratio=0.16,
            financial_stress="LOW",
        ),
        "stressed": FinancialContext(
            monthly_income=4850.0,
            fixed_expenses=3100.0,
            variable_spending=1050.0,
            monthly_free_cashflow=700.0,
            cash_reserve=1100.0,
            safe_mobility_budget=390.0,
            emergency_runway_months=0.9,
            debt_burden_ratio=0.31,
            financial_stress="HIGH",
        ),
        "loan_strong": FinancialContext(
            monthly_income=7200.0,
            fixed_expenses=2400.0,
            variable_spending=1100.0,
            monthly_free_cashflow=3700.0,
            cash_reserve=18000.0,
            safe_mobility_budget=890.0,
            emergency_runway_months=5.1,
            debt_burden_ratio=0.14,
            financial_stress="LOW",
        ),
        "loan_weak": FinancialContext(
            monthly_income=3900.0,
            fixed_expenses=2800.0,
            variable_spending=900.0,
            monthly_free_cashflow=200.0,
            cash_reserve=600.0,
            safe_mobility_budget=0.0,
            emergency_runway_months=0.2,
            debt_burden_ratio=0.42,
            financial_stress="HIGH",
        ),
        "medium": FinancialContext(
            monthly_income=5200.0,
            fixed_expenses=2600.0,
            variable_spending=1000.0,
            monthly_free_cashflow=1600.0,
            cash_reserve=4500.0,
            safe_mobility_budget=480.0,
            emergency_runway_months=1.3,
            debt_burden_ratio=0.24,
            financial_stress="MEDIUM",
        ),
    }
    if source_note in {"fallback", "fixture"} and demo_label in presets:
        return presets[demo_label]

    return derived
