# QuestMaster — 12-Hour Build Plan

## Operating principle

Two people must be able to work independently.

- **Petr owns frontend / UX / demo story.**
- **Eliška owns backend / orchestration / integrations / deployment.**
- The REST contract in `API_SPEC.md` is the boundary.
- `main` should remain demo-ready.
- Prefer small commits and frequent merges.
- Do not keep long-lived `Petr` and `Eliška` branches until the end.

Recommended branches:
- `frontend/...`
- `backend/...`
- short feature branches only.

---

# Phase 0 — 0:00–0:45
## Goal: freeze the shared contract before parallel work

### Together
- [ ] Create GitHub monorepo.
- [ ] Add:
  - `README.md`
  - `API_SPEC.md`
  - `BUILD_PLAN.md`
  - `DEMO_SCENARIOS.md`
- [ ] Confirm working project name or leave temporary.
- [ ] Create folders:
  - `/frontend`
  - `/backend`
  - `/shared/fixtures`
- [ ] Copy API mock fixtures from `API_SPEC.md`.
- [ ] Create `.gitignore`.
- [ ] Create `.env.example`.
- [ ] Register `.tech` domain.
- [ ] Create Vultr VM.
- [ ] Verify `GET /api/health` can eventually be reached from the VM.
- [ ] Point DNS as early as possible.
- [ ] Do not wait for DNS propagation before building.

### Gate
Both people agree:
- API contract is frozen,
- Toyota is the primary functional demo,
- Nessie financial context must affect the recommendation,
- CODE/TOOL/LLM is the execution story.

---

# Phase 1 — 0:45–3:15
## Parallel build: team creation

## Petr — Frontend
Build with Lovable.

### Must have
- [ ] Demo login / “Continue as Demo User”.
- [ ] Main SaaS shell.
- [ ] Sidebar:
  - Create
  - Teams
  - Reports
  - Logs
  - Settings
- [ ] Goal input.
- [ ] `Build my team` button.
- [ ] `Meet your team` agent cards.
- [ ] Agent detail panel.
- [ ] Agent editing:
  - rename,
  - role,
  - skills,
  - tools,
  - permissions,
  - budget,
  - remove,
  - simple add.
- [ ] Toyota demo template visible.
- [ ] All calls use mocked JSON matching `API_SPEC.md`.

### Important
Do not wait for backend.

Use:
- `shared/fixtures/plan_team_response.json`

---

## Eliška — Backend
### Must have
- [ ] FastAPI skeleton.
- [ ] `GET /api/health`.
- [ ] `GET /api/templates`.
- [ ] `POST /api/teams/plan`.
- [ ] Pydantic shared models.
- [ ] `LLMService` provider abstraction.
- [ ] Meta provider adapter if access works.
- [ ] fallback planner.
- [ ] synthetic Toyota customer fixtures.
- [ ] no database required yet.

### Team planner
One model call only.

Input:
- goal,
- available tools,
- max team size,
- budget preference.

Output:
- structured `TeamDefinition`.

If LLM fails:
- return fallback team with HTTP 200.

---

# Merge Gate 1 — target 3:15

Required:
- [ ] frontend calls real `/api/teams/plan`,
- [ ] team appears,
- [ ] fallback works,
- [ ] agent edits remain functional,
- [ ] no visual regression.

If integration fails:
- fix before adding execution features.

---

# Phase 2 — 3:15–7:00
## Parallel build: real execution

## Petr — Frontend
### Must have
- [ ] `Run Team` button.
- [ ] run page.
- [ ] polling every second.
- [ ] agent statuses.
- [ ] task statuses.
- [ ] executor badges:
  - CODE
  - TOOL
  - AI/LLM
- [ ] live activity summaries.
- [ ] output panel.
- [ ] cost panel.
- [ ] evaluator panel.
- [ ] audit timeline.
- [ ] security/access view.
- [ ] customer selector for demo scenarios.

Keep using mocks until real backend is ready.

---

## Eliška — Backend

### Execution
- [ ] `POST /api/runs`.
- [ ] `GET /api/runs/{id}`.
- [ ] execution planner.
- [ ] executor.
- [ ] task dependencies.
- [ ] background execution.
- [ ] run state in memory.

### CODE executor
Implement only needed functions:
- [ ] `calculate_total`
- [ ] `calculate_average`
- [ ] `calculate_change`
- [ ] `filter_records`
- [ ] `sort_records`
- [ ] `group_records`
- [ ] `calculate_affordability`
- [ ] `calculate_emergency_runway`
- [ ] `score_mobility_options`

Never execute generated Python.

### Nessie
Implement only what the demo needs:
- [ ] get customer
- [ ] get accounts
- [ ] get purchases / transactions needed for fixture
- [ ] optional bills/loans if easy
- [ ] per-run cache
- [ ] retry once
- [ ] fixture fallback

### Financial context
Build derived `FinancialContext`.

Raw banking rows must not be passed to Decision Agent.

### Security
- [ ] allowed-tools enforcement.
- [ ] data-scope enforcement where practical.
- [ ] audit permission checks.
- [ ] decision agent only sees `financial_summary`.

