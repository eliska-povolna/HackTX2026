"""Seed Capital One Nessie sandbox for QuestMaster Scenario A/B.

Creates two customers with accounts/deposits/purchases approximating DEMO_SCENARIOS.
Updates toyota_customers.json nessie_customer_id fields with live IDs.
Never prints the API key.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

BASE = (os.getenv("NESSIE_BASE_URL") or "https://api.nessieisreal.com").rstrip("/")
KEY = (os.getenv("NESSIE_API_KEY") or "").strip()
TOYOTA_PATH = ROOT / "backend" / "app" / "fixtures" / "toyota_customers.json"
MAP_PATH = ROOT / "backend" / "app" / "fixtures" / "nessie_live_ids.json"

ADDRESS = {
    "street_number": "1100",
    "street_name": "Congress Ave",
    "city": "Austin",
    "state": "TX",
    "zip": "78701",
}


def _client() -> httpx.Client:
    return httpx.Client(timeout=30.0, verify=False)


def _post(http: httpx.Client, path: str, payload: dict) -> dict:
    r = http.post(f"{BASE}{path}", params={"key": KEY}, json=payload)
    if r.status_code >= 400:
        raise RuntimeError(f"POST {path} -> {r.status_code}: {r.text[:300]}")
    return r.json()


def _get(http: httpx.Client, path: str):
    r = http.get(f"{BASE}{path}", params={"key": KEY})
    r.raise_for_status()
    return r.json()


def _ensure_merchant(http: httpx.Client) -> str:
    merchants = _get(http, "/merchants")
    if isinstance(merchants, list) and merchants:
        return merchants[0].get("_id") or merchants[0].get("id")
    created = _post(
        http,
        "/merchants",
        {
            "name": "QuestMaster Demo Merchant",
            "category": "food",
            "address": ADDRESS,
            "geocode": {"lat": 30.27, "lng": -97.74},
        },
    )
    # Nessie often wraps objectId
    return (
        created.get("objectCreated", {}).get("_id")
        or created.get("_id")
        or created.get("id")
    )


def _create_customer(http: httpx.Client, first: str, last: str) -> str:
    created = _post(
        http,
        "/customers",
        {"first_name": first, "last_name": last, "address": ADDRESS},
    )
    return (
        created.get("objectCreated", {}).get("_id")
        or created.get("_id")
        or created.get("id")
    )


def _create_account(http: httpx.Client, customer_id: str, type_: str, nickname: str, balance: float) -> str:
    created = _post(
        http,
        f"/customers/{customer_id}/accounts",
        {
            "type": type_,
            "nickname": nickname,
            "rewards": 0,
            "balance": int(balance),
        },
    )
    return (
        created.get("objectCreated", {}).get("_id")
        or created.get("_id")
        or created.get("id")
    )


def _create_deposit(http: httpx.Client, account_id: str, amount: float, description: str) -> None:
    _post(
        http,
        f"/accounts/{account_id}/deposits",
        {
            "medium": "balance",
            "transaction_date": "2026-09-01",
            "status": "completed",
            "amount": amount,
            "description": description,
        },
    )


def _create_purchase(
    http: httpx.Client, account_id: str, merchant_id: str, amount: float, description: str
) -> None:
    _post(
        http,
        f"/accounts/{account_id}/purchases",
        {
            "merchant_id": merchant_id,
            "medium": "balance",
            "purchase_date": "2026-09-15",
            "amount": amount,
            "status": "completed",
            "description": description,
        },
    )


def seed_profile(http: httpx.Client, merchant_id: str, profile: dict) -> dict:
    cid = _create_customer(http, profile["first_name"], profile["last_name"])
    checking = _create_account(
        http, cid, "Checking", "Primary Checking", profile["checking_balance"]
    )
    savings = _create_account(
        http, cid, "Savings", "Savings", profile["savings_balance"]
    )
    for _ in range(3):
        _create_deposit(http, checking, profile["paycheck"], "PAYROLL ACME CORP")
    for p in profile["purchases"]:
        _create_purchase(http, checking, merchant_id, p["amount"], p["description"])
    return {
        "customer_id": cid,
        "checking_id": checking,
        "savings_id": savings,
        "label": profile["label"],
    }


STABLE = {
    "label": "stable",
    "first_name": "Sarah",
    "last_name": "Miller",
    "checking_balance": 3200,
    "savings_balance": 5800,
    "paycheck": 4850,
    "purchases": [
        {"amount": 1850, "description": "RENT"},
        {"amount": 400, "description": "AUTO INSURANCE"},
        {"amount": 469, "description": "TOYOTA LEASE"},
        {"amount": 120, "description": "GROCERIES"},
        {"amount": 85, "description": "GAS"},
        {"amount": 65, "description": "UTILITIES"},
        {"amount": 45, "description": "STREAMING"},
        {"amount": 95, "description": "DINING"},
        {"amount": 40, "description": "PHARMACY"},
        {"amount": 150, "description": "MISC"},
        {"amount": 180, "description": "CLOTHING"},
        {"amount": 120, "description": "COFFEE LUNCH"},
    ],
}

STRESSED = {
    "label": "stressed",
    "first_name": "Jordan",
    "last_name": "Lee",
    "checking_balance": 650,
    "savings_balance": 450,
    "paycheck": 4850,
    "purchases": [
        {"amount": 2100, "description": "RENT"},
        {"amount": 420, "description": "AUTO INSURANCE"},
        {"amount": 469, "description": "TOYOTA LEASE"},
        {"amount": 111, "description": "PHONE INTERNET"},
        {"amount": 180, "description": "GROCERIES"},
        {"amount": 140, "description": "GAS"},
        {"amount": 95, "description": "UTILITIES"},
        {"amount": 85, "description": "DINING"},
        {"amount": 120, "description": "MEDICAL"},
        {"amount": 200, "description": "CREDIT CARD MIN"},
        {"amount": 110, "description": "MISC"},
        {"amount": 120, "description": "SUBSCRIPTIONS"},
        {"amount": 380, "description": "PERSONAL LOAN PAYMENT"},
    ],
}


def main() -> None:
    if not KEY:
        raise SystemExit("NESSIE_API_KEY missing in .env")

    with _client() as http:
        # Confirm auth
        customers = _get(http, "/customers")
        print("existing_customers=", len(customers) if isinstance(customers, list) else customers)

        merchant_id = _ensure_merchant(http)
        print("merchant_ok=", bool(merchant_id))

        mapping = {
            "stable": seed_profile(http, merchant_id, STABLE),
            "stressed": seed_profile(http, merchant_id, STRESSED),
        }
        MAP_PATH.write_text(json.dumps(mapping, indent=2), encoding="utf-8")
        print("wrote", MAP_PATH.name)
        print("stable_id=", mapping["stable"]["customer_id"])
        print("stressed_id=", mapping["stressed"]["customer_id"])

        # Update toyota fixtures
        toyota = json.loads(TOYOTA_PATH.read_text(encoding="utf-8"))
        for row in toyota:
            if row["id"] == "customer-stable":
                row["nessie_customer_id"] = mapping["stable"]["customer_id"]
            elif row["id"] == "customer-stressed":
                row["nessie_customer_id"] = mapping["stressed"]["customer_id"]
        TOYOTA_PATH.write_text(json.dumps(toyota, indent=2) + "\n", encoding="utf-8")
        print("updated toyota_customers.json nessie_customer_id fields")

        # Verify live load
        for label, info in mapping.items():
            cid = info["customer_id"]
            accts = _get(http, f"/customers/{cid}/accounts")
            print(f"verify {label}: accounts={len(accts)}")


if __name__ == "__main__":
    main()
