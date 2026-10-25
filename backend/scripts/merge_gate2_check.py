"""Merge Gate 2 live verification: Scenario A vs B."""

from __future__ import annotations

import json
import time
import urllib.request

BASE = "http://127.0.0.1:8000/api"


def req(method: str, path: str, body=None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=90) as resp:
        return resp.status, json.loads(resp.read().decode())


def run_customer(team: dict, customer_id: str) -> dict:
    _, created = req(
        "POST",
        "/runs",
        {
            "goal": "Determine this customer's smartest next mobility decision.",
            "team": team,
            "context": {"customer_id": customer_id},
            "quality_mode": "normal",
        },
    )
    run_id = created["run_id"]
    for _ in range(120):
        _, body = req("GET", f"/runs/{run_id}")
        if body["status"] in {"COMPLETED", "FAILED", "BUDGET_LIMIT"}:
            return body
        time.sleep(0.25)
    raise SystemExit(f"timeout waiting for {customer_id}")


def main() -> None:
    _, health = req("GET", "/health")
    assert health["status"] == "ok"
    print("HEALTH OK", health.get("version"))

    _, plan = req(
        "POST",
        "/teams/plan",
        {
            "goal": "Toyota next move",
            "max_team_size": 5,
            "budget_preference": "balanced",
        },
    )
    team = plan["team"]
    print("TEAM", team["team_name"], "agents", len(team["agents"]))

    a = run_customer(team, "customer-stable")
    b = run_customer(team, "customer-stressed")

    print("\n=== SCENARIO A (stable) ===")
    print("status", a["status"])
    print("stress", a["financial_context"]["financial_stress"])
    print("rec", a["recommendation"]["recommended_action"])
    print("executors", sorted({t["executor"] for t in a["tasks"]}))
    print(
        "metrics",
        {
            k: a["metrics"][k]
            for k in ["code_tasks", "tool_tasks", "llm_tasks", "llm_calls_avoided"]
        },
    )
    events = {e["event_type"] for e in a["audit_events"]}
    print("audit_types", sorted(events))
    print("denied", any(e.get("result") == "DENIED" for e in a["audit_events"]))
    print("raw_in_rec", "purchases" in str(a["recommendation"]).lower())

    print("\n=== SCENARIO B (stressed) ===")
    print("status", b["status"])
    print("stress", b["financial_context"]["financial_stress"])
    print("rec", b["recommendation"]["recommended_action"])

    assert a["status"] == "COMPLETED" and b["status"] == "COMPLETED"
    assert a["financial_context"]["financial_stress"] == "LOW"
    assert b["financial_context"]["financial_stress"] == "HIGH"
    assert (
        a["recommendation"]["recommended_action"]
        != b["recommendation"]["recommended_action"]
    )
    assert {"CODE", "TOOL", "LLM"} <= {t["executor"] for t in a["tasks"]}
    assert "TOOL_CALL" in events and "PERMISSION_CHECK" in events
    assert "purchases" not in str(a["recommendation"]).lower()
    print("\nMERGE GATE 2: PASS")


if __name__ == "__main__":
    main()
