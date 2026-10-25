# QuestMaster API Specification

**Status:** HackTX 2026 MVP contract  
**Purpose:** Freeze the REST contract between Petr's frontend and Eliška's backend.

This file is authoritative for frontend/backend integration.

---

# 1. General rules

Base URL:

```text
${API_BASE_URL}/api
```

Development example:

```text
http://localhost:8000/api
```

Production example:

```text
https://questmaster.tech/api
```

Content type:

```http
Content-Type: application/json
```

All timestamps:
- ISO 8601 UTC strings.

All IDs:
- opaque strings,
- frontend must not infer meaning from ID format.

Money:
- USD,
- represented as JSON numbers,
- round for display only on frontend.

The frontend must not receive:
- API keys,
- raw provider secrets,
- server `.env`,
- hidden model prompts,
- private chain-of-thought.

---

# 2. Enums

## ExecutorType

```text
CODE
TOOL
LLM
```

## RunStatus

```text
PLANNING
RUNNING
REVIEWING
COMPLETED
FAILED
BUDGET_LIMIT
APPROVAL_REQUIRED
```

## TaskStatus

```text
WAITING
RUNNING
COMPLETED
FAILED
SKIPPED
BLOCKED
```

## PermissionDecision

```text
ALLOWED
DENIED
APPROVAL_REQUIRED
```

## DataSource

```text
NESSIE_LIVE
NESSIE_FALLBACK
TOYOTA_SYNTHETIC
LOCAL_CODE
LLM
CACHE
```

## QualityMode

```text
normal
strict
```

## RiskLevel

```text
low
medium
high
```

---

# 3. Shared models

## 3.1 BudgetPolicy

```json
{
  "max_llm_cost_usd": 0.05,
  "max_llm_calls": 2
}
```

Fields:

| Field | Type | Required |
|---|---|---:|
| max_llm_cost_usd | number | yes |
| max_llm_calls | integer | yes |

---

## 3.2 AgentPermissions

```json
{
  "data_scopes": [
    "financial_summary"
  ],
  "actions": [
    "read",
    "calculate"
  ]
}
```

`data_scopes` and `actions` are strings for MVP flexibility.

Recommended data scopes:
- `toyota_customer_profile`
- `vehicle_data`
- `lease_data`
- `usage_data`
- `nessie_raw_financial`
- `financial_summary`

Recommended actions:
- `read`
- `calculate`
- `synthesize`
- `recommend`
- `external_action`

---

## 3.3 AgentDefinition

```json
{
  "id": "financial-agent",
  "name": "Maya",
  "role": "Financial Context Analyst",
  "purpose": "Determine what mobility options are financially safe.",
  "skills": [
    "cashflow_analysis",
    "affordability"
  ],
  "instructions": "Use only approved tools and data scopes.",
  "allowed_tools": [
    "nessie_accounts",
    "nessie_purchases",
    "build_financial_context"
  ],
  "permissions": {
    "data_scopes": [
      "nessie_raw_financial",
      "financial_summary"
    ],
    "actions": [
      "read",
      "calculate"
    ]
  },
  "guardrails": [
    "Do not expose raw transactions to downstream agents."
  ],
  "model_policy": "auto",
  "budget": {
    "max_llm_cost_usd": 0.05,
    "max_llm_calls": 2
  },
  "estimated_cost_usd": 0.01
}
```

Required fields:
- `id`
- `name`
- `role`
- `purpose`
- `skills`
- `allowed_tools`
- `permissions`
- `budget`

Optional/default:
- `instructions = ""`
- `guardrails = []`
- `model_policy = "auto"`
- `estimated_cost_usd = 0`

---

## 3.4 TeamDefinition

```json
{
  "id": "team-toyota-mobility",
  "team_name": "Toyota Mobility Decision Team",
  "summary": "Proactively determines the customer's smartest mobility decision.",
  "agents": []
}
```

---

