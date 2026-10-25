# QuestMaster — HackTX 2026

**Live demo:** [https://questmaster.tech](https://questmaster.tech)

QuestMaster is a secure, cost-aware multi-agent platform for reusable business teams.

> An agent defines **responsibility**. QuestMaster decides **how** each task should run — and, when an LLM is required, **which model tier** to use.

---

## Execution routing (CODE → TOOL → LLM)

A **deterministic ExecutionRouter** selects the executor for each task. An LLM is never asked to decide whether to call itself.

```text
CODE  → approved deterministic implementation exists
TOOL  → approved external connector / data source required
LLM   → language or reasoning is genuinely necessary
```

If no valid executor exists under the agent’s `model_policy` and budgets, the task fails clearly.

Multi-agent does **not** mean multi-LLM.

---

## Model routing (FAST / STRONG)

When an LLM task is required, a deterministic **ModelRouter** chooses between configured Meta model tiers:

| Situation | Tier |
|---|---|
| Team planning (`fast` work style) | FAST |
| Team planning (`balanced` / `thorough`) | STRONG |
| Customer-facing synthesis after deterministic scores (`fast` / `balanced`) | FAST |
| Synthesis under `thorough` or high risk | STRONG |
| Reviewer | STRONG |

Decisions are audited as `MODEL_DECISION` (tier + model name + reason). API keys are never exposed.

If the requested tier is not configured (`LLM_FAST_MODEL` / `LLM_STRONG_MODEL`), the run fails truthfully rather than silently claiming another model was used.

This hackathon build uses **Meta** models only — not multi-provider arbitrage.

---

## Work style (`fast` / `balanced` / `thorough`)

TeamPolicy `work_style` changes real execution:

| Style | Effect |
|---|---|
| `fast` | Prefer FAST LLM; normal evaluators; skip optional reviewer unless high-risk/strict |
| `balanced` | Task-based model tier; reviewer only when risk/quality mode requires it |
| `thorough` | Prefer STRONG for reasoning/synthesis; run a real Reviewer LLM task when a Reviewer agent exists; keep deterministic evaluators |

---

## Permissions (never silently broadened)

Runtime **never** auto-appends missing tools or data scopes to make a task succeed.

If a task needs `nessie_load_customer_financials` and the assigned agent lacks that tool (or `nessie_raw_financial` / `read`), the plan/run fails with `PERMISSION_CHECK = DENIED`.

Skills describe capability and help assign tasks when ids/roles are missing. **Skills do not grant permissions.**

---

## Skills, instructions, and guardrails

For real LLM tasks, QuestMaster builds the system prompt from the agent and team configuration:

- role, purpose, skills, instructions  
- allowed data scopes / actions / tool **names**  
- team guardrails + agent guardrails  
- task-specific instructions  

### Guardrail layers (truthful)

1. **Hard backend rules** — raw Nessie isolation, permission checks, budgets, no silent financial side-effects  
2. **Natural-language guardrails** — included in the LLM system prompt (not mathematically guaranteed alone)  
3. **Deterministic evaluators** — final validation on the run result  

---

## Secrets and named integrations

Secrets stay in **server environment configuration**.

Agents may be granted named integrations such as `nessie` or `meta` via `allowed_integrations`. Those are permission references only:

- secret values never appear in `AgentDefinition`  
- never in API responses  
- never in system prompts  
- never in audit messages  

QuestMaster does **not** provide a generic secrets vault or “paste your API key” UI.

---

## Budgets (three ceilings)

An LLM call must satisfy **all** of:

1. Server safety ceiling (`MAX_LLM_CALLS_PER_RUN` / `MAX_ESTIMATED_RUN_COST_USD`)  
2. Team budget (`TeamPolicy.max_llm_calls` / `max_llm_cost_usd`)  
3. Assigned agent budget (`agent.budget.*`)  

`llm_calls_avoided` / `estimated_cost_saved_usd` compare actual CODE/TOOL (and skipped optional LLM) work against a documented **hypothetical full-LLM baseline**. That is an internal comparison — not independently measured market savings.

---

## Reusable, trigger-ready teams

The **Toyota Mobility Decision Team** is a reusable business responsibility. The same team runs for any demo customer when a mobility decision is relevant.

This build is **trigger-ready / event-driven**, not continuous monitoring (no cron/queue workers):

```http
POST /api/triggers/mobility/evaluate
{ "customer_id": "customer-stable", "start_run_if_relevant": false }
```

CODE `evaluate_mobility_relevance` can stop a run as `NO_ACTION_NEEDED` before Nessie/LLM work.

---

## Toyota demo

> Same team. Same Toyota. Different financial reality. Different recommendation.

| Customer | ID | Recommendation |
|---|---|---|
| Sarah Miller | `customer-stable` | `BUY_CURRENT_VEHICLE` |
| Jordan Lee | `customer-stressed` | `RETURN_VEHICLE` |

Typical path: TOOL (Toyota) → CODE (relevance) → TOOL (Nessie) → CODE (FinancialContext) → CODE (score) → LLM (synthesize) → CODE (evaluators). Thorough mode may add a STRONG Reviewer LLM step.

Raw Nessie rows never enter the Decision Agent / LLM prompt — only `FinancialContext` crosses that boundary.

---

## Live stack

| Piece | Status |
|---|---|
| Public site | [questmaster.tech](https://questmaster.tech) |
| Frontend | Vultr + Caddy (`hacktx-2026-spark`) |
| Backend | FastAPI on Vultr |
| Deploy | GitHub Actions on push to `main` |
| Persistence | Tiger Data — teams, runs, metrics, audit |
| Banking | Capital One Nessie **live** |
| Models | Meta AI (FAST / STRONG tiers) |

---

## API overview

Base path: `/api`

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness |
| POST | `/demo-session` | Demo session |
| GET | `/templates` | Templates |
| GET | `/demo/customers` | Toyota customers |
| GET | `/context-sources` | Built-in + file context catalog |
| POST | `/teams/plan` | Meta LLM planning (`503` on failure) |
| GET/POST/DELETE | `/teams` | Tiger-backed teams |
| POST/GET/DELETE | `/attachments` | Context file uploads |
| POST | `/triggers/mobility/evaluate` | Relevance (+ optional run) |
| POST/GET | `/runs`, `/runs/{id}` | Start / poll runs |
| GET | `/runs/{id}/audit` | Per-run audit |
| GET | `/audit-events` | Cross-run audit |
| GET | `/integrations` | Non-secret status |

Optional team fields (backward compatible): `policy.work_style`, `policy.max_llm_*`, `policy.guardrails`.  
Optional agent field: `allowed_integrations` (named refs only).

Full contract: [`API_SPEC.md`](./API_SPEC.md) · Demo script: [`DEMO_SCENARIOS.md`](./DEMO_SCENARIOS.md)

Frontend may later expose work style, team budget, team guardrails, and allowed integrations; the backend already enforces defaults when omitted.

---

## Local development

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -r requirements.txt
cp ../.env_template ../.env
uvicorn app.main:app --reload --app-dir .
pytest -q
```

Configure `LLM_FAST_MODEL`, `LLM_STRONG_MODEL`, `META_API_KEY`, `NESSIE_API_KEY`, and Tiger URL from [`.env_template`](./.env_template). Never commit `.env`.

Test/dev may enable `NESSIE_FALLBACK_ENABLED` and `LLM_ALLOW_DETERMINISTIC_SYNTHESIS`. Production must not silently substitute fake data.

---

## Production truthfulness

- Failed Nessie / LLM / routing / budget / permission checks fail clearly  
- `LLM_CALL` means a real model call succeeded  
- Integrations never claim `CONNECTED` without configuration  
- Permissions are not silently broadened at runtime  

---

## Prize-track integrations

| Partner | Role |
|---|---|
| Toyota | Reusable Mobility Decision Team |
| Capital One Nessie | Live banking TOOL |
| NMC2 | Responsibility + routing, tools, budgets, guardrails, evaluators |
| Tiger Data | Persisted teams / runs / audit |
| Vultr + .tech | Hosted demo |
| Meta AI | Planning + synthesis (+ review) model tiers |

---

## Repository layout

```text
HackTX2026/
  README.md
  API_SPEC.md
  DEMO_SCENARIOS.md
  .env_template
  backend/app/     # ExecutionRouter, ModelRouter, planner, executor, Nessie, Tiger
  backend/tests/
  .github/workflows/
```

Frontend: `hacktx-2026-spark` (separate repository).
