# QuestMaster — HackTX 2026

> Working project name. Rename globally if the team picks a different final name.

## 1. Product vision

QuestMaster is a secure, cost-aware platform for building and running teams of AI agents around real business goals.

The key product idea is:

> **An agent defines responsibility. The platform decides how the work should be executed.**

A task does **not** automatically mean an LLM call. QuestMaster routes each task to the cheapest safe execution path that can reliably complete it:

- **CODE** — deterministic Python logic for calculations, filtering, sorting, aggregation, thresholds, rules, and transformations.
- **TOOL** — APIs and connectors such as Capital One Nessie.
- **LLM** — only for planning, interpretation, reasoning, synthesis, and other genuinely ambiguous work.

This reduces:
- AI cost,
- latency,
- hallucinations,
- unnecessary data exposure,
- and audit complexity.

The product is a multi-agent platform, but **multi-agent does not mean multi-LLM**.

---

## 2. Hackathon goals

The 12-hour build must produce one reliable end-to-end demo where a non-technical user can:

1. open the application on a `.tech` domain,
2. enter a business goal,
3. receive an AI-proposed team of agents,
4. inspect and lightly edit the team,
5. see each agent's role, skills, tools, permissions, guardrails, and budget,
6. click **Run Team**,
7. receive an execution plan,
8. watch tasks execute using CODE, TOOL, or LLM,
9. see live progress,
10. receive final outputs,
11. see AI cost, avoided AI calls, evaluators, and an audit log,
12. run the application from Vultr.

The primary full demo template is a Toyota Financial Services / mobility decision use case powered by synthetic Toyota data and Nessie banking data.

---

## 3. Prize-track fit

### Toyota Financial Services — “What’s Your Next Move?”
The Toyota workflow should:
- **Personalize** automatically from customer, vehicle, lease/loan, usage, and financial context.
- **Simplify** realistic options, costs, assumptions, and trade-offs.
- **Guide** to a clear next step.
- **Show value** through retention, relevant revenue, reduced service effort, or better mobility fit.
- Work as a real prototype, not a static mock.

The customer should not need to explain their whole life in chat. Personalization should be primarily proactive and data-driven.

### NMC2 — Build the Platform Behind the Agent
QuestMaster should demonstrate:
- reusable team templates,
- connectors/tools,
- context,
- model choice,
- cost/budget controls,
- guardrails,
- evaluators,
- observability,
- triggers/interfaces,
- and self-service team building.

NMC2’s internal example is engineers creating their own agent teams without repeatedly depending on a central AI team. QuestMaster generalizes that idea to any business team.

### Capital One — Best Use of Nessie
Nessie must materially affect the recommendation.

The key story is:

> **Same Toyota. Different financial reality. Different recommendation.**

Nessie data should be converted into a structured financial context such as:
- monthly income,
- fixed obligations,
- variable spending,
- free cash flow,
- cash reserve,
- emergency runway,
- debt burden,
- safe mobility budget,
- financial stress level.

The recommendation must depend on that context.

### MLH bonus tracks
Use only when they fit naturally:
- `.tech` — real public demo domain.
- Vultr — real hosting.
- Gemini / other model track — only if credits/access become available.
- SpaceXSI — only if access becomes available without distracting from the core build.
- Tiger Data — optional; do not add unless the MVP is already stable.

---

## 4. Core architecture

```text
                         USER
                           │
                           ▼
                    .TECH FRONTEND
                           │
                         REST
                           │
                           ▼
                     FASTAPI API
                           │
                ┌──────────┴──────────┐
                │                     │
           TEAM PLANNER         RUN PLANNER
                │                     │
                └──────────┬──────────┘
                           ▼
                       EXECUTOR
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
             CODE         TOOL          LLM
              │            │             │
        deterministic    Nessie      LLMService
           functions      etc.       (provider adapter)
              └────────────┬────────────┘
                           │
                     POLICY LAYER
                permissions / budget
                 security / guardrails
                           │
                           ▼
                       EVALUATORS
                           │
                           ▼
                  OUTPUT + AUDIT LOG
```

