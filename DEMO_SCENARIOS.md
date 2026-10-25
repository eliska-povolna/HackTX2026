# QuestMaster — Demo Scenarios

These scenarios exist for three reasons:

1. give frontend and backend a shared test target,
2. prove Toyota personalization,
3. prove Nessie materially changes the recommendation.

All data is synthetic / sandbox data.

---

# Demo principle

The main demo should prove:

> **Same vehicle context does not imply the same recommendation. Financial reality matters.**

The customer should not need to explain their life manually in chat.

The system should proactively combine:
- Toyota customer data,
- vehicle data,
- contract data,
- usage data,
- Nessie-derived financial context.

---

# Scenario A — Stable financial position

## Customer
**Name:** Sarah Miller  
**Customer ID:** `customer-stable`

## Toyota / vehicle data

```json
{
  "vehicle": {
    "year": 2023,
    "make": "Toyota",
    "model": "RAV4",
    "mileage": 31000
  },
  "contract": {
    "type": "LEASE",
    "months_remaining": 2,
    "monthly_payment": 469,
    "buyout_amount": 24500
  },
  "usage": {
    "monthly_miles_current": 650,
    "monthly_miles_six_months_ago": 1050,
    "change_percent": -38.1
  },
  "market_assumptions": {
    "estimated_vehicle_value": 26800,
    "estimated_upgrade_payment": 585
  }
}
```

## Nessie-derived financial context

Target derived result:

```json
{
  "monthly_income": 4850,
  "fixed_expenses": 2250,
  "variable_spending": 900,
  "monthly_free_cashflow": 1700,
  "cash_reserve": 9000,
  "safe_mobility_budget": 560,
  "emergency_runway_months": 4.7,
  "debt_burden_ratio": 0.16,
  "financial_stress": "LOW"
}
```

The raw Nessie fixture should be designed so deterministic aggregation approximately produces these values.

## Expected recommendation

Preferred:

```text
BUY CURRENT VEHICLE
```

Rationale:
- buyout is below estimated vehicle value,
- expected monthly ownership cost fits safe mobility budget,
- vehicle remains useful,
- customer has adequate reserve,
- upgrade exceeds preferred safe budget or creates worse economics,
- reduced usage does not automatically justify abandoning a vehicle that still fits the customer.

## Expected alternatives
- Upgrade — lower fit.
- Return / flexible mobility — plausible but not strongest.
- Follow-up — only if key financing assumptions are unavailable.

## Toyota judge story

> Sarah’s lease is ending, but Next Move is not based only on the lease. Her usage has changed and her financial position is strong. The system proactively identifies buying her current RAV4 as the strongest next move.

---

# Scenario B — Same Toyota, constrained financial position

## Customer
**Name:** Jordan Lee  
**Customer ID:** `customer-stressed`

## Toyota / vehicle data

Use nearly the same or identical Toyota context as Scenario A.

```json
{
  "vehicle": {
    "year": 2023,
    "make": "Toyota",
    "model": "RAV4",
    "mileage": 31000
  },
  "contract": {
    "type": "LEASE",
    "months_remaining": 2,
    "monthly_payment": 469,
    "buyout_amount": 24500
  },
  "usage": {
    "monthly_miles_current": 650,
    "monthly_miles_six_months_ago": 1050,
    "change_percent": -38.1
  },
  "market_assumptions": {
    "estimated_vehicle_value": 26800,
    "estimated_upgrade_payment": 585
  }
}
```

## Nessie-derived financial context

Target:

```json
{
  "monthly_income": 4850,
  "fixed_expenses": 3100,
  "variable_spending": 1050,
  "monthly_free_cashflow": 700,
  "cash_reserve": 1100,
  "safe_mobility_budget": 390,
  "emergency_runway_months": 0.9,
  "debt_burden_ratio": 0.31,
  "financial_stress": "HIGH"
}
```

## Expected recommendation

Preferred:

```text
RETURN_VEHICLE
```

or, if the implementation has a flexible-mobility option:

```text
FLEXIBLE_MOBILITY
```

Rationale:
- current/future ownership cost exceeds safe mobility budget,
- emergency runway is weak,
- usage has materially decreased,
- upgrading is clearly inappropriate,
- preserving liquidity is more important than capturing vehicle equity.

## Judge story

> The car did not change. The lease did not change. Nessie changed the decision because Jordan’s financial reality is different.

This is the core Capital One demonstration.

---

# Scenario C — Optional predictive-maintenance extension

Only build if Scenario A and B are stable.

## Customer
**ID:** `customer-maintenance`

## Key signal
- older/high-mileage Toyota,
- predicted major maintenance within 4–6 months,
- estimated cost ~$1,200,
- moderate financial buffer.

## Desired platform behavior

