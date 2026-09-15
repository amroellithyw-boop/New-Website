# Feature inventory: every legacy capability and where it stands

Every feature found in the ProfitForge repository, mapped to its ForgeOS status.
Nothing useful has been dropped without a reason stated here.

**Status key.** Ported: rebuilt on the deterministic engine with tests.
Improved: ported and a defect fixed on the way. Planned: designed, not yet
built, with the module it will live in. Superseded: a better mechanism now
exists. Dropped: not worth carrying, with the reason.

## Intelligence and personalisation

| Legacy feature | Status | Where |
| --- | --- | --- |
| Client intake questionnaire, 60 fields | Ported | `forge/clients/profile.py`, `ClientProfile.from_legacy_intake` |
| Industry intelligence, 22 NAICS with WSIB, seasonality, CCA, errors | Ported | `forge/industry/` |
| Cost structures, 33 industries, 5 revenue tiers | Ported | `forge/industry/` |
| Custom industries (Supabase CRUD) | Planned | Add rows to the industry data; a loader for operator-defined packs |
| Services catalogue, 13 packages | Improved | Each service now maps to workflows and control categories in scope |
| Provincial tax table, 10 provinces | Improved | 13 provinces and territories, Decimal rates, `forge/clients/tax_regions.py` |
| Client-info registry with cross-form autofill | Superseded | One profile object with JSON round-trip |
| `ai_context_notes` per client | Ported | `ClientProfile.ai_context_notes`, reaches models as data |
| Knowledge base per client | Ported | `forge/clients/knowledge.py` |
| Onboarding plan generation | Ported | `forge/agents/communications.py`, structured output |
| Health score | Improved | Now scored on engine-proven facts, `forge/cfo/working_capital.py` |

## Documents and coding

| Legacy feature | Status | Where |
| --- | --- | --- |
| Three-pass document pipeline | Improved | `forge/documents/pipeline.py`; tax is now computed by code, entries validated to balance |
| Vision extraction of statements, invoices, receipts, payroll | Ported | Pass 1, `ExtractedDocument` |
| Credit-card refund section logic | Ported | Extraction prompt plus a deterministic negative-amount check |
| Per-cardholder sections | Ported | `ExtractedTransaction.cardholder` |
| Salvage mode for truncated output | Ported | `salvage_json` |
| Known Canadian vendor patterns | Improved | Deterministic table checked before any model, `forge/documents/vendors.py` |
| Vendor research with web search | Ported | Pass 2, only for vendors the table and cache do not know |
| Vendor cache with operator corrections | Improved | Corrections become precedents the risk router honours |
| Chart-of-accounts mapping and AI suggest | Planned | Precedent store exists; the QuickBooks account resolver is next |
| Three-AI journal entry review | Superseded | The review hierarchy, with independence across vendors |
| Auto bank rules and auto cleanup pushed to QuickBooks | Dropped for now | Write-back is autonomy level A2; not before supervised evidence |

## Accounting and compliance

| Legacy feature | Status | Where |
| --- | --- | --- |
| Canadian payroll, CPP, CPP2, EI, federal and Ontario tax | Improved | Three defects fixed, `forge/payroll/` |
| T4 boxes including 16A | Planned | Year-end workflow |
| HST centre, filings, ITCs | Planned | `tax_readiness` workflow; two tax controls exist today |
| WSIB filings | Planned | `wsib` workflow; rate-group control exists today |
| CCA schedule with half-year rule | Ported | `forge/cfo/schedules.py` |
| Deferred revenue tracker with monthly recognition | Ported | `deferred_revenue_schedule`, exact allocation |
| Loan amortisation with lender reconciliation | Ported | `amortisation_schedule`, `reconcile_to_lender`, missed-payment estimate |
| Period locks | Ported | `Period.is_closed` and two period-control controls |
| Month-end close checklist | Planned | `close` workflow on the work-item state machine |
| Year-end checklist generation | Planned | `year_end` workflow |
| Bank reconciliation | Improved | Four-pass matcher with an outstanding-items proof |
| AR and AP centres | Improved | Aging engine plus eleven receivable and payable controls |
| Journal entry posting to QuickBooks | Dropped for now | No balance check and no idempotency in the legacy code; returns at A2 with both |
| Audit trail | Improved | Every state transition, agent decision and model call |
| Issue tracker | Superseded | Findings and work items |
| Follow-ups | Ported | Open questions in the knowledge store |

## CFO and reporting

| Legacy feature | Status | Where |
| --- | --- | --- |
| Working capital scorecard, DSO, DPO, CCC, ratios | Ported | `forge/cfo/working_capital.py` |
| 13-week cash forecast | Ported | `forge/cfo/cash_forecast.py`, deterministic from aging, payroll cadence, recurring vendors and debt |
| CFO hub: insights, KPIs, quick wins, benchmarks | Partly ported | Benchmarks and cash health are engine functions; narrative insights are a planned `cfo_brief` workflow |
| Forecast and budget centre | Planned | Budget variance control exists; driver models are the `planning` workflow |
| Management reporting | Improved | The control review report, plus the evidence bundle |
| Client questions email | Ported | `draft_client_questions` |
| Report review by AI | Superseded | Review hierarchy |

## Platform

| Legacy feature | Status | Where |
| --- | --- | --- |
| QuickBooks OAuth and token refresh | Improved | Read-only, rotated token persisted first, `forge/connectors/qbo_auth.py` |
| QuickBooks account and transaction sync | Partly ported | Accounts and journal entries mapped; ten entity mappers remain |
| Supabase persistence, 17 tables | Planned | Canonical model is the schema; a persistence layer maps to it |
| Express API, 105 endpoints | Planned | FastAPI over the pipeline; CLI covers every capability today |
| React interface, 132 components | Dropped | 33,000 lines in one file; rebuild against the API |
| Shared-secret authentication | Dropped | Managed auth with accounts and roles before client two |
| Browser local storage for client data | Dropped | Server-side tenant isolation |
| Plaintext token storage | Dropped | Managed secrets store |
| Email via Resend | Planned | Communications drafts exist; sending is an action at A2 |
| Kill-switch middleware, patch scripts | Dropped | Applied history, not code |