### Deployment architecture

```text
Internet
   ↓
https://questmaster.tech
   ↓
Caddy / Nginx on one Vultr Ubuntu VM
   ├── frontend
   └── /api/* → FastAPI
```

No Kubernetes.  
No Kafka.  
No Redis.  
No distributed worker system.  
No microservice architecture.

---

## 5. Frontend / backend ownership

### Petr owns
Everything the user sees:
- Lovable frontend,
- visual design,
- logo/branding,
- team builder UX,
- agent cards,
- Toyota demo experience,
- run progress UI,
- output cards,
- cost/observability UI,
- audit/security UI,
- mock API fixtures,
- pitch flow,
- Devpost.

Frontend may be whatever Lovable generates (React/TypeScript, Vite or Next.js). It must communicate with the backend through the documented REST API only.

### Eliška owns
Everything behind the REST boundary:
- FastAPI,
- LLM provider abstraction,
- Meta model integration if available,
- Nessie integration,
- deterministic tools,
- planner,
- executor,
- cost optimizer,
- permissions,
- data minimization,
- evaluators,
- audit events,
- Vultr deployment,
- `.tech` backend routing,
- fallback behavior.

### Shared rule
**Neither person changes the API contract unilaterally.**

If the contract must change:
1. update `API_SPEC.md`,
2. agree on the change,
3. update frontend mocks and backend implementation,
4. commit the change.

---

## 6. Repository layout

Recommended monorepo:

```text
questmaster/
├── README.md
├── API_SPEC.md
├── BUILD_PLAN.md
├── DEMO_SCENARIOS.md
├── frontend/
│   └── ...
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── models.py
│   │   ├── planner.py
│   │   ├── executor.py
│   │   ├── cost_optimizer.py
│   │   ├── permissions.py
│   │   ├── evaluators.py
│   │   ├── audit.py
│   │   ├── tools/
│   │   │   ├── nessie.py
│   │   │   ├── financial.py
│   │   │   ├── toyota.py
│   │   │   └── deterministic.py
│   │   ├── services/
│   │   │   └── llm_service.py
│   │   └── fixtures/
│   │       ├── nessie_demo.json
│   │       ├── toyota_customers.json
│   │       └── demo_runs.json
│   └── requirements.txt
└── shared/
    └── fixtures/
        ├── plan_team_response.json
        ├── run_response.json
        └── toyota_recommendation.json
```

---

## 7. LLM provider strategy

Do **not** couple orchestration code to OpenAI, Gemini, Meta, or any single provider.

Use one service abstraction:

```python
class LLMService:
    async def plan_team(...): ...
    async def plan_run(...): ...
    async def reason(...): ...
    async def synthesize(...): ...
```

Hackathon provider priority:

1. **Meta AI / Muse** if credits and API access are available.
2. Another available provider only if needed.
3. Fallback fixtures for demo reliability.

Configuration belongs in environment variables:

```env
LLM_PROVIDER=meta
LLM_FAST_MODEL=
LLM_STRONG_MODEL=
META_API_KEY=
NESSIE_API_KEY=
NESSIE_BASE_URL=
```

Never hardcode provider-specific model names into application logic.

---

## 8. Agent model

An agent is a configuration object, not a permanent process.

Minimum concept:

```json
{
  "id": "financial-agent",
  "name": "Maya",
  "role": "Financial Context Analyst",
  "purpose": "Determine what mobility options are financially safe for the customer.",
  "skills": ["cashflow_analysis", "affordability", "financial_context"],
  "instructions": "Use only approved data scopes and tools.",
  "allowed_tools": ["nessie_accounts", "nessie_purchases", "financial_context"],
  "permissions": {
    "data_scopes": ["nessie_raw_financial", "financial_summary"],
    "actions": ["read", "calculate"]
  },
  "guardrails": [
    "Do not expose raw transaction details to downstream agents.",
    "Do not create transfers or financial obligations."
  ],
  "model_policy": "auto",
  "budget": {
    "max_llm_cost_usd": 0.05,
    "max_llm_calls": 2
  }
}
```