1. Vehicle agent/tool identifies maintenance risk.
2. Financial agent builds Nessie context.
3. CODE executor compares:
   - pay later,
   - save monthly now,
   - maintenance financing,
   - trade/upgrade if economically justified.
4. LLM explains trade-offs only after deterministic calculations.

Potential recommendation:

```text
Start a $200/month maintenance reserve for six months.
```

This scenario aligns with Toyota’s “predictive maintenance financing” idea.

Do not implement unless core demo is complete.

---

# Platform demo — agent team

The Toyota team should typically contain 4–5 members.

Suggested team:

## 1. Context Agent
Purpose:
- identify relevant customer/vehicle signals,
- decide which information is needed.

Allowed data:
- Toyota profile,
- vehicle,
- lease,
- usage.

Should not receive:
- raw Nessie transactions.

Typical executor:
- CODE and/or lightweight LLM.

---

## 2. Financial Context Agent
Purpose:
- convert Nessie data into a safe financial summary.

Allowed tools:
- Nessie customer/accounts/transactions,
- deterministic financial calculators.

Allowed data:
- raw Nessie financial data.

Output:
- `FinancialContext`.

Must not:
- expose raw transactions downstream.

Typical executor:
- TOOL + CODE.

LLM should usually not be required.

---

## 3. Vehicle / Mobility Agent
Purpose:
- evaluate vehicle fit, usage, contract, and mobility alternatives.

Allowed data:
- Toyota profile,
- vehicle,
- lease,
- usage,
- derived financial summary if needed.

Typical executor:
- CODE + optional LLM for ambiguous interpretation.

---

## 4. Decision Agent
Purpose:
- combine approved derived outputs and produce a recommendation.

Allowed data:
- financial summary,
- vehicle/mobility summary,
- option calculations.

Forbidden:
- raw Nessie purchase rows.

Typical executor:
- LLM for synthesis,
- CODE for final constraint validation.

---

## 5. Reviewer
Purpose:
- optional high-risk/strict-mode review.

Run only if:
- quality mode is strict,
- risk is high,
- external action would be proposed.

Normal demo:
- reviewer may be skipped,
- show:
  `Reviewer skipped — normal risk`
  `Estimated AI cost saved: ...`

---

# Security demo

Demonstrate least privilege without breaking the main flow.

Example audit events:

```text
Financial Agent → Nessie account data → ALLOWED
Financial Agent → FinancialContext → CREATED
Decision Agent → raw Nessie purchases → DENIED
Decision Agent → FinancialContext → ALLOWED
```

The DENIED event may be generated by a safe test/check rather than a real accidental failure.

The point is to show:
- agent boundaries,
- data minimization,
- permission enforcement in code.

---

# Cost demo

Desired task mix:

```text
Load financial data           TOOL
Aggregate monthly income      CODE
Calculate safe mobility       CODE
Compare ownership options     CODE
Interpret ambiguous context   LLM
Generate explanation          LLM
Reviewer                      SKIPPED
```

The UI should be able to show something like:

```text
AI calls avoided: 4
CODE tasks: 3
TOOL tasks: 1
LLM tasks: 2
Estimated AI cost saved: $0.02
```

Exact savings may be an estimate based on a configured baseline.

Never claim savings as independently measured if they are only estimated.

---

# Demo order for judges

## Toyota
1. Open customer.
2. Show proactive “Your Next Move”.
3. Compare options.
4. Explain why recommendation fits this customer.
5. Show Scenario B briefly to prove personalization.
6. Only then show the platform beneath it if time remains.

## NMC2
1. Open team builder.
2. Show team generated from business goal.
3. Show tools, permissions, budgets, guardrails.
4. Run team.
5. Show CODE/TOOL/LLM routing.
6. Show observability/evaluators/audit.
7. Show Toyota as one reusable template.

## Capital One
1. Show identical/nearly identical Toyota situation for two customers.
2. Show derived Nessie financial contexts.
3. Show different recommendations.
4. State:
   “The vehicle didn’t change. Nessie changed the decision.”
5. Show raw financial data is minimized and not exposed to Decision Agent.

---

# Acceptance tests

Before demo, verify:

### Scenario A
- [ ] completes,
- [ ] recommendation = buy current vehicle or expected equivalent,
- [ ] Nessie context = low financial stress,
- [ ] evaluators pass.

### Scenario B
- [ ] completes,
- [ ] recommendation differs from Scenario A,
- [ ] Nessie context = high financial stress,
- [ ] evaluators pass.

### Security
- [ ] raw Nessie data not present in final recommendation payload,
- [ ] permission denial can be shown,
- [ ] no secrets appear.

### Cost
- [ ] CODE task visible,
- [ ] TOOL task visible,
- [ ] LLM task visible,
- [ ] avoided-call/savings metric visible.

### Reliability
- [ ] each main scenario succeeds three consecutive times.