### Cost
- [ ] max calls per run.
- [ ] max run budget.
- [ ] code/tool first.
- [ ] fast vs strong model abstraction.
- [ ] baseline savings estimate.

### Evaluators
- [ ] financial validity.
- [ ] permission compliance.
- [ ] budget compliance.
- [ ] security/data-minimization compliance.
- [ ] tool success.

---

# Merge Gate 2 — target 7:00

Required:
- [ ] Toyota Scenario A completes end-to-end.
- [ ] Nessie live or fallback data flows through backend.
- [ ] `FinancialContext` is calculated.
- [ ] recommendation is generated.
- [ ] frontend shows real run status.
- [ ] audit log shows at least:
  - TOOL call,
  - CODE calculation,
  - LLM call,
  - permission event.
- [ ] no secrets appear in frontend/network response.

If this does not work:
**stop adding features and fix it.**

---

# Phase 3 — 7:00–9:00
## Reliability + second scenario

### Together
- [ ] Run Scenario A three times.
- [ ] Run Scenario B three times.
- [ ] Verify same/similar Toyota data can yield a different recommendation because Nessie financial context differs.
- [ ] Confirm no raw Nessie transactions are sent to Decision Agent.
- [ ] Verify budget limits.
- [ ] Verify fallback planner.
- [ ] Verify Nessie fallback.
- [ ] Verify one permission denial in a safe test.

### Petr
- [ ] polish Toyota recommendation screen.
- [ ] polish run execution.
- [ ] improve visual distinction between CODE / TOOL / LLM.
- [ ] show “AI calls avoided”.
- [ ] show “estimated AI cost saved”.
- [ ] add judge-friendly labels.

### Eliška
- [ ] improve audit messages.
- [ ] make evaluator results stable.
- [ ] ensure provider failures degrade gracefully.
- [ ] verify no 500 in expected demo flow.

---

# Phase 4 — 9:00–10:00
## Deployment

Deployment should already have been smoke-tested in Phase 0.

### Eliška
- [ ] deploy frontend and backend to Vultr.
- [ ] configure Caddy/Nginx.
- [ ] `/` → frontend.
- [ ] `/api/*` → FastAPI.
- [ ] environment variables server-side.
- [ ] HTTPS.
- [ ] test public `/api/health`.

### Petr
- [ ] test real public frontend.
- [ ] verify no localhost URLs.
- [ ] verify mobile/desktop enough for judging.
- [ ] prepare screenshots.

---

# Phase 5 — 10:00–11:00
## Public-domain test + track requirements

- [ ] `.tech` resolves.
- [ ] HTTPS works.
- [ ] end-to-end demo works from public URL.
- [ ] Nessie source is visible in audit/metrics.
- [ ] Vultr hosting is demonstrably real.
- [ ] add relevant sponsor/MLH track descriptions to Devpost.
- [ ] add architecture diagram if easy.

Do not add Snowflake/Solana/etc. unless the core is already fully stable.

---

# Phase 6 — 11:00–12:00
## Freeze features

No new product features.

Only:
- bug fixing,
- fallback testing,
- visual polish,
- demo seed data,
- Devpost,
- pitch,
- screenshots,
- rehearsal.

### Reliability checklist
- [ ] demo succeeds three consecutive times,
- [ ] planner fallback tested,
- [ ] Nessie fallback tested,
- [ ] LLM unavailable path tested,
- [ ] no duplicate Run requests,
- [ ] loading states,
- [ ] empty states,
- [ ] readable error messages,
- [ ] public URL works.

---

# Non-negotiable priorities

## Priority 1 — Toyota working prototype
A real working end-to-end customer recommendation.

## Priority 2 — NMC2 platform story
Reusable teams, tools, permissions, budgets, evaluators, observability.

## Priority 3 — Nessie meaningful use
Financial context materially changes recommendation.

## Priority 4 — public deployment
Vultr + `.tech`.

## Priority 5 — bonus tracks
Only after everything above works.

---

# Stop-doing list

Do not build:
- Kubernetes,
- Kafka,
- Redis,
- generic DAG engine,
- arbitrary code execution,
- real SSO,
- OAuth,
- MFA,
- true multi-tenancy,
- secret manager,
- long-term memory,
- advanced RBAC/ABAC,
- complex WebSockets,
- general-purpose workflow canvas,
- full Nessie wrapper,
- model marketplace,
- GPU scheduler,
- autonomous agent loops,
- agent-to-agent debate for its own sake.

---

# Git workflow

Use `main` as demo-ready.

Recommended:
- short feature branches,
- small commits,
- frequent merges.

Before a risky change:
```bash
git add .
git commit -m "working checkpoint"
```

Do not leave the final merge until the end.

---

# Build-agent instruction

Every coding agent working on this repo should obey:

1. Read `README.md`.
2. Read `API_SPEC.md`.
3. Read `BUILD_PLAN.md`.
4. Do not change API fields unless explicitly instructed.
5. Do not replace CODE/TOOL/LLM routing with a generic autonomous loop.
6. Do not add infrastructure outside the approved scope.
7. Preserve graceful fallbacks.
8. Preserve security/data-minimization boundaries.
9. Prefer reliability over abstraction.
10. Stop when the assigned phase is complete.