Agents define **responsibility + permissions**.

Executors define **how work is performed**.

---

## 9. Execution model

Each task has exactly one executor type:

- `CODE`
- `TOOL`
- `LLM`

### CODE
Use for:
- arithmetic,
- affordability,
- lease calculations,
- averages,
- totals,
- change,
- thresholds,
- filtering,
- sorting,
- grouping,
- deterministic scoring,
- policy checks.

### TOOL
Use for:
- Nessie,
- database access,
- fixed lookups,
- future connectors,
- approved external APIs.

### LLM
Use only for:
- team planning,
- ambiguous interpretation,
- reasoning,
- synthesis,
- human-friendly explanation,
- optional review.

The execution engine itself is normal Python, not an autonomous AI loop.

---

## 10. Cost optimizer

Core rule:

```text
Can safe deterministic CODE solve it?
    ↓ yes → CODE

Can an approved TOOL retrieve/perform it?
    ↓ yes → TOOL

Is LLM reasoning actually needed?
    ↓ yes → choose cheapest allowed adequate model
```

This is not merely cost optimization. It is:

> **the cheapest safe adequate execution path**

Required controls:
- `MAX_LLM_CALLS_PER_RUN`
- `MAX_ESTIMATED_RUN_COST`
- FAST vs STRONG model policy
- optional reviewer only for strict/high-risk runs
- per-agent budget
- global run budget

---

## 11. Security principles

Security is a core product feature.

### Least privilege
Each agent receives only:
- required tools,
- required data scopes,
- required actions.

### Data minimization
Raw Nessie financial data should remain inside the financial analysis boundary whenever possible.

Downstream agents should receive derived features, not raw transactions.

Example:

```json
{
  "monthly_income": 4850,
  "fixed_expenses": 2740,
  "variable_spending": 980,
  "monthly_free_cashflow": 1130,
  "cash_reserve": 3400,
  "safe_mobility_budget": 530,
  "emergency_runway_months": 1.8,
  "financial_stress": "MEDIUM"
}
```

The final Decision Agent should not need to see raw purchase rows.

### Tool enforcement
Permissions are enforced in backend code.

Never trust the LLM to obey tool permissions.

```python
if tool_name not in agent.allowed_tools:
    raise PermissionDenied(...)
```

### Human approval
For this hackathon, no real banking action is required.

If external actions are ever demonstrated, represent them as:
- `APPROVAL_REQUIRED`
- not silently executed.

### Secrets
Secrets live server-side only in `.env`.

Never:
- send keys to frontend,
- log keys,
- commit `.env`.

---

## 12. Evaluators

Keep evaluation cheap and deterministic.

At the end of a run, compute:

- financial validity,
- required data available,
- budget compliance,
- permission compliance,
- security/data-minimization compliance,
- tool success rate,
- human intervention required,
- recommendation confidence if available.

Example UI:

```text
Financial validity      PASS
Budget constraint       PASS
Tool permissions        PASS
Security policy         PASS
Required data           PASS
```

Do not build a complex evaluation framework during the hackathon.

---

## 13. Toyota demo principle

Toyota should be a **proactive personalized experience**, not just a chatbot.

The workflow should be able to infer a meaningful recommendation from synthetic:
- Toyota customer profile,
- vehicle data,
- lease/loan data,
- usage data,
- Nessie-derived financial context.

The primary customer experience should surface:

```text
YOUR NEXT MOVE

Recommended: BUY CURRENT VEHICLE

Why:
- ...
- ...
- ...

Alternatives:
- Return
- Upgrade
- Refinance / restructure where applicable
- Flexible mobility option where applicable

Next step:
- ...
```

A chat/follow-up explanation may exist, but the customer should not need to know what question to ask.

---

## 14. Nessie role

Nessie must not be a decorative integration.

Pipeline:

