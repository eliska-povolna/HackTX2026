"""Integration smoke for FE + Vultr prep. Uses fallbacks; prints no secrets."""

from __future__ import annotations

import json
import time
import urllib.error
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
    try:
        with urllib.request.urlopen(request, timeout=120) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        raise AssertionError(f"{method} {path} -> {exc.code}: {raw[:400]}") from exc


def wait_run(run_id: str) -> dict:
    for _ in range(150):
        _, body = req("GET", f"/runs/{run_id}")
        if body["status"] in {"COMPLETED", "FAILED", "BUDGET_LIMIT"}:
            return body
        time.sleep(0.25)
    raise AssertionError(f"timeout waiting for {run_id}")


def main() -> None:
    st, health = req("GET", "/health")
    assert st == 200 and health["status"] == "ok"
    print("1 health OK", health.get("version"))

    st, templates = req("GET", "/templates")
    assert st == 200 and any(t["id"] == "toyota-mobility" for t in templates["templates"])
    print("2 templates OK")

    st, plan = req(
        "POST",
        "/teams/plan",
        {
            "goal": "Proactively determine the smartest next mobility decision.",
            "max_team_size": 5,
            "budget_preference": "balanced",
        },
    )
    assert st == 200 and plan["team"]["agents"]
    team = plan["team"]
    print(
        "3 teams/plan OK",
        "source=",
        plan["planner_source"],
        "fallback=",
        plan["fallback_used"],
        "agents=",
        len(team["agents"]),
    )

    st, customers = req("GET", "/demo/customers")
    assert st == 200 and len(customers["customers"]) >= 2
    print("4 demo/customers OK", [c["id"] for c in customers["customers"]])

    results = {}
    for cid in ("customer-stable", "customer-stressed"):
        st, created = req(
            "POST",
            "/runs",
            {
                "goal": "Determine this customer's smartest next mobility decision.",
                "team": team,
                "context": {"customer_id": cid},
                "quality_mode": "normal",
            },
        )
        assert st == 202
        assert created["status"] == "PLANNING"
        assert created["run_id"]
        print(f"5/7 create run {cid} OK", created["run_id"], created["status"])
        body = wait_run(created["run_id"])
        assert body["status"] == "COMPLETED"
        assert body.get("recommendation")
        assert body["recommendation"].get("recommended_action")
        assert body.get("financial_context")
        results[cid] = body
        print(
            f"6/8 poll {cid} OK",
            "stress=",
            body["financial_context"]["financial_stress"],
            "rec=",
            body["recommendation"]["recommended_action"],
        )

    a = results["customer-stable"]["recommendation"]["recommended_action"]
    b = results["customer-stressed"]["recommendation"]["recommended_action"]
    assert a != b
    print("9/10 recommendations OK and different:", a, "vs", b)
    print("SMOKE PASS")


if __name__ == "__main__":
    main()
