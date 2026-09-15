# ForgeOS

An evidence-first finance operating system for owner-managed businesses, starting
with contractors and trades.

ForgeOS is not an accounting chatbot. It is a control system. Deterministic code
calculates; language models interpret verified evidence; every material
conclusion moves through a review hierarchy that can return work for correction;
and nothing acts on the business without an approval whose scope actually covers
the action.

## The one idea

> AI reasons. Code calculates. Policy authorises. Humans stay accountable.

A model is never the calculator of record. Currency math, reconciliations,
schedules, ratios, aging, roll-forwards and thresholds are code, reproducible to
the cent. What models do is read a bounded evidence packet and form a judgement
that another role then tries to break.

## Try it in sixty seconds

```bash
cd services/forge-core
pip install -e ".[dev]"

forge tieout      # prove the books reconcile
forge bench       # run the quality gate
forge providers   # which model vendors are configured, and the routing
forge forecast    # working capital, health score, 13-week cash
forge review --seeded --out review.html
forge run --seeded        # the whole finance team for one client, one period
forge close --seeded      # the month-end checklist, each task proven
forge tax calendar        # every filing deadline the profile implies
forge brief               # the five things the owner needs to hear
forge qbo connect --tenant sandbox   # authorise a QuickBooks company, read-only
```

No database, no API key and no network are required. A seeded synthetic
contractor is built in memory, defects are planted in it, and the whole pass runs
in about a tenth of a second.

## What is here today

| Layer | State |
| --- | --- |
| Exact money arithmetic | Integer minor units; floats rejected at the constructor |
| Canonical financial model | Accounts, transactions, parties, jobs, debt, open items, lineage on every record |
| Deterministic engine | Trial balance, statements, aging, bank reconciliation, roll-forwards, anomaly features |
| Control catalogue | 56 versioned controls, each with purpose, severity, evidence contract and remediation |
| Evidence packets | Bounded, checksummed, with untrusted source text fenced as data |
| Risk router | Deterministic tiering R0 to R4 that records its reasons |
| Review hierarchy | Preparer, manager, controller, adversary, policy, VP, CFO, human |
| Model gateway | 12 providers through 2 adapters, cost-aware routing, cascade, cross-vendor independence, fallback, budgets |
| Client profiles | One profile drives policy for every control, benchmark, tax rate and workflow |
| Document pipeline | Vision extraction, deterministic vendor table, model research only for unknowns, code-computed tax, balance-validated entries |
| CFO layer | CCA, deferred revenue and loan schedules, working capital, health score, 13-week cash forecast, job profitability, owner brief |
| Tax | HST return working paper reconciled to the ledger, filing calendar, corporate provision, T5018 and T4A obligations |
| Close | Sixteen-task month-end checklist, every task proven done, open or blocked from the engine |
| Learning | Per-client outcome log; dismissals suppress, acceptances become known patterns; critical never suppressed |
| Operations | One-command client run, job plan per client, firm queue and daily brief across every client |
| Growth | Pricing from the profile, proposals from the diagnostic, outreach drafts, pipeline |
| Work items | 16-state machine; cannot approve over an open note or act beyond approved scope |
| Industry packs | 22 Canadian industries, 33 cost structures, 5 revenue tiers |
| Canadian payroll | 2026 CPP, CPP2, EI, federal and Ontario tax, exact to the cent |
| QuickBooks connector | Read-only, normalising, with a tie-out against QBO's own trial balance |
| ForgeBench | 17 injectors, 15 defects, 2 adversarial decoys, release gate in CI |
| Client report | Standalone HTML review plus a machine-readable evidence bundle |

Current numbers on the seeded case:

| Metric | Value |
| --- | --- |
| Planted defects detected | 15 of 15 |
| Critical defects detected | 6 of 6 |
| Decoys wrongly reported | 0 of 2 |
| Findings on a clean book | 9 |
| Full control pass | ~110 ms |

## What is deliberately not here

No write-back to any ledger. No autonomous filing, payroll release or payment.
No native general ledger. No model is asked to add up money. These are sequencing
decisions, not omissions; see `docs/01-architecture.md` for why each one waits.

## Repository layout

```
services/forge-core/          the engine, controls, agents and bench
  forge/canonical/            the financial model every layer agrees on
  forge/engine/               deterministic accounting math
  forge/rules/catalog/        the 56 controls, grouped by finance area
  forge/tax/ forge/close/     sales tax, provision, calendar, slips; the close checklist
  forge/learning/             outcomes that become suppressions and known patterns
  forge/operations/           client runs, job plans, the firm queue, the daily brief
  forge/growth/               pricing, proposals, outreach, pipeline
  forge/agents/               contracts, model gateway, review loop
  forge/workitems/            risk scoring, routing, state machine
  forge/bench/                seeded defects and the release gate
  forge/report/               the client-facing review
docs/                         constitution, architecture, strategy, decisions
```

## Documentation

| Document | What it answers |
| --- | --- |
| `docs/10-operating-manual.md` | How to run it day to day, what it cannot do yet and why, what is not right |
| `docs/11-handover-checklist.md` | The six things only the owner can supply, step by step, and what is already done for each |
| `docs/12-scope-language-for-legal-review.md` | Every sentence a client reads, ready for a lawyer |
| `docs/00-product-constitution.md` | The rules that cannot be traded away for a demo |
| `docs/01-architecture.md` | The layers, the decisions taken, and what each one costs |
| `docs/02-commercial-strategy.md` | How this earns money, in what order, at what price |
| `docs/03-build-stack.md` | Which tool and which model for which job |
| `docs/04-getting-existing-code-in.md` | Moving the Replit project into this repository |
| `docs/05-decision-log.md` | Every shortcut, assumption and deferred feature |
| `docs/06-legacy-migration.md` | What moved over from ProfitForge, and the nine defects found in it |
| `docs/07-ai-providers.md` | Every model vendor, how routing picks one, and why cheap is also rigorous |
| `docs/08-feature-inventory.md` | All 53 legacy features and where each one stands |
| `docs/09-walkthrough.md` | How to run it, set up providers, and what only you can supply |
