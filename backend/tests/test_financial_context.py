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