## 3.5 TaskDefinition

```json
{
  "id": "task-financial-context",
  "name": "Build financial context",
  "assigned_agent_id": "financial-agent",
  "executor": "TOOL",
  "action": "build_financial_context",
  "depends_on": [],
  "risk_level": "medium"
}
```

Fields:

| Field | Type |
|---|---|
| id | string |
| name | string |
| assigned_agent_id | string |
| executor | `CODE \| TOOL \| LLM` |
| action | string |
| depends_on | string[] |
| risk_level | `low \| medium \| high` |

---

## 3.6 ExecutionPlan

```json
{
  "tasks": [
    {
      "id": "load-financial-data",
      "name": "Load banking data",
      "assigned_agent_id": "financial-agent",
      "executor": "TOOL",
      "action": "nessie_load_customer_financials",
      "depends_on": [],
      "risk_level": "medium"
    },
    {
      "id": "calculate-affordability",
      "name": "Calculate safe mobility budget",
      "assigned_agent_id": "financial-agent",
      "executor": "CODE",
      "action": "calculate_affordability",
      "depends_on": ["load-financial-data"],
      "risk_level": "medium"
    },
    {
      "id": "synthesize-recommendation",
      "name": "Synthesize mobility recommendation",
      "assigned_agent_id": "decision-agent",
      "executor": "LLM",
      "action": "synthesize_recommendation",
      "depends_on": ["calculate-affordability"],
      "risk_level": "medium"
    }
  ]
}
```

---

## 3.7 FinancialContext

This is the safe derived object downstream agents may consume.

```json
{
  "monthly_income": 4850.0,
  "fixed_expenses": 2740.0,
  "variable_spending": 980.0,
  "monthly_free_cashflow": 1130.0,
  "cash_reserve": 3400.0,
  "safe_mobility_budget": 530.0,
  "emergency_runway_months": 1.8,
  "debt_burden_ratio": 0.24,
  "financial_stress": "MEDIUM"
}
```

Raw transaction records should not be included here.

---

## 3.8 RecommendationOption

```json
{
  "id": "buy-current",
  "label": "Buy current vehicle",
  "monthly_cost": 495.0,
  "three_year_cost": 17820.0,
  "fit_score": 91,
  "safe_for_budget": true,
  "tradeoffs": [
    "Keeps a vehicle that still fits expected family needs.",
    "Avoids the higher monthly cost of upgrading."
  ]
}
```

---

## 3.9 ToyotaRecommendation

```json
{
  "recommended_action": "BUY_CURRENT_VEHICLE",
  "confidence": 0.91,
  "headline": "Buying your current RAV4 is your strongest option.",
  "summary": "Your current vehicle still fits your needs and is within your safe mobility budget.",
  "reasons": [
    "The estimated monthly payment stays below your safe mobility budget.",
    "Your current vehicle remains suitable for expected family needs."
  ],
  "assumptions": [
    "Synthetic vehicle value and financing terms are used for the hackathon demo."
  ],
  "next_step": {
    "label": "Review buyout financing options",
    "action_type": "FOLLOW_UP"
  },
  "options": []
}
```

Recommended action enum values for demo:
- `BUY_CURRENT_VEHICLE`
- `RETURN_VEHICLE`
- `UPGRADE_VEHICLE`
- `REFINANCE`
- `FLEXIBLE_MOBILITY`
- `FOLLOW_UP_REQUIRED`

---

## 3.10 AuditEvent

```json
{
  "id": "evt-001",
  "timestamp": "2026-10-25T02:03:11Z",
  "run_id": "run-001",
  "agent_id": "financial-agent",
  "event_type": "TOOL_CALL",
  "executor_type": "TOOL",
  "resource": "nessie_accounts",
  "result": "ALLOWED",
  "message": "Retrieved account data from Nessie.",
  "cost_usd": 0.0
}
```

