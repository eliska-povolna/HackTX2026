"""Probe live Nessie connectivity without printing secrets."""

from __future__ import annotations

import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from app.tools.nessie import NessieClient  # noqa: E402


def main() -> None:
    client = NessieClient()
    print("BASE=", client.base_url)
    print("KEY_SET=", bool(client.api_key))
    print("KEY_LEN=", len(client.api_key or ""))
    print("FALLBACK_ENABLED=", client.fallback_enabled)

    if not client.api_key:
        print("RESULT=NO_KEY")
        return

    def probe(path: str):
        url = f"{client.base_url}{path}"
        try:
            with httpx.Client(timeout=25.0, verify=False) as http:
                resp = http.get(url, params={"key": client.api_key})
            preview = resp.text[:200].replace(client.api_key, "***")
            print(f"LIVE {path} -> {resp.status_code} body={preview!r}")
            if resp.headers.get("content-type", "").startswith("application/json"):
                return resp.status_code, resp.json()
            return resp.status_code, None
        except Exception as exc:
            print(f"LIVE {path} FAIL {type(exc).__name__}: {exc}")
            return None, None

    status, customers = probe("/customers")
    if customers == []:
        print("NOTE=live customers list empty (sandbox may need seeding)")
    elif isinstance(customers, list) and customers:
        first = customers[0]
        cid = first.get("_id") or first.get("id")
        print("FIRST_CUSTOMER_ID=", cid)
        probe(f"/customers/{cid}")
        probe(f"/customers/{cid}/accounts")
        # purchases for first account if any
        _, accounts = probe(f"/customers/{cid}/accounts")
        if isinstance(accounts, list) and accounts:
            aid = accounts[0].get("_id") or accounts[0].get("id")
            probe(f"/accounts/{aid}/purchases")

    print("\n--- connector load_customer_financials ---")
    client.clear_run_cache()
    for nid in ("nessie-demo-001", "nessie-demo-002"):
        data, source = client.load_customer_financials(nid)
        print(
            f"{nid}: source={source.value} "
            f"accounts={len(data.get('accounts') or [])} "
            f"purchases={len(data.get('purchases') or [])} "
            f"note={data.get('source_note')}"
        )

    # Force live-only attempt for a random/nonexistent id to see API auth behavior
    print("\n--- auth sanity (expect 404/empty, not 401) ---")
    probe("/customers/does-not-exist-xyz")


if __name__ == "__main__":
    os.environ.setdefault("APP_ENV", "development")
    main()
