"""Deterministic CODE helpers for mobility scoring (no generated Python)."""

from __future__ import annotations

from typing import Any

from app.models import (
    FinancialContext,
    NextStep,
    RecommendationOption,
    ToyotaRecommendation,
)

# Lease / contract decision window (months). Demo customers use months_remaining=2.
_LEASE_DECISION_WINDOW_MONTHS = 6
# Material usage swing (absolute percent). Demo customers use ~-38%.
_USAGE_CHANGE_THRESHOLD = 20.0


def evaluate_mobility_relevance(toyota: dict[str, Any]) -> dict[str, Any]:
    """
    Deterministic CODE gate: whether a full mobility decision should run now.

    Uses only signals already present on the Toyota customer fixture
    (contract.months_remaining, usage.change_percent). No new data sources.
    """
    contract = toyota.get("contract") or {}
    usage = toyota.get("usage") or {}
    months = int(contract.get("months_remaining") if contract.get("months_remaining") is not None else 99)
    change = float(usage.get("change_percent") or 0.0)

    reasons: list[str] = []
    if months <= _LEASE_DECISION_WINDOW_MONTHS:
        reasons.append("Lease is within the decision window")
    if abs(change) >= _USAGE_CHANGE_THRESHOLD:
        reasons.append("Driving pattern changed materially")

    # Explicit override for tests / synthetic non-relevant fixtures
    if toyota.get("force_not_relevant") is True:
        return {
            "relevant": False,
            "reasons": ["No mobility decision signal in the current customer window"],
            "months_remaining": months,
            "usage_change_percent": change,
        }

    relevant = len(reasons) > 0
    if not relevant:
        reasons = ["No mobility decision signal in the current customer window"]
    return {
        "relevant": relevant,
        "reasons": reasons,
        "months_remaining": months,
        "usage_change_percent": change,
    }


