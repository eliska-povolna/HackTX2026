"""Capital One Nessie connector with fixture fallback and per-run cache."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

import httpx

from app.models import DataSource
from app.runtime_mode import nessie_fallback_enabled

logger = logging.getLogger(__name__)

FIXTURES_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "nessie_demo.json"
LIVE_IDS_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "nessie_live_ids.json"


def _ssl_verify_targets() -> list[bool | str]:
    targets: list[bool | str] = []
    try:
        import certifi

        targets.append(certifi.where())
    except ImportError:
        pass
    targets.append(True)
    app_env = (os.getenv("APP_ENV") or "development").strip().lower()
    if app_env in {"development", "dev", "local"}:
        targets.append(False)
    return targets


class NessieClient:
    def __init__(self) -> None:
        self.api_key = (os.getenv("NESSIE_API_KEY") or "").strip()
        self.base_url = (
            os.getenv("NESSIE_BASE_URL") or "https://api.nessieisreal.com"
        ).rstrip("/")
        # Resolved at call time via nessie_fallback_enabled(); kept for scripts/logging.
        self.fallback_enabled = nessie_fallback_enabled()
        self._fixture = self._load_json(FIXTURES_PATH)
        self._live_ids = self._load_live_ids()
        self._run_cache: dict[str, Any] = {}

    @staticmethod
    def _load_json(path: Path) -> dict:
        with path.open(encoding="utf-8") as f:
            return json.load(f)

    def _load_live_ids(self) -> dict:
        if LIVE_IDS_PATH.exists():
            try:
                return self._load_json(LIVE_IDS_PATH)
            except Exception:
                return {}
        return {}

    def demo_label_for_customer_id(self, nessie_customer_id: str) -> Optional[str]:
        """Return 'stable' / 'stressed' for known demo customers (live or fixture)."""
        if nessie_customer_id in {"nessie-demo-001"} or nessie_customer_id.endswith("001"):
            if nessie_customer_id.startswith("nessie-demo"):
                return "stable"
        if nessie_customer_id in {"nessie-demo-002"} or (
            nessie_customer_id.startswith("nessie-demo") and nessie_customer_id.endswith("002")
        ):
            return "stressed"
        for label, info in self._live_ids.items():
            if info.get("customer_id") == nessie_customer_id:
                return label
        return None

    def clear_run_cache(self) -> None:
        self._run_cache.clear()

    def _cache_get(self, key: str) -> Any | None:
        return self._run_cache.get(key)

    def _cache_set(self, key: str, value: Any) -> Any:
        self._run_cache[key] = value
        return value

    def _redact(self, message: str) -> str:
        if self.api_key:
            return message.replace(self.api_key, "***")
        return message

    def _get_json(self, path: str, params: Optional[dict] = None) -> Any:
        if not self.api_key:
            raise RuntimeError("NESSIE_API_KEY not configured")
        query = {"key": self.api_key}
        if params:
            query.update(params)
        url = f"{self.base_url}{path}"
        last_exc: Exception | None = None
        for verify in _ssl_verify_targets():
            try:
                with httpx.Client(timeout=25.0, verify=verify) as client:
                    resp = client.get(url, params=query)
                    resp.raise_for_status()
                    return resp.json()
            except httpx.HTTPStatusError:
                raise
            except Exception as exc:
                last_exc = exc
                if "CERTIFICATE" in str(exc).upper() or "SSL" in str(exc).upper():
                    logger.info(
                        "Nessie TLS attempt failed (verify=%s): %s",
                        verify,
                        self._redact(str(exc)),
                    )
                    continue
                raise
        assert last_exc is not None
        raise last_exc

    def _fixture_customer(self, nessie_customer_id: str) -> dict:
        customers = self._fixture.get("customers", {})
        label = self.demo_label_for_customer_id(nessie_customer_id)
        if label == "stable":
            return customers["nessie-demo-001"]
        if label == "stressed":
            return customers["nessie-demo-002"]
        if nessie_customer_id in customers:
            return customers[nessie_customer_id]
        return customers["nessie-demo-001"]

    def load_customer_financials(self, nessie_customer_id: str) -> tuple[dict, DataSource]:
        """Return aggregated banking payload + source.

        Production/live: never silently substitute fixtures. Test/dev may use fixtures
        when nessie_fallback_enabled() is true.
        """
        allow_fixtures = nessie_fallback_enabled()
        self.fallback_enabled = allow_fixtures

        cache_key = f"financials:{nessie_customer_id}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached["data"], DataSource.CACHE

        live_error: Exception | None = None
        if self.api_key:
            try:
                customer = self._get_json(f"/customers/{nessie_customer_id}")
                accounts = self._get_json(f"/customers/{nessie_customer_id}/accounts")
                purchases: list[dict] = []
                deposits: list[dict] = []
                account_list = (
                    accounts if isinstance(accounts, list) else accounts.get("accounts", [])
                )
                for acc in account_list:
                    acc_id = acc.get("_id") or acc.get("id")
                    if not acc_id:
                        continue
                    try:
                        purch = self._get_json(f"/accounts/{acc_id}/purchases")
                        if isinstance(purch, list):
                            for p in purch:
                                desc = (p.get("description") or "").lower()
                                if any(
                                    k in desc
                                    for k in (
                                        "rent",
                                        "lease",
                                        "insurance",
                                        "loan payment",
                                        "phone",
                                    )
                                ):
                                    p["fixed"] = True
                            purchases.extend(purch)
                    except Exception:
                        pass
                    try:
                        deps = self._get_json(f"/accounts/{acc_id}/deposits")
                        if isinstance(deps, list):
                            deposits.extend(deps)
                    except Exception:
                        pass
                data = {
                    "customer_id": nessie_customer_id,
                    "customer": customer,
                    "accounts": account_list,
                    "purchases": purchases,
                    "deposits": deposits,
                    "loans": [],
                    "source_note": "live",
                    "demo_label": self.demo_label_for_customer_id(nessie_customer_id),
                }
                if account_list:
                    self._cache_set(cache_key, {"data": data})
                    return data, DataSource.NESSIE_LIVE
                live_error = RuntimeError("Nessie returned no accounts for customer")
            except Exception as exc:
                live_error = Exception(self._redact(str(exc)))
                logger.warning(
                    "Nessie live failed for %s: %s", nessie_customer_id, live_error
                )
        else:
            live_error = RuntimeError("NESSIE_API_KEY not configured")

        if not allow_fixtures:
            raise live_error or RuntimeError("Nessie live unavailable")

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
            "demo_label": fixture.get("label")
            or self.demo_label_for_customer_id(nessie_customer_id),
        }
        self._cache_set(cache_key, {"data": data})
        return data, DataSource.NESSIE_FALLBACK


_nessie: Optional[NessieClient] = None


def get_nessie_client() -> NessieClient:
    global _nessie
    if _nessie is None:
        _nessie = NessieClient()
    return _nessie