Suggested event types:
- `RUN_CREATED`
- `TEAM_PLANNED`
- `TASK_STARTED`
- `TASK_COMPLETED`
- `TASK_FAILED`
- `TOOL_CALL`
- `PERMISSION_CHECK`
- `DATA_MINIMIZATION`
- `LLM_CALL`
- `BUDGET_CHECK`
- `EVALUATOR_RESULT`
- `RUN_COMPLETED`

---

## 3.11 EvaluatorResult

```json
{
  "name": "permission_compliance",
  "label": "Tool permissions",
  "status": "PASS",
  "details": "All tool calls matched agent permissions."
}
```

Status:
- `PASS`
- `WARN`
- `FAIL`

---

# 4. Health endpoint

## GET `/api/health`

Purpose:
- deployment smoke test,
- frontend connectivity,
- early Vultr verification.

Response `200`:

```json
{
  "status": "ok",
  "service": "questmaster-api",
  "version": "0.1.0"
}
```

This endpoint must never call LLM or Nessie.

---

# 5. Session endpoint

Authentication is mocked for hackathon.

## POST `/api/demo-session`

Request:

```json
{}
```

Response:

```json
{
  "user": {
    "id": "demo-user",
    "name": "Alex Morgan",
    "organization": "Toyota Financial Services",
    "role": "Team Builder"
  },
  "workspace_id": "toyota-demo"
}
```

Frontend may also implement this entirely client-side.

No production auth is required.

---

# 6. Templates

## GET `/api/templates`

Returns available team templates.

Response:

```json
{
  "templates": [
    {
      "id": "toyota-mobility",
      "name": "Toyota Mobility Decision Team",
      "description": "Proactively identifies the customer's smartest mobility decision.",
      "category": "Mobility",
      "is_demo": true
    },
    {
      "id": "customer-support",
      "name": "Customer Support Team",
      "description": "Example reusable business template.",
      "category": "Operations",
      "is_demo": false
    }
  ]
}
```

Only Toyota needs to be fully functional.

---

# 7. Team planning

## POST `/api/teams/plan`

Purpose:
Convert a user goal into an editable team definition.

Request:

```json
{
  "goal": "Proactively determine the smartest next mobility decision for this Toyota customer.",
  "max_team_size": 5,
  "budget_preference": "balanced"
}
```

Response `200`:

```json
{
  "team": {
    "id": "generated-team-001",
    "team_name": "Toyota Mobility Decision Team",
    "summary": "Combines financial, vehicle and mobility context to recommend the best next step.",
    "agents": [
      {
        "id": "financial-agent",
        "name": "Maya",
        "role": "Financial Context Analyst",
        "purpose": "Determine what options are financially safe.",
        "skills": ["cashflow_analysis", "affordability"],
        "instructions": "Minimize exposure of raw banking data.",
        "allowed_tools": [
          "nessie_load_customer_financials",
          "build_financial_context"
        ],
        "permissions": {
          "data_scopes": [
            "nessie_raw_financial",
            "financial_summary"
          ],
          "actions": ["read", "calculate"]
        },
        "guardrails": [
          "Do not expose raw transactions to downstream agents."
        ],
        "model_policy": "auto",
        "budget": {
          "max_llm_cost_usd": 0.05,
          "max_llm_calls": 1
        },
        "estimated_cost_usd": 0.005
      }
    ]
  },
  "planner_source": "LLM",
  "fallback_used": false
}
```

If planner fails:
- return `200`,
- return fallback team,
- `planner_source = "FALLBACK"`,
- `fallback_used = true`.

Do not return `500` merely because team planning failed.

---

# 8. Demo customers

## GET `/api/demo/customers`

Response:

```json
{
  "customers": [
    {
      "id": "customer-stable",
      "name": "Sarah Miller",
      "vehicle": "2023 Toyota RAV4",
      "scenario_label": "Financially stable",
      "description": "Strong reserve and stable cash flow."
    },
    {
      "id": "customer-stressed",
      "name": "Jordan Lee",
      "vehicle": "2023 Toyota RAV4",
      "scenario_label": "Financially constrained",
      "description": "Same vehicle context, weaker financial buffer."
    }
  ]
}
```