def score_mobility_options(
    toyota: dict[str, Any],
    financial: FinancialContext,
) -> dict[str, Any]:
    """Score ownership options. Same vehicle + different finance → different winner."""
    contract = toyota.get("contract") or {}
    market = toyota.get("market_assumptions") or {}
    usage = toyota.get("usage") or {}
    vehicle = toyota.get("vehicle") or {}

    buyout = float(contract.get("buyout_amount") or 24500)
    lease_payment = float(contract.get("monthly_payment") or 469)
    vehicle_value = float(market.get("estimated_vehicle_value") or 26800)
    upgrade_payment = float(market.get("estimated_upgrade_payment") or 585)
    # Approximate ownership payment from buyout over 60 months + insurance buffer
    buy_monthly = round(buyout / 60 + 85, 2)
    return_monthly = 0.0
    flexible_monthly = 320.0

    usage_drop = float(usage.get("change_percent") or 0)

    options = [
        RecommendationOption(
            id="buy-current",
            label="Buy current vehicle",
            monthly_cost=buy_monthly,
            three_year_cost=round(buy_monthly * 36, 2),
            fit_score=0,
            safe_for_budget=buy_monthly <= financial.safe_mobility_budget,
            tradeoffs=[
                "Keeps a vehicle that still fits expected needs.",
                "Converts lease equity when buyout is below market value.",
            ],
        ),
        RecommendationOption(
            id="upgrade",
            label="Upgrade vehicle",
            monthly_cost=upgrade_payment,
            three_year_cost=round(upgrade_payment * 36, 2),
            fit_score=0,
            safe_for_budget=upgrade_payment <= financial.safe_mobility_budget,
            tradeoffs=[
                "Higher monthly cost than keeping the current vehicle.",
                "Useful only if current vehicle no longer fits needs.",
            ],
        ),
        RecommendationOption(
            id="return",
            label="Return vehicle",
            monthly_cost=return_monthly,
            three_year_cost=0.0,
            fit_score=0,
            safe_for_budget=True,
            tradeoffs=[
                "Preserves cash and removes lease obligation.",
                "Forfeits buyout equity if market value exceeds buyout.",
            ],
        ),
        RecommendationOption(
            id="flexible",
            label="Flexible mobility",
            monthly_cost=flexible_monthly,
            three_year_cost=round(flexible_monthly * 36, 2),
            fit_score=0,
            safe_for_budget=flexible_monthly <= financial.safe_mobility_budget,
            tradeoffs=[
                "Lower commitment while usage is reduced.",
                "May cost more long-term than buying a sound vehicle.",
            ],
        ),
    ]

    for opt in options:
        score = 50
        if opt.safe_for_budget:
            score += 25
        else:
            score -= 30
        if opt.id == "buy-current":
            if buyout < vehicle_value:
                score += 15
            if financial.financial_stress == "LOW":
                score += 20
            if financial.financial_stress == "HIGH":
                score -= 35
            if financial.emergency_runway_months >= 3:
                score += 10
            if usage_drop < -30 and financial.financial_stress != "LOW":
                score -= 10
        elif opt.id == "upgrade":
            if financial.financial_stress == "HIGH":
                score -= 40
            if not opt.safe_for_budget:
                score -= 20
            score -= 5  # usually not preferred in demo
        elif opt.id == "return":
            if financial.financial_stress == "HIGH":
                score += 35
            if financial.emergency_runway_months < 1.5:
                score += 15
            if usage_drop < -30:
                score += 10
            if financial.financial_stress == "LOW":
                score -= 15
        elif opt.id == "flexible":
            if financial.financial_stress == "HIGH":
                score += 20
            if usage_drop < -30:
                score += 10
            if financial.financial_stress == "LOW":
                score -= 10
        # Prefer staying under budget strongly
        headroom = financial.safe_mobility_budget - opt.monthly_cost
        score += int(max(min(headroom / 20, 10), -15))
        opt.fit_score = max(0, min(100, score))

    ranked = sorted(options, key=lambda o: o.fit_score, reverse=True)
    winner = ranked[0]

    action_map = {
        "buy-current": "BUY_CURRENT_VEHICLE",
        "upgrade": "UPGRADE_VEHICLE",
        "return": "RETURN_VEHICLE",
        "flexible": "FLEXIBLE_MOBILITY",
    }
    recommended_action = action_map.get(winner.id, "FOLLOW_UP_REQUIRED")

    model = vehicle.get("model", "vehicle")
    year = vehicle.get("year", "")
    reasons = [
        f"Estimated monthly cost ${winner.monthly_cost:.0f} vs safe mobility budget ${financial.safe_mobility_budget:.0f}.",
        f"Financial stress is {financial.financial_stress} with {financial.emergency_runway_months} months runway.",
        f"Usage changed {usage_drop}% versus six months ago.",
    ]
    if winner.id == "buy-current":
        reasons.insert(
            0,
            f"Buyout ${buyout:.0f} is below estimated value ${vehicle_value:.0f} for the {year} {model}.",
        )

    recommendation = ToyotaRecommendation(
        recommended_action=recommended_action,
        confidence=round(winner.fit_score / 100.0, 2),
        headline=_headline(recommended_action, year, model),
        summary=_summary(recommended_action, financial),
        reasons=reasons[:4],
        assumptions=[
            "Synthetic vehicle value and financing terms are used for the hackathon demo.",
            "Nessie-derived FinancialContext drives affordability — raw transactions are not exposed.",
        ],
        next_step=NextStep(
            label=_next_step_label(recommended_action),
            action_type="FOLLOW_UP",
        ),
        options=ranked,
    )

    return {
        "options": [o.model_dump() for o in ranked],
        "recommendation_seed": recommendation.model_dump(),
        "buy_monthly": buy_monthly,
        "lease_payment": lease_payment,
    }


