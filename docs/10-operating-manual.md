# Operating manual: how to run it, what it cannot do yet, and why it is built this way

This is the document to read when you sit down to use ForgeOS. It answers, in
order: what is in the finance team, whether every blueprint role and workflow
is present, how to run it step by step, how it learns per client, what it
cannot do today and why, why it is built the way it is, and what I think is
not right yet.

## 1. The finance team: every role from the blueprints

Every role has a written mandate in `forge/agents/roles.py`, a place in the
review chain in `forge/workitems/risk.py`, and a test that fails if a role is
added without a mandate.

| Role | Prepares | Reviews at | Specialist for |
| --- | --- | --- | --- |
| Bookkeeper | integrity, classification, reconciliation, period control | R0 | |
| AP specialist | accounts payable | | |
| AR specialist | accounts receivable | | |
| Payroll specialist | payroll | | payroll findings at R3 and above |
| Close accountant | close, fixed assets, variance | | |
| Accounting manager | | R1 and above | |
| Controller | | R2 and above | |
| Tax specialist | tax | | tax findings at R3 and above |
| Treasury specialist | treasury, debt | | treasury and debt at R3 and above |
| FP&A analyst | planning | | |
| FP&A manager | | | planning at R3 and above |
| Commercial analyst | benchmark, profitability | | |
| Finance business partner | | | benchmark, profitability, risk at R3 and above |
| VP Finance | | R3 and above | |
| CFO | | R4 | |
| Adversary | | every R2 and above, from a different model family | |
| Policy reviewer | | every R3 and above | |
| Evidence auditor | | every R3 and above | |
| Orchestrator | routes, budgets, schedules | | |
| Human | | final approver at R3 and above; sole executor below A2 | |

## 2. Every workflow from the blueprints

| Blueprint workflow | Built as | Command |
| --- | --- | --- |
| Continuous controller | 56 controls, risk router, review hierarchy | `forge run`, `forge review` |
| Documents and coding | three-pass document pipeline, vendor table, tax split | `forge document` |
| Bank reconciliation | four-pass matcher with an unexplained-difference proof | inside every run |
| Collections | aging, overdue findings, drafted collection emails | inside every run |
| Payables | duplicate, unapplied, no-bill and terms controls, drafted recovery | inside every run |
| Payroll | 2026 CPP, CPP2, EI, tax; remittance and tie-out controls; remittance calendar | inside every run |
| Month-end close | sixteen-task checklist proven from the engine; drafted adjustments | `forge close` |
| Tax readiness | HST return working paper, filing calendar, corporate provision, slips | `forge tax` |
| Cash forecast and treasury | thirteen-week forecast, working capital scorecard, debt controls | `forge forecast` |
| FP&A and profitability | job profitability, margin-outlier control, benchmark controls | inside every run |
| CFO brief | the five things that matter, from the engine | `forge brief` |
| Onboarding | intake to profile to policy; onboarding plan draft | `forge onboard` |
| Learning | outcome log per client; suppressions and known patterns | `forge outcome` |
| Firm operations | job plan per client, firm queue, daily brief | `forge run-all` |
| Sales | pricing, proposal, outreach, pipeline | `forge sales`, `forge brief --proposal` |

## 3. Step by step

**Once.**

```bash
cd services/forge-core
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
forge bench            # must print "Release gate: PASS"
forge providers        # shows which model vendors are configured
```

Set at least two vendor keys in your shell so reviews are independent, for
example `ANTHROPIC_API_KEY` and `XAI_API_KEY`. Install Ollama and pull a model
if you want free local extraction. `forge providers` shows the routing that
results.

**Per client, once.**

```bash
forge onboard intake.json --out clients/<id>.json
```

The intake is your existing questionnaire. The profile it writes is the single
source of personalisation: province sets every tax rate, revenue sets the
benchmark tier, industry sets the cost structure and the expected controls,
services set the workflows and the job plan.

**Every day.**

```bash
forge run-all clients/ --pipeline data/pipeline.json
```

This runs every client, prints the daily brief and the queue. Work the queue
top down. Priority 1 is approvals waiting on you and books that do not tie.
Priority 2 is close blockers, deadlines within a week, and cash going negative.
Priority 3 is deadlines within a month and material findings. Priority 4 is
sales follow-ups.

**For one client, when something needs attention.**

```bash
forge run   --profile clients/<id>.json --data-dir data
forge close --profile clients/<id>.json --data-dir data
forge brief --profile clients/<id>.json --data-dir data
```

**Every time you decide something.**

```bash
forge outcome <finding-id> accepted  --data-dir data --by <you>
forge outcome <finding-id> dismissed --data-dir data --by <you> --reason "..."
```

This is the step that makes the system yours. Skip it and it stays generic.

**Every month.**

```bash
forge close --profile clients/<id>.json
forge tax return --profile clients/<id>.json --period-end <month-end> --months 3
```

**Every quarter and year.**

```bash
forge tax calendar  --profile clients/<id>.json
forge tax provision --profile clients/<id>.json
forge tax slips     --profile clients/<id>.json
```

**To sell.**

```bash
forge sales add --prospect-id <id> --name "<business>" --naics <code>
forge sales outreach --prospect-id <id>
forge sales move --prospect-id <id> --stage diagnostic_booked
forge brief --proposal --profile clients/<id>.json
forge sales due
```

The diagnostic is the product that sells the retainer. Run it, show the
recoverable cash, send the proposal.

## 4. How it retains knowledge per client

