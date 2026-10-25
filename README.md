# QuestMaster — HackTX 2026

**Live demo:** [https://questmaster.tech](https://questmaster.tech)

QuestMaster is a secure, cost-aware multi-agent platform for reusable business teams.

> An agent defines **responsibility**. QuestMaster decides **how** each task should run.

Execution is chosen by a **deterministic ExecutionRouter** (not by an LLM deciding whether to call itself):

```text
CODE  → when an approved deterministic implementation exists
TOOL  → when an approved external connector / data source is required
LLM   → only when language or reasoning is genuinely necessary
```

If no valid executor exists under the agent’s `model_policy` and budgets, the task fails clearly. There is no silent substitution of another method.

Multi-agent does **not** mean multi-LLM.

---

## Live stack

| Piece | Status |
|---|---|
| Public site | [questmaster.tech](https://questmaster.tech) (`.tech` DNS) |
| Frontend | Vultr + Caddy (`hacktx-2026-spark`) |
| Backend | FastAPI on Vultr |
| Deploy | GitHub Actions → SSH deploy on push to `main` |
| Persistence | Tiger Data (PostgreSQL) — teams, runs, metrics, audit |
| Banking | Capital One Nessie **live** for both Toyota demo customers |
| Models | Meta AI for planning and recommendation synthesis |

---

## Toyota demo

> Same team. Same Toyota. Different financial reality. Different recommendation.

| Customer | ID | Stress | Recommendation |
|---|---|---|---|
| Sarah Miller | `customer-stable` | LOW | `BUY_CURRENT_VEHICLE` |
| Jordan Lee | `customer-stressed` | HIGH | `RETURN_VEHICLE` |

Both customers are **separate runs of the same reusable Toyota Mobility Decision Team**. Only customer and financial context change.

Typical path:

1. **TOOL** — load Toyota customer / vehicle / lease / usage context  
2. **CODE** — `evaluate_mobility_relevance` (stop early if no decision is needed)  
3. **TOOL** — load Capital One Nessie banking data  
4. **CODE** — build minimized `FinancialContext`  
5. **CODE** — score mobility options  
6. **LLM** — synthesize the customer-facing recommendation  
7. **CODE** — run evaluators  

Nessie raw account/purchase rows never enter the Decision Agent / LLM prompt — only `FinancialContext` crosses that boundary. Audit events record `ROUTE_DECISION`, permissions, budgets, relevance, and evaluators.

---

## Reusable, trigger-ready teams

The Toyota Mobility Decision Team represents an ongoing business responsibility, not a one-off prompt. The same team can be invoked for any customer when a mobility decision becomes relevant.

This build is **trigger-ready / event-driven**. It does **not** continuously monitor customers (there is no cron, queue, or worker fleet).

Integration point for a future scheduled scan, CRM event, or lease-maturity webhook:

```http
POST /api/triggers/mobility/evaluate
Content-Type: application/json

{ "customer_id": "customer-stable", "start_run_if_relevant": false }
```

Response includes `relevant`, `reasons`, and optional `run_id` when `start_run_if_relevant` is true.

If relevance is false during a run → status `NO_ACTION_NEEDED`, **0 Nessie calls**, **0 LLM calls**.

---

## Cost optimization

- Prefer **CODE**, then **TOOL**, then **LLM**
- Enforce **run-level** and **per-agent** LLM budgets (`max_llm_calls`, `max_llm_cost_usd`)
- Skip optional LLM review on normal-risk runs (explicit audit reason)
- Stop irrelevant events before expensive work
- Metrics compare actual execution against a documented **hypothetical full-LLM baseline** (each task treated as if it were an LLM call). That baseline is an internal comparison — not independently measured market dollar savings.

Agent `model_policy`:

| Value | Behavior |
|---|---|
| `auto` | Normal cheap → safe → adequate routing |
| `deterministic_only` | CODE / TOOL only; LLM tasks fail clearly |
| `llm_allowed` | LLM permitted when the router selects it |

---

## Context sources

`GET /api/context-sources` lists dismissible built-ins (`toyota_customer`, `nessie_banking`) and uploaded files. Plan and run requests may pass `context_source_ids` to enable or disable them.

There is no user secrets vault and no generic “paste your API key” connector in this prototype. Server-side integrations use environment configuration only.

---

## API overview

Base path: `/api`

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness |
| POST | `/demo-session` | Hackathon demo session |
| GET | `/templates` | Team templates |
| GET | `/demo/customers` | Toyota demo customers |
| GET | `/context-sources` | Built-in + file context catalog |
| POST | `/teams/plan` | Meta LLM team planning (`503` on failure) |
| GET/POST/DELETE | `/teams` | Tiger-backed saved teams |
| POST/GET/DELETE | `/attachments` | Demo context file uploads |
| POST | `/triggers/mobility/evaluate` | Relevance check (+ optional run) |
| POST/GET | `/runs`, `/runs/{id}` | Start and poll runs |
| GET | `/runs/{id}/audit` | Per-run audit trail |
| GET | `/audit-events` | Cross-run audit history |
| GET | `/integrations` | Non-secret integration status |

Full contract: [`API_SPEC.md`](./API_SPEC.md) · Demo script: [`DEMO_SCENARIOS.md`](./DEMO_SCENARIOS.md)

---

## Local development

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -r requirements.txt
cp ../.env_template ../.env   # fill secrets locally; never commit .env
uvicorn app.main:app --reload --app-dir .
```

```bash
cd backend
pytest -q
```

Copy secrets from [`.env_template`](./.env_template). Never commit `.env`.

Test/dev may enable Nessie fixtures and deterministic synthesis with `NESSIE_FALLBACK_ENABLED` and `LLM_ALLOW_DETERMINISTIC_SYNTHESIS`. Production must not silently substitute fake data.

---

## Production truthfulness

- Failed Nessie calls fail the run — no silent fixture banking data
- Failed LLM planning returns a structured error — no fake team
- Failed LLM synthesis records `LLM_ERROR` — `LLM_CALL` means a real model call succeeded
- Budget denial → clear budget failure — no fake recommendation
- Unsupported routing → clear failure — no invented executor
- Integrations never claim `CONNECTED` without configuration

---

## Prize-track integrations

| Track / partner | How QuestMaster uses it |
|---|---|
| **Toyota** | Reusable Mobility Decision Team; relevance → recommendation |
| **Capital One Nessie** | Live banking TOOL that changes the recommendation |
| **NMC2** | Responsibility + deterministic routing, tools, budgets, guardrails, evaluators |
| **Tiger Data** | Persisted teams, runs, metrics, audit |
| **Vultr + .tech** | Hosted public demo at questmaster.tech |
| **Meta AI** | Team planning and recommendation synthesis |
| **Grok** | Build-time / production verification support |

---

## Repository layout

```text
HackTX2026/
  README.md
  API_SPEC.md
  DEMO_SCENARIOS.md
  .env_template
  backend/
    app/                 # FastAPI, ExecutionRouter, planner, executor, Nessie, Tiger
    tests/
    scripts/
  .github/workflows/     # deploy on push to main
```

Frontend: separate repository `hacktx-2026-spark`.