def _headline(action: str, year: Any, model: Any) -> str:
    if action == "BUY_CURRENT_VEHICLE":
        return f"Buying your current {year} {model} is your strongest option."
    if action == "RETURN_VEHICLE":
        return f"Returning your {year} {model} best protects your financial runway."
    if action == "FLEXIBLE_MOBILITY":
        return "A flexible mobility option fits your current cash constraints."
    if action == "UPGRADE_VEHICLE":
        return "An upgrade may fit, but check the higher monthly cost carefully."
    return "A short follow-up is needed before locking a mobility decision."


def _summary(action: str, financial: FinancialContext) -> str:
    if action == "BUY_CURRENT_VEHICLE":
        return (
            "Your current vehicle still fits your needs and stays within your "
            f"safe mobility budget of ${financial.safe_mobility_budget:.0f}."
        )
    if action == "RETURN_VEHICLE":
        return (
            "Ownership cost pressure and limited reserves make returning the vehicle "
            "the safer next move."
        )
    if action == "FLEXIBLE_MOBILITY":
        return "Lower commitment mobility preserves liquidity while usage remains reduced."
    return "Review the scored options and confirm financing assumptions."


def _next_step_label(action: str) -> str:
    if action == "BUY_CURRENT_VEHICLE":
        return "Review buyout financing options"
    if action == "RETURN_VEHICLE":
        return "Schedule lease-end inspection and return"
    if action == "FLEXIBLE_MOBILITY":
        return "Compare short-term mobility plans"
    return "Talk with an advisor"


def build_fallback_recommendation(seed: dict[str, Any]) -> ToyotaRecommendation:
    return ToyotaRecommendation.model_validate(seed)


def calculate_emergency_runway(financial: FinancialContext) -> dict[str, Any]:
    return {
        "emergency_runway_months": financial.emergency_runway_months,
        "cash_reserve": financial.cash_reserve,
    }


def identify_financial_stress(financial: FinancialContext) -> dict[str, Any]:
    return {
        "financial_stress": financial.financial_stress,
        "debt_burden_ratio": financial.debt_burden_ratio,
        "monthly_free_cashflow": financial.monthly_free_cashflow,
    }


def calculate_affordability(financial: FinancialContext) -> dict[str, Any]:
    """Deterministic affordability / headroom signals (no LLM)."""
    stress = financial.financial_stress
    headroom = financial.safe_mobility_budget
    loan_fit = (
        stress == "LOW"
        and financial.emergency_runway_months >= 3.0
        and financial.debt_burden_ratio <= 0.22
        and financial.monthly_free_cashflow >= 1200
    )
    risky = stress == "HIGH" or financial.emergency_runway_months < 1.5
    return {
        "financial_stress": stress,
        "safe_mobility_budget": headroom,
        "emergency_runway_months": financial.emergency_runway_months,
        "debt_burden_ratio": financial.debt_burden_ratio,
        "monthly_free_cashflow": financial.monthly_free_cashflow,
        "cash_reserve": financial.cash_reserve,
        "loan_candidate_fit": loan_fit,
        "high_risk": risky,
        "affordability_score": _affordability_score(financial),
    }


def _affordability_score(financial: FinancialContext) -> int:
    score = 50
    if financial.financial_stress == "LOW":
        score += 25
    elif financial.financial_stress == "HIGH":
        score -= 30
    score += int(min(max(financial.emergency_runway_months * 5, -20), 25))
    score += int(min(max(financial.monthly_free_cashflow / 80, -20), 20))
    score -= int(min(financial.debt_burden_ratio * 80, 30))
    return max(0, min(100, score))


