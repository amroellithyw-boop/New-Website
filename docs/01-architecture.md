# Architecture

## The layer stack

The dependency arrow only ever points inward. Nothing in `engine` or below knows
that a language model exists, which is what makes the accounting testable without
a key, a network or a mock.

```
 connectors    read-only adapters producing raw payloads
     |
 vault         immutable payload storage with checksums
     |
 normalize     raw -> canonical, with lineage and a mapping version
     |
 canonical     the financial model every layer agrees on
     |
 engine        deterministic accounting math (the calculator of record)
     |
 rules         versioned controls turning math into findings
     |
 evidence      bounded, checksummed packets
     |
 workitems     risk scoring, routing, state machine
     |
 agents        preparers and reviewers, under enforced contract
     |
 report        the client-facing review
```

`bench` sits beside all of it and measures the whole thing.

## Decisions taken, and what each one costs

### ADR-001: Money is an integer count of minor units

**Decision.** Every amount is `(minor_units: int, currency: str)`. `Money` refuses
to be constructed from a float. Multiplication takes an explicit rounding mode.
Division for allocation uses the largest-remainder method so parts always sum
back to the whole.

**Why.** A float in a ledger is a defect waiting for a period end. The
one-cent close difference that takes an afternoon to find is almost always an
allocation that did not round-trip.

**Cost.** Slightly more verbose call sites, and every new currency needs an entry
in `MINOR_UNITS` with a test.

### ADR-002: Canonical records are dataclasses, not ORM rows

**Decision.** The financial model is plain frozen dataclasses. Persistence is a
separate concern layered on top.

**Why.** The accounting math is then testable with no database, the full
control suite runs in about a tenth of a second, and swapping the persistence
layer never touches an accounting rule. It also forces a connector's job to end
at a clean boundary: produce canonical records with lineage, and stop.

**Cost.** A mapping layer has to be written for whatever database is chosen, and
very large ledgers will need streaming rather than in-memory assembly. That
trade is worth taking while the design centre is a business with under a hundred
thousand transactions a year.

### ADR-003: Debit-positive signed amounts everywhere

**Decision.** A transaction line carries one signed amount. Positive is a debit,
negative is a credit. `side`, `debit` and `credit` are derived.

**Why.** Two parallel columns invite one to be populated and the other forgotten.
One convention, enforced at the type, removes a whole class of sign bug.

**Cost.** Presentation has to flip the sign for credit-normal accounts, which is
done in exactly one place. That flip keys off the account **type**, not the
contra-adjusted normal balance, so accumulated depreciation stays negative inside
the asset section and the accounting equation still holds. Getting this wrong was
a real bug during the build: each section read tidily and the balance sheet was
out by the whole contra balance.

### ADR-004: Controls are versioned objects, not functions

**Decision.** Each control is a class declaring `rule_id`, `version`, purpose,
severity, risk floor, required evidence, remediation and whether it involves
cash already disbursed. `evaluate` is pure.

**Why.** The metadata is what lets ForgeBench measure a control, lets the router
tier it, lets the report explain it to a client, and lets a reviewer see the
contract without reading the implementation.

**Cost.** More ceremony per control. It pays for itself the first time a
threshold changes and the bench tells you which detections moved.

### ADR-005: Materiality is anchored to trailing twelve-month revenue

**Decision.** Half a percent of trailing twelve-month revenue, with total assets
as a fallback and a floor for very small businesses. Profit is deliberately not a
candidate benchmark.

**Why.** A contractor whose profit swings between one and twelve percent of
revenue on one job would get a materiality that moves by an order of magnitude
month to month. The queue then becomes unstable across periods, which destroys
the reader's trust faster than being slightly too sensitive. Year-to-date is also
rejected: a seasonal business closing January would be measured against one month
of snow revenue.

**Cost.** A genuinely loss-making business gets a materiality that feels high
relative to its profit. Severity floors on the controls cover the cases that
matter regardless of size.

### ADR-006: Risk is scored on consequence, not on size

**Decision.** The dominant inputs are whether money has already left the business,
whether a statutory deadline has passed, and whether the action is reversible.
Size contributes, capped.

**Why.** An overdue invoice and an unauthorised payment can be the same number.
One leads to a phone call, the other to a recovery action and possibly a fraud
conversation. A router that scores on size sends both to the same place, and
after a fortnight of that nobody opens the queue.

**Cost.** Each control has to declare `involves_disbursed_cash` honestly. That is
a one-line judgement per control and it is reviewable in a diff.

### ADR-007: Agent output is a schema, not prose

**Decision.** Preparers return `PreparerProposal`; reviewers return
`ReviewDecision`. A RETURN with no correction notes, an ESCALATE with no reason,
an APPROVE with no scope, and a proposal with no evidence are all rejected at
construction. Invalid responses are retried with the validation error fed back,
then fail loudly.

**Why.** "Review" cannot mean a second model agreeing pleasantly. A mandatory
`strongest_alternative` field and a mandatory `residual_risk` field produce
something a human can argue with; an instruction to "act like a world-class
controller" produces confident paragraphs.

**Cost.** Schema drift has to be versioned along with prompts.

### ADR-008: An offline deterministic provider ships in the core

**Decision.** The gateway includes a provider that needs no key and no network
and returns schema-valid output derived from the evidence packet.

**Why.** ForgeBench has to run on every commit. A quality gate that depends on a
paid external service is a quality gate that gets disabled the first time it is
flaky. The orchestration, routing, correction loops and gate enforcement are
therefore tested continuously even when model calls are not.

**Cost.** It tests the plumbing, not the judgement. Model-quality evaluation is a
separate, paid suite that runs on a schedule rather than on every push.

### ADR-009: Reviewer independence is by model family

**Decision.** For R3 and above the reviewer prefers a different model family than
the preparer. When no independent family is configured, the system records
`independence_unavailable` on the work item.

**Why.** Two instances of one model fail the same way, and a correlated failure
looks exactly like agreement. Claiming independence you do not have is worse than
not having it.

**Cost.** Running two providers costs more and adds an integration. The honest
audit event means you can defer the cost without deceiving yourself.

### ADR-010: The data gate blocks model reasoning

**Decision.** If the trial balance does not net to zero, debits do not equal
credits, or assets do not equal liabilities plus equity, the run still produces
deterministic findings but spends no model tokens.

**Why.** Reasoning on top of numbers that do not add up produces confident,
expensive, wrong answers.

**Cost.** A client with genuinely broken books gets a shorter first report. That
report tells them the one thing that matters most.

## What is deliberately deferred

| Deferred | Until |
| --- | --- |
| Write-back to any ledger | Thousands of supervised draft actions with a very low error rate |
| Autonomous filing, payroll release, payments | Not a near-term goal at any volume of evidence |
| A native general ledger | ForgeOS owns enough workflow and semantics that a ledger adds value beyond rebuilding QuickBooks |
| A second accounting connector | The canonical model has survived one connector in production |
| Multi-entity consolidation | A single-entity close is boringly reliable |
| A custom model | Never. Frontier models through a gateway |

## Where the real complexity will land

Three areas will absorb more effort than they appear to:

**Normalisation.** Every accounting system represents the same economic event
slightly differently, and the canonical model is what stops that variation
leaking into fifty controls. Budget for the mapping layer to be rewritten once.

**False positives.** Detection is the easy half. The clean-book noise floor is
the number that decides whether anyone keeps reading the queue, which is why it
is a test with a hard ceiling rather than a metric on a dashboard.

**Evidence sufficiency.** An analytical finding with no source records is not
actionable. This was a real defect during the build: variance controls initially
reported that repairs rose 2,945% with nothing for the client to look at.