```text
Nessie synthetic banking data
        ↓
approved TOOL calls
        ↓
deterministic financial aggregation
        ↓
FinancialContext
        ↓
Toyota recommendation
```

The Toyota recommendation should change when the financial context changes.

This is the Capital One story:

> **The vehicle did not change. The financial reality did. Therefore the recommendation changed.**

---

## 15. Fallback strategy

The demo must not fail because one external service fails.

Required fallbacks:

### Team planner failure
Use static fallback team:
- Coordinator
- Financial Analyst
- Vehicle Analyst
- Decision Specialist
- Reviewer

### Nessie failure
Load `backend/app/fixtures/nessie_demo.json`.

Record source:
- `NESSIE_LIVE`
- or `NESSIE_FALLBACK`

### LLM failure
Use a deterministic fallback execution plan and/or prepared final explanation for the demo scenario.

Never expose a raw 500 error during the demo if a graceful fallback is possible.

---

## 16. Authentication / tenancy

Hackathon only:

- one demo login,
- one workspace,
- no OAuth,
- no SSO,
- no MFA,
- no SCIM,
- no true multi-tenancy.

Example:

```text
Continue as Demo User

Alex Morgan
Toyota Financial Services
Role: Team Builder
```

Backend may use:
- `demo_user_id`
- `workspace_id = "toyota-demo"`

The data model may include `workspace_id` for future extensibility.

---

## 17. Data persistence

Priority:
1. in-memory run state first,
2. SQLite only when the core demo works.

If SQLite is added, keep it small:
- `runs`
- `outputs`
- `audit_logs`

Do not lose hackathon time building ORM abstractions.

---

## 18. Live updates

Preferred:
- frontend polls `GET /api/runs/{run_id}` every 1 second.

Only use SSE if polling is already stable and time remains.

Do not build WebSockets.

Never display private chain-of-thought.

Display short activity summaries instead:
- “Retrieving financial data from Nessie.”
- “Calculating safe mobility budget.”
- “Comparing ownership options.”
- “Generating customer-friendly explanation.”

---

## 19. What must be real

Must work:
- goal → team generation,
- agent cards,
- agent editing,
- permissions/tools/budget display,
- run creation,
- execution plan,
- CODE executor,
- TOOL executor,
- LLM executor,
- Nessie call or reliable fallback,
- financial context calculation,
- Toyota recommendation,
- live task statuses,
- outputs,
- basic cost counter,
- audit log,
- security permission enforcement,
- evaluators,
- Vultr hosting,
- `.tech` public URL.

---

## 20. What may be mocked

May be mocked:
- login,
- historical reports,
- enterprise SSO,
- MFA,
- billing,
- secret-manager UI,
- advanced roles,
- email sending,
- Slack,
- Google Drive,
- long-term memory,
- sandboxed code execution,
- advanced analytics,
- multi-tenant administration.

Mocked functionality must never be presented as fully implemented security.

---

## 21. Definition of done

The hack is “done” when:

1. the public `.tech` URL loads,
2. a user can continue as Demo User,
3. the user can create or select the Toyota team,
4. the team is visibly editable,
5. permissions and budgets are visible,
6. clicking Run starts a real backend run,
7. Nessie live data or a transparent fallback is used,
8. CODE/TOOL/LLM tasks execute,
9. live status updates appear,
10. a Toyota recommendation appears,
11. cost savings / avoided AI calls appear,
12. evaluator results appear,
13. audit events appear,
14. the full demo succeeds three times consecutively.

After condition 14 is met:
**stop adding core features. Polish and rehearse.**

---

## 22. Key judge story

QuestMaster is not another LLM wrapper.

It creates:

```text
TEAM
+
EXECUTION PLAN
```

Then routes each task:

```text
deterministic problem → CODE
data retrieval        → TOOL
reasoning/synthesis   → LLM
```

Security and budget are enforced at every step.

The key lines:

> **Agent defines who is responsible. The platform decides how the work is executed.**

> **The right agent, the right tool, the right data — and nothing more.**

> **AI where judgment is needed. Code where certainty is possible.**
