"""Nessie fallback and per-run cache."""

from app.models import DataSource
from app.tools.nessie import NessieClient


def test_nessie_fallback_without_key():
    client = NessieClient()
    client.api_key = ""
    data, source = client.load_customer_financials("nessie-demo-001")
    assert source == DataSource.NESSIE_FALLBACK
    assert data["accounts"]
    assert data["purchases"]


def test_nessie_run_cache():
    client = NessieClient()
    client.api_key = ""
    client.clear_run_cache()
    _, s1 = client.load_customer_financials("nessie-demo-001")
    _, s2 = client.load_customer_financials("nessie-demo-001")
    assert s1 == DataSource.NESSIE_FALLBACK
    assert s2 == DataSource.CACHE
