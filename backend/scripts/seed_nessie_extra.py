"""Seed additional Capital One Nessie sandbox customers for portfolio demos.

Preserves existing Sarah/Jordan IDs in nessie_live_ids.json. Creates distinct
live profiles for Priya, Marcus, Elena, Chris, and Alex so portfolio runs are
not fixture-backed and Alex no longer shares Sarah's customer id.

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

# Profiles sized so live derivation (no presets) produces distinct stress/headroom.
PROFILES = {
    "loan_strong": {
        "toyota_id": "customer-loan-strong",
        "first_name": "Priya",
        "last_name": "Shah",
        "checking_balance": 6200,
        "savings_balance": 11800,
        "paycheck": 7200,
        "purchases": [
            {"amount": 1900, "description": "RENT"},
            {"amount": 280, "description": "AUTO INSURANCE"},
            {"amount": 220, "description": "GROCERIES"},
            {"amount": 160, "description": "GAS"},
            {"amount": 90, "description": "UTILITIES"},
            {"amount": 120, "description": "DINING"},
            {"amount": 80, "description": "STREAMING"},
            {"amount": 150, "description": "MISC"},
        ],
    },
    "loan_weak": {
        "toyota_id": "customer-loan-weak",
        "first_name": "Marcus",
        "last_name": "Nguyen",
        "checking_balance": 280,
        "savings_balance": 320,
        "paycheck": 3900,
        "purchases": [
            {"amount": 1650, "description": "RENT"},
            {"amount": 310, "description": "AUTO INSURANCE"},
            {"amount": 380, "description": "PERSONAL LOAN PAYMENT"},
            {"amount": 200, "description": "GROCERIES"},
            {"amount": 140, "description": "GAS"},
            {"amount": 120, "description": "UTILITIES"},
            {"amount": 110, "description": "DINING"},
            {"amount": 90, "description": "PHONE INTERNET"},
            {"amount": 180, "description": "CREDIT CARD MIN"},
        ],
    },
    "medium": {
        "toyota_id": "customer-high-usage",
        "first_name": "Elena",
        "last_name": "Rossi",
        "checking_balance": 1800,
        "savings_balance": 2700,
        "paycheck": 5200,
        "purchases": [
            {"amount": 1750, "description": "RENT"},
            {"amount": 360, "description": "AUTO INSURANCE"},
            {"amount": 529, "description": "TOYOTA LEASE"},
            {"amount": 160, "description": "GROCERIES"},
            {"amount": 140, "description": "GAS"},
            {"amount": 95, "description": "UTILITIES"},
            {"amount": 85, "description": "DINING"},
            {"amount": 120, "description": "MISC"},
        ],
    },
    "mid_lease": {
        "toyota_id": "customer-mid-lease",
        "first_name": "Chris",
        "last_name": "Patel",
        "checking_balance": 2100,
        "savings_balance": 3200,
        "paycheck": 5100,
        "purchases": [
            {"amount": 1600, "description": "RENT"},
            {"amount": 300, "description": "AUTO INSURANCE"},
            {"amount": 399, "description": "TOYOTA LEASE"},
            {"amount": 150, "description": "GROCERIES"},
            {"amount": 100, "description": "GAS"},
            {"amount": 90, "description": "UTILITIES"},
            {"amount": 80, "description": "DINING"},
            {"amount": 110, "description": "MISC"},
        ],
    },
    "no_action": {
        "toyota_id": "customer-no-action",
        "first_name": "Alex",
        "last_name": "Kim",
        "checking_balance": 3000,
        "savings_balance": 5500,
        "paycheck": 4800,
        "purchases": [
            {"amount": 1700, "description": "RENT"},
            {"amount": 350, "description": "AUTO INSURANCE"},
            {"amount": 449, "description": "TOYOTA LEASE"},
            {"amount": 130, "description": "GROCERIES"},
            {"amount": 90, "description": "GAS"},
            {"amount": 70, "description": "UTILITIES"},
            {"amount": 80, "description": "DINING"},
            {"amount": 100, "description": "MISC"},
        ],
    },
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


def _create_account(
    http: httpx.Client, customer_id: str, type_: str, nickname: str, balance: float
) -> str:
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


def seed_profile(http: httpx.Client, merchant_id: str, label: str, profile: dict) -> dict:
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
        "label": label,
        "toyota_id": profile["toyota_id"],
    }


def main() -> None:
    if not KEY:
        raise SystemExit("NESSIE_API_KEY missing in .env")

    existing = {}
    if MAP_PATH.exists():
        existing = json.loads(MAP_PATH.read_text(encoding="utf-8"))

    with _client() as http:
        merchant_id = _ensure_merchant(http)
        mapping = dict(existing)
        for label, profile in PROFILES.items():
            if label in mapping and mapping[label].get("customer_id"):
                print(f"keep {label}={mapping[label]['customer_id']}")
                continue
            info = seed_profile(http, merchant_id, label, profile)
            mapping[label] = {
                "customer_id": info["customer_id"],
                "checking_id": info["checking_id"],
                "savings_id": info["savings_id"],
                "label": label,
            }
            print(f"seeded {label}={info['customer_id']}")

        MAP_PATH.write_text(json.dumps(mapping, indent=2) + "\n", encoding="utf-8")

        toyota = json.loads(TOYOTA_PATH.read_text(encoding="utf-8"))
        by_toyota = {p["toyota_id"]: label for label, p in PROFILES.items()}
        for row in toyota:
            label = by_toyota.get(row["id"])
            if not label:
                continue
            info = mapping.get(label)
            if not info:
                continue
            row["nessie_customer_id"] = info["customer_id"]
            row["nessie_provenance"] = "NESSIE_LIVE"
        TOYOTA_PATH.write_text(json.dumps(toyota, indent=2) + "\n", encoding="utf-8")
        print("updated toyota_customers.json")


if __name__ == "__main__":
    main()
