"""Deterministic CODE helpers for mobility scoring (no generated Python)."""

from __future__ import annotations

from typing import Any

from app.models import (
    FinancialContext,
    NextStep,
    RecommendationOption,
    ToyotaRecommendation,
)


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
