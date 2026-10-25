"""FinancialContext derivation and scenario split."""

from app.tools.financial import build_financial_context
from app.tools.nessie import NessieClient
from app.tools.deterministic import score_mobility_options
from app.tools.toyota import load_toyota_customer


def test_stable_fixture_financial_context():
    client = NessieClient()
    client.api_key = ""  # force fallback
    data, source = client.load_customer_financials("nessie-demo-001")
    assert source.value == "NESSIE_FALLBACK"
    ctx = build_financial_context(data)
    assert ctx.financial_stress == "LOW"
    assert ctx.safe_mobility_budget == 560.0
    assert "purchases" not in ctx.model_dump()


def test_stressed_fixture_financial_context():
    client = NessieClient()
    client.api_key = ""
    data, source = client.load_customer_financials("nessie-demo-002")
    assert source.value == "NESSIE_FALLBACK"
    ctx = build_financial_context(data)
    assert ctx.financial_stress == "HIGH"
    assert ctx.safe_mobility_budget == 390.0


def test_live_payload_is_not_overwritten_by_demo_label():
    """Live/cache banking inputs must drive results even when demo_label is present."""
    thin = {
        "customer_id": "b8470c8a-6fde-495f-b1f1-792c771b0709",
        "source_note": "live",
        "demo_label": "stable",
        "accounts": [{"balance": 400}, {"balance": 200}],
        "deposits": [{"amount": 4000}, {"amount": 4000}],
        "purchases": [
            {"amount": 2200, "description": "RENT", "fixed": True, "category": "housing"},
            {"amount": 469, "description": "TOYOTA LEASE", "fixed": True, "category": "auto"},
            {"amount": 900, "description": "GROCERIES", "fixed": False},
        ],
        "loans": [],
    }
    rich = {
        **thin,
        "accounts": [{"balance": 8000}, {"balance": 12000}],
        "deposits": [{"amount": 7500}, {"amount": 7500}],
        "purchases": [
            {"amount": 1600, "description": "RENT", "fixed": True, "category": "housing"},
            {"amount": 400, "description": "AUTO INSURANCE", "fixed": True, "category": "insurance"},
            {"amount": 469, "description": "TOYOTA LEASE", "fixed": True, "category": "auto"},
            {"amount": 500, "description": "GROCERIES", "fixed": False},
        ],
    }
    ctx_thin = build_financial_context(thin)
    ctx_rich = build_financial_context(rich)
    assert ctx_thin.cash_reserve == 600.0
    assert ctx_rich.cash_reserve == 20000.0
    assert ctx_thin.safe_mobility_budget != ctx_rich.safe_mobility_budget
    # Must not collapse to the stable fixture preset.
    assert ctx_rich.safe_mobility_budget != 560.0 or ctx_rich.monthly_free_cashflow != 1700.0
    assert ctx_thin.financial_stress == "HIGH"
    assert ctx_rich.financial_stress == "LOW"


def test_scenarios_differ_on_recommendation():
    toyota_a = load_toyota_customer("customer-stable")
    toyota_b = load_toyota_customer("customer-stressed")
    assert toyota_a["vehicle"] == toyota_b["vehicle"]
    assert toyota_a["contract"] == toyota_b["contract"]

    client = NessieClient()
    client.api_key = ""
    fin_a = build_financial_context(client.load_customer_financials("nessie-demo-001")[0])
    fin_b = build_financial_context(client.load_customer_financials("nessie-demo-002")[0])

    rec_a = score_mobility_options(toyota_a, fin_a)["recommendation_seed"]["recommended_action"]
    rec_b = score_mobility_options(toyota_b, fin_b)["recommendation_seed"]["recommended_action"]
    assert rec_a == "BUY_CURRENT_VEHICLE"
    assert rec_b in {"RETURN_VEHICLE", "FLEXIBLE_MOBILITY"}
    assert rec_a != rec_b
