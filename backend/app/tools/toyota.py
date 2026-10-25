"""Synthetic Toyota customer loader."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FIXTURES_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "toyota_customers.json"


def load_toyota_customers() -> list[dict[str, Any]]:
    with FIXTURES_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def load_toyota_customer(customer_id: str) -> dict[str, Any]:
    customers = load_toyota_customers()
    match = next((c for c in customers if c["id"] == customer_id), None)
    if match is None:
        raise KeyError(f"Unknown Toyota customer: {customer_id}")
    return match
