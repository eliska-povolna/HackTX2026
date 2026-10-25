# QuestMaster follow-up QA

This follow-up continues `overnight-qa-2026-10-25` in both repositories. The user authorized merging when necessary for application verification.

## Implemented

- Portfolio mobility loads all installed Toyota demo customers, deterministically skips no-action customers, derives finance only for relevant customers, scores their options and ranks decision urgency.
- Loan opportunity uses the same Toyota/Nessie pack but evaluates the full portfolio and ranks derived financial fit. It does not submit offers, applications or transactions, and does not determine real credit eligibility.
- One final LLM explanation receives only deterministic scores, reasons and minimized derived summaries. Raw banking data stays inside the financial handler.
- Portfolio assignment uses actual grants, independent of agent IDs or ordering. Plan-time alignment remains separate from runtime enforcement; removed grants fail with PERMISSION_DENIED.
- Portfolio output, per-customer banking provenance and skipped customers are typed results, displayed in a responsive table and persisted without a schema migration using the existing result JSON column.
- Output integrity and minimization checks inspect actual output instead of unconditional PASS badges.
- Recruiting remains NEEDS_SETUP, including recruiting for Toyota; missing ATS/email/calendar capabilities are reported before planning.

## Local verification

- Backend: 94 tests passed.
- Frontend: 72 tests passed.
- Production build and TypeScript checks passed.
- Tests include distinct five-customer mobility vs seven-customer loan workflows, zero-relevance stop, arbitrary/reordered agent IDs, removed permissions, unavailable Nessie, zero model budget, real outbound model payload minimization, rendered portfolio/provenance data and database hydration after clearing in-memory state.

## Deployment verification

Pending live verification after the authorized merge. The initial report's single-customer-only limitation is superseded by this implementation; no claim of successful deployment is made in this pre-deployment snapshot.

## Remaining limits

- Intent recognition still uses the existing constrained classifier; this is not arbitrary workflow composition.
- Sarah/Jordan have mapped live Nessie records; several additional demo profiles use clearly labeled banking fixtures. No financial records were seeded.
- Financial fit scores are transparent demo heuristics, not validated credit-risk models.
