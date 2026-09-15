# Decision log

Every shortcut, assumption and deferred feature, written down. The point of this
file is that a future reader can tell the difference between a deliberate choice
and an oversight.

## Assumptions currently baked in

| Assumption | Where | Risk if wrong |
| --- | --- | --- |
| Ontario HST at 13% | `DEFAULT_POLICY["sales_tax_rate"]` | Tax controls mis-measure in other provinces. Read from entity jurisdiction before the second province. |
| Straight-line depreciation at 15% | `DEFAULT_POLICY["depreciation_rate"]` | The depreciation reasonableness control is a heuristic, not a schedule. Replace with a real asset register before relying on it. |
| Single currency per entity | `Ledger.currency` | Multi-currency needs FX revaluation and a translation control. Not started. |
| Single legal entity per run | `RuleContext` | Consolidation, intercompany elimination and minority interest are all absent. |
| Calendar month periods | `FiscalCalendar` generation | 4-4-5 and 52/53-week calendars are not handled. |
| Period close status comes from the source | `Period.is_closed` | Currently inferred in the fixture. A real connector must read it. |

## Known limitations

**Duplicate detection uses exact amounts.** A duplicate bill entered with a
transposed digit will not cluster. Fuzzy amount matching within a tolerance is a
known follow-up, and it will need care: loosening it is the fastest way to raise
the false-positive rate.

**Recurring-pattern detection needs four occurrences.** A quarterly obligation
takes a year to learn. Contract-driven expectations would fix this properly.

**Bank matching caps group size at four.** A deposit batch of eight receipts will
not match as a group. The bound is deliberate: the search is combinatorial, and an
unbounded version turns one pathological account into a hung job.

**The covenant control only implements leverage.** Fixed-charge coverage, current
ratio and tangible net worth covenants are not implemented, and covenant
definitions vary per agreement in ways that resist generalisation.

**Materiality does not adjust for known misstatement.** An auditor lowers
performance materiality as errors accumulate. ForgeOS does not yet.

**No job-level profitability controls.** For a contractor this is arguably the
single most valuable analysis, and it is absent. The canonical model carries
`job_id` on every line, so the data is there; the controls are not written.

**Reviewer independence is unverified in practice.** The mechanism exists and
reports honestly when unavailable, but it has never run with two live providers.

**Offline provider tests plumbing, not judgement.** ForgeBench proves the
orchestration, routing, correction loop and gate enforcement work. It says
nothing about whether a real model's review catches real defects. That needs a
separate paid suite with human-graded cases.

## Defects found and fixed during the build

Recorded because each one is a class of error worth watching for again.

1. **The income statement used closing balances instead of period movement.** Any
   period not starting at inception silently reported inception-to-date results.
   Caught by the balance sheet failing to balance, not by the profit and loss
   looking wrong, which is the usual way this presents.

2. **Contra accounts were flipped into the wrong side of the equation.**
   Accumulated depreciation was presented positive inside the asset section, so
   each section read tidily while total assets were overstated by twice the
   contra balance. The sign flip now keys off account type, never off the
   contra-adjusted normal balance.

3. **Variance controls reported revenue backwards.** Raw debit-positive sums were
   compared without the presentation flip, so a 72% revenue *increase* was
   reported to the client as a decrease.

4. **The loan split control passed on a fully mis-coded payment.** Coding the
   whole payment to principal makes the arithmetic tie while interest expense
   disappears entirely. Now checked against the stated rate as well.

5. **Analytical findings carried no evidence.** Variance, depreciation, runway and
   covenant controls produced conclusions with no source records, so a client
   could be told repairs rose 2,945% with nothing to look at. They now cite driver
   transactions, and where the finding is an absence they cite the comparative
   period instead.

6. **Materiality was anchored to a volatile benchmark.** Picking the most
   conservative of revenue, profit and assets produced $500 materiality on a
   $1.8M business and 54 findings on clean books. Now half a percent of trailing
   twelve-month revenue, which produced 8.

7. **Risk routing scored on size rather than consequence.** An overdue invoice
   and an unauthorised payment of similar value both reached the CFO. Controls now
   declare whether cash has already been disbursed.

8. **A latent `NameError` in the missing-recurrence control.** Reachable only on a
   specific branch, found by the linter rather than by a test. A reminder that
   type and lint gates catch a different class of defect than tests do.

## Open questions

**Where does the client-specific policy live?** Capitalisation thresholds, chart
semantics, approved treatments and vendor expectations are currently a dictionary
passed into the run. That is fine for one client and wrong for fifty.

**How is a false positive recorded and learned from?** A control that a client
dismisses three times should either suppress for that client or be retuned. The
feedback loop is designed for but not built.

**What is the unit of pricing?** Per entity, per transaction volume, or per
finding acted on. This affects what needs metering, and metering is easier to
build before there are clients than after.

**When does the ledger stop being enough?** Deferred deliberately. The trigger
should be a concrete customer outcome that integrations cannot deliver, not a
general sense that owning the ledger would be better.