Three stores, one directory per client, all read at the start of every run.

- **The profile** is what the client is. It changes rarely.
- **The knowledge store** holds facts, precedents and open questions. A
  precedent is a decision with its evidence, so the next time the same
  question comes up the answer is already there and the finding is routed as
  known rather than novel.
- **The outcome log** holds every accepted, dismissed, corrected or deferred
  decision on a finding. The learning policy built from it suppresses a
  pattern after three dismissals with no acceptance, and marks a pattern known
  after any acceptance or correction. Known lowers the novelty score, which
  lowers the review tier, which means fewer model calls and fewer approvals.
  Critical findings are never suppressed, and suppressed findings are counted
  on the run summary, so nothing disappears silently.

This is how it gets more competent over time on each client: not by retraining
a model, which would be unverifiable, but by accumulating verified decisions
that change routing and scope. The behaviour is deterministic, so you can see
exactly why a finding was or was not raised.

## 5. What it cannot do today, and why

1. **It cannot run on a real QuickBooks file end to end.** The connector reads
   accounts and journal entries and ties out against QuickBooks' own trial
   balance. Invoices, bills, payments, deposits, expenses, transfers, credit
   memos, vendor credits, purchases and payroll entries still need mappers.
   This is a few hundred lines of careful mapping and the single largest
   blocker to a paying client. I did not guess the mappings without a sandbox
   file to verify against, because a wrong sign on one entity type would
   silently break every downstream number.
2. **It cannot post, send, pay or file anything.** Autonomy level A2 is the
   first level that executes, and the constitution requires supervised
   production evidence before any client reaches it. Every action is drafted
   with its lines and its gate reason; a human executes. This is a sequencing
   decision, not a technical limit.
3. **The 2026 tax constants are not verified against the CRA tables.** CPP,
   CPP2, EI, basic personal amounts and Ontario brackets are set from the
   values I had and flagged `verified_against` empty. Check them against the
   T4127 tables before the first real payroll.
4. **Live model calls are untested.** The Anthropic and OpenAI-compatible
   adapters are written to the vendors' documented request shapes, and every
   test runs against the deterministic offline provider. The first call with
   a real key may surface a field name that differs.
5. **It does not know what "OpenClaw" is.** If it is an OpenAI-compatible
   endpoint it is a catalogue entry away.
6. **No persistence layer, API or interface.** Files on disk and the CLI cover
   every capability. A second person using it, or a client seeing it, needs an
   API and an interface. Building those before the numbers are proven on a
   real file would be building on sand.
7. **WSIB premiums, T4 slip rendering and driver-based budgets** are computed
   as far as the data allows (dates, obligations, per-employee totals, the
   variance control) but not rendered as filings.

## 6. Why it is built this way

**Code calculates, models reason.** No model ever adds up money. Every control
is deterministic code with a version number, and every finding carries the
calculation and the source records. This is why ForgeBench can measure it, why
a client can audit it, and why a cheaper model can be used: the model is asked
to judge evidence, not to produce numbers.

**Evidence-first.** A model only sees a bounded, checksummed evidence packet
with untrusted text fenced as data. This closes prompt injection through
vendor names and memo fields, keeps token cost bounded, and makes every
decision reproducible.

**Risk tiers decide cost.** Routine items get one cheap or free model or none
at all. Material items get a preparer, a controller and an adversary from
different vendors. Money leaving the business gets a human. This is how it
uses every advanced model without paying frontier prices for bookkeeping.

**One profile drives everything.** Personalisation that is applied by hand is
applied inconsistently. Every control, benchmark, tax rate, workflow and job
reads the same policy derived from the same profile.

**Learning is a log of decisions, not a fine-tune.** Deterministic, auditable,
per client, and impossible to poison from a document.

**The CLI before the interface.** Every capability runs in seconds on any
machine with no keys. That is what let 277 tests and a release gate exist
before any screen did.

## 7. What is not right yet, in my judgement

- **Unexplained findings on the seeded book are nine.** The gate allows
  twelve. Most are true consequences of planted errors showing up through a
  second control, which is correct behaviour, but a few are threshold
  choices that a real file will tune better than I can.
- **The corporate provision is fiscal year to date and does not annualise.**
  Comparing it to instalments needs annualisation and prior-year tax, which
  the profile does not carry yet.
- **The close checklist owner for each task is a role, not a person.** When a
  second person works the queue, tasks need assignment.
- **Pricing constants are my estimates** of a reasonable Ontario market
  position. Replace them with yours in `forge/growth/pricing.py`.
- **The filing calendar assumes assigned frequencies from revenue.** The CRA
  assigns them per account; every such date is flagged `confirm`.
- **The industry packs are for 22 industries.** A client outside them gets
  generic benchmarks and the benchmark controls stay quiet.
- **Slip obligations use the subcontractor account subtype.** A file that
  codes subcontractors to cost of sales without the subtype will miss them
  until the chart mapping sets it.

None of these change a number a client would see wrongly; they narrow what the
system will say rather than make it say something false.

## 8. What I need from you, in priority order

1. A QuickBooks sandbox app and one real, anonymised client file with defects
   you already know about. This unblocks the mappers and turns ForgeBench
   from synthetic to real.
2. Two vendor API keys, and Ollama if you want free local extraction.
3. Confirmation of the 2026 payroll constants against the CRA tables.
4. Your pricing, your firm name and sender name for proposals and outreach.
5. A decision on hosting in a Canadian region.
6. Legal review of the scope language in the report and proposal.
