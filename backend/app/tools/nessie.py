"""Capital One Nessie connector with fixture fallback and per-run cache."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

import httpx

from app.models import DataSource

logger = logging.getLogger(__name__)

FIXTURES_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "nessie_demo.json"


def _ssl_verify() -> bool | str:
    try:
        import certifi

        return certifi.where()
    except ImportError:
        return True


class NessieClient:
    def __init__(self) -> None:
        self.api_key = (os.getenv("NESSIE_API_KEY") or "").strip()
        self.base_url = (
            os.getenv("NESSIE_BASE_URL") or "https://api.nessieisreal.com"
        ).rstrip("/")
        self.fallback_enabled = (
            os.getenv("NESSIE_FALLBACK_ENABLED") or "true"
        ).strip().lower() in {"1", "true", "yes", "on"}
        self._fixture = self._load_fixture()
        self._run_cache: dict[str, Any] = {}

    def _load_fixture(self) -> dict:
        with FIXTURES_PATH.open(encoding="utf-8") as f:
            return json.load(f)

    def clear_run_cache(self) -> None:
        self._run_cache.clear()

    def _cache_get(self, key: str) -> Any | None:
        return self._run_cache.get(key)

    def _cache_set(self, key: str, value: Any) -> Any:
        self._run_cache[key] = value
        return value

    def _get_json(self, path: str, params: Optional[dict] = None) -> Any:
        if not self.api_key:
            raise RuntimeError("NESSIE_API_KEY not configured")
        query = {"key": self.api_key}
        if params:
            query.update(params)
        url = f"{self.base_url}{path}"
        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                verify: bool | str = _ssl_verify()
                try:
                    with httpx.Client(timeout=20.0, verify=verify) as client:
                        resp = client.get(url, params=query)
                        resp.raise_for_status()
                        return resp.json()
                except Exception as ssl_exc:
                    if "SSL" in str(ssl_exc).upper() or "CERTIFICATE" in str(ssl_exc).upper():
                        app_env = (os.getenv("APP_ENV") or "development").lower()
                        if app_env in {"development", "dev", "local"}:
                            with httpx.Client(timeout=20.0, verify=False) as client:
                                resp = client.get(url, params=query)
                                resp.raise_for_status()
                                return resp.json()
                    raise
            except Exception as exc:
                last_exc = exc
                logger.info("Nessie attempt %s failed: %s", attempt + 1, exc)
        assert last_exc is not None
        raise last_exc

    def _fixture_customer(self, nessie_customer_id: str) -> dict:
        customers = self._fixture.get("customers", {})
        if nessie_customer_id not in customers:
            # default to stable fixture
            return customers["nessie-demo-001"]
        return customers[nessie_customer_id]

    def load_customer_financials(self, nessie_customer_id: str) -> tuple[dict, DataSource]:
        """Return aggregated banking payload + source. Never raises if fallback enabled."""
        cache_key = f"financials:{nessie_customer_id}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached["data"], DataSource.CACHE

        live_error: Exception | None = None
        if self.api_key:
            try:
                customer = self._get_json(f"/customers/{nessie_customer_id}")
                accounts = self._get_json(f"/customers/{nessie_customer_id}/accounts")
                # purchases per account
                purchases: list[dict] = []
                account_list = accounts if isinstance(accounts, list) else accounts.get("accounts", [])
                for acc in account_list:
                    acc_id = acc.get("_id") or acc.get("id")
                    if not acc_id:
                        continue
                    try:
                        purch = self._get_json(f"/accounts/{acc_id}/purchases")
                        if isinstance(purch, list):
                            purchases.extend(purch)
                    except Exception:
                        pass
                data = {
                    "customer_id": nessie_customer_id,
                    "customer": customer,
                    "accounts": account_list,
                    "purchases": purchases,
                    "deposits": [],
                    "loans": [],
                    "source_note": "live",
                }
                # If live returns empty useful data, fall through to fixture
                if account_list:
                    packed = {"data": data}
                    self._cache_set(cache_key, packed)
                    return data, DataSource.NESSIE_LIVE
            except Exception as exc:
                live_error = exc
                logger.warning("Nessie live failed for %s: %s", nessie_customer_id, exc)

        if not self.fallback_enabled and live_error:
            raise live_error

        fixture = self._fixture_customer(nessie_customer_id)
        data = {
            "customer_id": fixture["customer_id"],
            "customer": {
                "first_name": fixture.get("first_name"),
                "last_name": fixture.get("last_name"),
            },
            "accounts": fixture.get("accounts", []),
            "purchases": fixture.get("purchases", []),
            "deposits": fixture.get("deposits", []),
            "loans": fixture.get("loans", []),
            "source_note": "fallback",
        }
        self._cache_set(cache_key, {"data": data})
        return data, DataSource.NESSIE_FALLBACK


_nessie: Optional[NessieClient] = None


def get_nessie_client() -> NessieClient:
    global _nessie
    if _nessie is None:
        _nessie = NessieClient()
    return _nessie
