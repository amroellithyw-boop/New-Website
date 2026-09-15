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
forge controls    # list all 50 controls
forge review --seeded --out review.html
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
| Control catalogue | 50 versioned controls, each with purpose, severity, evidence contract and remediation |
| Evidence packets | Bounded, checksummed, with untrusted source text fenced as data |
| Risk router | Deterministic tiering R0 to R4 that records its reasons |
| Review hierarchy | Preparer, manager, controller, adversary, policy, VP, CFO, human |
| Model gateway | Provider-agnostic, routes by risk, enforces reviewer independence, tracks cost |
| Work items | 16-state machine; cannot approve over an open note or act beyond approved scope |
| ForgeBench | 17 injectors, 15 defects, 2 adversarial decoys, release gate in CI |
| Client report | Standalone HTML review plus a machine-readable evidence bundle |

Current numbers on the seeded case:

| Metric | Value |
| --- | --- |
| Planted defects detected | 15 of 15 |
| Critical defects detected | 6 of 6 |
| Decoys wrongly reported | 0 of 2 |
| Findings on a clean book | 8 |
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
  forge/rules/catalog/        the 50 controls, grouped by finance area
  forge/agents/               contracts, model gateway, review loop
  forge/workitems/            risk scoring, routing, state machine
  forge/bench/                seeded defects and the release gate
  forge/report/               the client-facing review
docs/                         constitution, architecture, strategy, decisions
```

## Documentation

| Document | What it answers |
| --- | --- |
| `docs/00-product-constitution.md` | The rules that cannot be traded away for a demo |
| `docs/01-architecture.md` | The layers, the decisions taken, and what each one costs |
| `docs/02-commercial-strategy.md` | How this earns money, in what order, at what price |
| `docs/03-build-stack.md` | Which tool and which model for which job |
| `docs/04-getting-existing-code-in.md` | Moving the Replit project into this repository |
| `docs/05-decision-log.md` | Every shortcut, assumption and deferred feature |
