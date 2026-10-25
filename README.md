# QuestMaster — HackTX 2026

Live demo: **https://questmaster.tech**

QuestMaster is a secure, cost-aware multi-agent platform. An agent defines **responsibility**; the platform decides **how** work runs:

- **CODE** — deterministic calculations, scoring, validation
- **TOOL** — connectors such as Capital One Nessie
- **LLM** — planning and synthesis only when ambiguity requires it

Multi-agent does **not** mean multi-LLM.

---

## What’s live today

| Piece | Status |
|---|---|
| Public site | `https://questmaster.tech` (`.tech` DNS) |
| Frontend | Vultr + Caddy |
| Backend | FastAPI on Vultr (`questmaster-backend`) |
| Auto-deploy | GitHub Actions SSH deploy on push to `main` |
| Persistence | Tiger Data (PostgreSQL / time-series) for runs, metrics, audit |
| Banking demo | Capital One Nessie **live** for both Toyota demo customers |
| Coding agents | Cursor + Grok used during build; Grok Bot for production QA |

---

## Toyota demo (core story)

Same vehicle context (2023 Toyota RAV4 lease). Different financial reality → different recommendation:

| Customer | Financial stress | Expected recommendation |
|---|---|---|
| Sarah Miller (`customer-stable`) | LOW | `BUY_CURRENT_VEHICLE` |
| Jordan Lee (`customer-stressed`) | HIGH | `RETURN_VEHICLE` |

Nessie banking data is loaded live, converted into a structured **FinancialContext**, and only that summary crosses the LLM boundary. Raw Nessie rows stay inside the financial agent / tool boundary.

Execution path remains visible: CODE / TOOL / LLM routing, permissions, budgets, evaluators, and audit events.

---

## Backend API (high level)

Base path: `/api`

- `GET /health`
- `GET /templates`, `GET /demo/customers`, `GET /demo/customers/{id}`
- `POST /teams/plan` — Meta LLM team planning (`503` on failure; no silent fallback team)
- `POST /attachments` — demo context file upload
- `POST /runs`, `GET /runs`, `GET /runs/{id}`
- `GET /audit-events` — cross-run Tiger audit history
- `GET /integrations` — non-secret status (`CONNECTED` / `HOSTED` / `UNCONFIGURED` / `ERROR`)

Authoritative contract: [`API_SPEC.md`](./API_SPEC.md).

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

Tests:

```bash
cd backend
pytest -q
```

Test/dev may use Nessie fixtures and deterministic synthesis when explicitly enabled (`NESSIE_FALLBACK_ENABLED`, `LLM_ALLOW_DETERMINISTIC_SYNTHESIS`). **Production must not silently substitute fake data.**

---

## Production truthfulness

In production / live mode:

- Failed Nessie calls fail the run — no silent fixture banking data
- Failed LLM planning returns a structured error — no fake “AI team”
- Failed LLM synthesis records `LLM_ERROR` and fails the task — `LLM_CALL` means a real model call succeeded
- Integrations never claim `CONNECTED` without configuration

---

## Repository layout

```text
HackTX2026/
  API_SPEC.md
  README.md
  .env_template
  backend/
    app/           # FastAPI app, planner, executor, Nessie, Tiger models
    tests/
    scripts/       # smoke / Nessie verification helpers
  .github/workflows/deploy.yml
```

Frontend lives in the separate `hacktx-2026-spark` repository.

---

## Prize-track fit (short)

- **Toyota** — proactive next-move recommendation from vehicle + financial context
- **Capital One Nessie** — live banking data changes the recommendation
- **NMC2** — reusable team planning, tools, budgets, guardrails, evaluators, observability
- **Tiger Data** — persisted runs + audit history powering Reports/Activity
- **Vultr + .tech** — real hosted demo

---

## Security note

Never commit `.env`, API keys, Tiger URLs with passwords, or SSH private keys. Rotate anything that was ever pushed accidentally.