## GET `/api/demo/customers/{customer_id}`

Returns synthetic Toyota-facing data only.

Example:

```json
{
  "id": "customer-stable",
  "name": "Sarah Miller",
  "vehicle": {
    "year": 2023,
    "make": "Toyota",
    "model": "RAV4",
    "mileage": 31000
  },
  "contract": {
    "type": "LEASE",
    "months_remaining": 2,
    "monthly_payment": 469.0,
    "buyout_amount": 24500.0
  },
  "usage": {
    "monthly_miles_current": 650,
    "monthly_miles_six_months_ago": 1050,
    "change_percent": -38.1
  },
  "nessie_customer_id": "nessie-demo-001"
}
```

Do not include raw Nessie data here.

---

# 9. Run creation

## POST `/api/runs`

Purpose:
Start one end-to-end team execution.

Request:

```json
{
  "goal": "Determine this customer's smartest next mobility decision.",
  "team": {
    "id": "generated-team-001",
    "team_name": "Toyota Mobility Decision Team",
    "summary": "...",
    "agents": []
  },
  "context": {
    "customer_id": "customer-stable"
  },
  "quality_mode": "normal"
}
```

Response `202`:

```json
{
  "run_id": "run-001",
  "status": "PLANNING"
}
```

Frontend then polls:

```text
GET /api/runs/run-001
```

Duplicate-click protection:
- frontend disables Run while request is pending,
- backend should avoid starting an identical duplicate request if simple to implement.

---

# 10. Run status

## GET `/api/runs/{run_id}`

Example response:

```json
{
  "run_id": "run-001",
  "status": "RUNNING",
  "goal": "Determine this customer's smartest next mobility decision.",
  "created_at": "2026-10-25T02:03:00Z",
  "updated_at": "2026-10-25T02:03:12Z",

  "plan": {
    "tasks": []
  },

  "tasks": [
    {
      "id": "load-financial-data",
      "name": "Load banking data",
      "assigned_agent_id": "financial-agent",
      "executor": "TOOL",
      "action": "nessie_load_customer_financials",
      "status": "COMPLETED",
      "activity_summary": "Retrieved financial data from Nessie.",
      "tool": "nessie_accounts",
      "duration_ms": 420,
      "cost_usd": 0.0,
      "data_source": "NESSIE_LIVE"
    },
    {
      "id": "calculate-affordability",
      "name": "Calculate safe mobility budget",
      "assigned_agent_id": "financial-agent",
      "executor": "CODE",
      "action": "calculate_affordability",
      "status": "RUNNING",
      "activity_summary": "Calculating safe mobility budget with local code.",
      "tool": null,
      "duration_ms": null,
      "cost_usd": 0.0,
      "data_source": "LOCAL_CODE"
    }
  ],

  "metrics": {
    "llm_calls": 1,
    "llm_calls_avoided": 4,
    "input_tokens": 1230,
    "output_tokens": 311,
    "estimated_ai_cost_usd": 0.0048,
    "estimated_baseline_cost_usd": 0.028,
    "estimated_cost_saved_usd": 0.0232,
    "code_tasks": 2,
    "tool_tasks": 2,
    "llm_tasks": 1
  },

  "financial_context": null,
  "recommendation": null,
  "outputs": [],
  "evaluators": [],
  "audit_events": []
}
```

When complete:
- `status = COMPLETED`,
- `financial_context` populated,
- `recommendation` populated,
- `outputs` populated,
- `evaluators` populated.

---

# 11. Outputs

Output item:

```json
{
  "id": "out-001",
  "title": "Mobility Recommendation",
  "agent_id": "decision-agent",
  "type": "RECOMMENDATION",
  "summary": "Buy the current vehicle.",
  "content": "Buying the current RAV4 is the strongest option because...",
  "timestamp": "2026-10-25T02:04:33Z",
  "cost_usd": 0.0041,
  "source_type": "LLM"
}
```

Suggested `type` values:
- `FINANCIAL_CONTEXT`
- `ANALYSIS`
- `RECOMMENDATION`
- `REPORT`
- `SECURITY_EVENT`

---

# 12. Audit log

## GET `/api/runs/{run_id}/audit`

Optional endpoint.

If not implemented separately, audit events may be embedded in the run status response.

Response:

```json
{
  "events": [
    {
      "id": "evt-001",
      "timestamp": "2026-10-25T02:03:11Z",
      "run_id": "run-001",
      "agent_id": "financial-agent",
      "event_type": "PERMISSION_CHECK",
      "executor_type": "TOOL",
      "resource": "nessie_accounts",
      "result": "ALLOWED",
      "message": "Financial Agent may access Nessie account data.",
      "cost_usd": 0
    },
    {
      "id": "evt-002",
      "timestamp": "2026-10-25T02:03:14Z",
      "run_id": "run-001",
      "agent_id": "decision-agent",
      "event_type": "PERMISSION_CHECK",
      "executor_type": "TOOL",
      "resource": "nessie_raw_purchases",
      "result": "DENIED",
      "message": "Decision Agent may receive financial_summary but not raw purchases.",
      "cost_usd": 0
    }
  ]
}
```

The second event is useful for security demonstration, but do not force a real failure in the main demo path.

---

# 13. Reports

## GET `/api/runs`

Optional after core flow works.

Response:

```json
{
  "runs": [
    {
      "run_id": "run-001",
      "goal": "Determine this customer's smartest next mobility decision.",
      "team_name": "Toyota Mobility Decision Team",
      "status": "COMPLETED",
      "duration_ms": 8400,
      "estimated_ai_cost_usd": 0.008,
      "llm_calls": 2
    }
  ]
}
```

If persistence is not ready:
- seed demo history client-side.

---

# 14. Integration status

## GET `/api/integrations`

Response:

```json
{
  "integrations": [
    {
      "id": "llm",
      "label": "AI Model Provider",
      "status": "CONNECTED"
    },
    {
      "id": "nessie",
      "label": "Capital One Nessie",
      "status": "CONNECTED"
    },
    {
      "id": "vultr",
      "label": "Vultr",
      "status": "HOSTED"
    }
  ]
}
```

Never return secret values.

---

# 15. Standard error format

For actual request validation or unrecoverable errors:

```json
{
  "error": {
    "code": "INVALID_REQUEST",
    "message": "Customer ID is required.",
    "recoverable": true
  }
}
```

Recommended codes:
- `INVALID_REQUEST`
- `NOT_FOUND`
- `PERMISSION_DENIED`
- `BUDGET_LIMIT`
- `TOOL_UNAVAILABLE`
- `LLM_UNAVAILABLE`
- `RUN_FAILED`

The backend should prefer graceful fallback for:
- planner failure,
- Nessie failure,
- demo LLM failure where a safe fallback exists.

---

# 16. Frontend polling behavior

After `POST /api/runs`:

1. navigate to run page,
2. poll `GET /api/runs/{run_id}` every 1000 ms,
3. stop polling when status is one of:
   - `COMPLETED`
   - `FAILED`
   - `BUDGET_LIMIT`
   - `APPROVAL_REQUIRED`

Do not use WebSockets for MVP.

---

# 17. Contract freeze rule

Frontend mocks must mirror this spec.

Backend responses must mirror this spec.

If a field changes:
1. change this file first,
2. update shared fixture,
3. update both sides.

Shared fixture files should include:
- `plan_team_response.json`
- `run_running_response.json`
- `run_completed_response.json`
- `toyota_customer_stable.json`
- `toyota_customer_stressed.json`
