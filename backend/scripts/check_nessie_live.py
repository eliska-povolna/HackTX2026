"""Probe live Nessie connectivity for Toyota demo IDs without printing secrets."""

from __future__ import annotations

import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from app.tools.nessie import LIVE_IDS_PATH, NessieClient  # noqa: E402
from app.tools.toyota import load_toyota_customers  # noqa: E402


def main() -> None:
    # Verification should exercise the production-like path (no silent fixtures).
    os.environ["NESSIE_FALLBACK_ENABLED"] = "false"
    os.environ.setdefault("APP_ENV", "development")

    client = NessieClient()
    print("BASE=", client.base_url)
    print("KEY_SET=", bool(client.api_key))
    print("KEY_LEN=", len(client.api_key or ""))
    print("FALLBACK_ENABLED=", client.fallback_enabled)
    print("LIVE_IDS_PATH=", LIVE_IDS_PATH)

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

    probe("/customers")

    print("\n--- Toyota demo customers (fixture mapping) ---")
    for customer in load_toyota_customers():
        print(
            f"{customer['id']}: nessie_customer_id={customer.get('nessie_customer_id')} "
            f"label={customer.get('scenario_label')}"
        )

    print("\n--- connector load_customer_financials (live IDs only) ---")
    client.clear_run_cache()
    # Prefer live UUID map; fall back to toyota_customers.json IDs.
    targets = []
    if client._live_ids:
        for label in ("stable", "stressed"):
            info = client._live_ids.get(label) or {}
            cid = info.get("customer_id")
            if cid:
                targets.append((f"customer-{label}", cid))
    if not targets:
        for customer in load_toyota_customers():
            targets.append((customer["id"], customer["nessie_customer_id"]))

    all_live = True
    for demo_id, nessie_id in targets:
        try:
            data, source = client.load_customer_financials(nessie_id)
            print(
                f"{demo_id} ({nessie_id}): source={source.value} "
                f"accounts={len(data.get('accounts') or [])} "
                f"purchases={len(data.get('purchases') or [])} "
                f"note={data.get('source_note')}"
            )
            if source.value != "NESSIE_LIVE":
                all_live = False
        except Exception as exc:
            all_live = False
            print(f"{demo_id} ({nessie_id}): FAIL {type(exc).__name__}: {exc}")

    print("\nRESULT=", "OK_LIVE" if all_live else "NOT_ALL_LIVE")


if __name__ == "__main__":
    main()