def rank_customers(
    summaries: list[dict[str, Any]],
    *,
    purpose: str = "loan",
) -> dict[str, Any]:
    """
    Deterministic portfolio ranking from minimized customer summaries.
    Never receives raw Nessie rows — only derived FinancialContext fields + Toyota signals.
    """
    purpose_l = (purpose or "loan").lower()
    ranked: list[dict[str, Any]] = []
    for row in summaries:
        financial = row.get("financial") or {}
        toyota = row.get("toyota") or {}
        relevance = row.get("relevance") or {}
        contract = toyota.get("contract") or {}
        usage = toyota.get("usage") or {}
        score = int(financial["affordability_score"] if financial.get("affordability_score") is not None else _score_from_fields(financial))
        reasons: list[str] = []

        if purpose_l in {"loan", "loan_opportunity", "lending"}:
            reasons.append(f"Derived affordability score {score}/100")
            if financial.get("loan_candidate_fit"):
                score += 15
                reasons.append("Strong loan-fit signals (stress/runway/cashflow)")
            if financial.get("high_risk"):
                score -= 25
                reasons.append("Elevated financial risk")
            if float(financial.get("debt_burden_ratio") or 0) > 0.28:
                score -= 10
                reasons.append("Elevated debt burden")
            miles = float((toyota.get("usage") or {}).get("monthly_miles_current") or 0)
            if miles >= 1200:
                score += 8
                reasons.append("High usage supports vehicle financing need")
        else:
            # Mobility urgency differs from loan affordability; never key on identity.
            score = 20
            stress = financial.get("financial_stress")
            if stress in {"HIGH", "MEDIUM"}:
                score += 20 if stress == "HIGH" else 10
                reasons.append("Financial pressure makes the mobility decision more urgent")
            if relevance.get("relevant"):
                score += 20
                reasons.extend(list(relevance.get("reasons") or [])[:2])
            else:
                score -= 40
                reasons.append("Outside mobility decision window")
            months = int(contract.get("months_remaining") if contract.get("months_remaining") is not None else 99)
            if months <= 6:
                score += 10
            if abs(float(usage.get("change_percent") or 0)) >= 20:
                score += 5

        score = max(0, min(100, score))
        ranked.append(
            {
                "customer_id": row.get("customer_id"),
                "name": row.get("name"),
                "score": score,
                "reasons": reasons[:4],
                "financial_summary": {
                    "financial_stress": financial.get("financial_stress"),
                    "safe_mobility_budget": financial.get("safe_mobility_budget"),
                    "monthly_free_cashflow": financial.get("monthly_free_cashflow"),
                    "cash_reserve": financial.get("cash_reserve"),
                    "emergency_runway_months": financial.get("emergency_runway_months"),
                    "debt_burden_ratio": financial.get("debt_burden_ratio"),
                    "affordability_score": financial.get("affordability_score"),
                    "nessie_provenance": row.get("nessie_provenance"),
                },
                "vehicle": toyota.get("vehicle"),
                "contract": {
                    "months_remaining": contract.get("months_remaining"),
                    "monthly_payment": contract.get("monthly_payment"),
                },
            }
        )

    ranked.sort(key=lambda r: r["score"], reverse=True)
    return {
        "purpose": purpose_l,
        "candidates": ranked,
        "top_ids": [r["customer_id"] for r in ranked[:5]],
    }


def _score_from_fields(financial: dict[str, Any]) -> int:
    try:
        fc = FinancialContext(
            monthly_income=float(financial.get("monthly_income") or 0),
            fixed_expenses=float(financial.get("fixed_expenses") or 0),
            variable_spending=float(financial.get("variable_spending") or 0),
            monthly_free_cashflow=float(financial.get("monthly_free_cashflow") or 0),
            cash_reserve=float(financial.get("cash_reserve") or 0),
            safe_mobility_budget=float(financial.get("safe_mobility_budget") or 0),
            emergency_runway_months=float(financial.get("emergency_runway_months") or 0),
            debt_burden_ratio=float(financial.get("debt_burden_ratio") or 0),
            financial_stress=str(financial.get("financial_stress") or "MEDIUM"),
        )
        return _affordability_score(fc)
    except Exception:
        return 50
