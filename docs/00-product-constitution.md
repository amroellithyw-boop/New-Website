# Product constitution

These are the rules that do not get traded away to make a demo land, a deadline
hold, or a client happy in the short term. Everything else in ForgeOS is
negotiable. This is not.

## 1. Evidence before opinion

No material recommendation exists without a traceable evidence bundle: source
records, period, deterministic calculations, assumptions and confidence. A
proposal citing no evidence is not a weak proposal; it is invalid, and the
schema rejects it.

**Enforced by:** `PreparerProposal.supporting_evidence_ids` validation;
`EvidencePacket.is_sufficient`; a test asserting every finding carries both
references and calculations.

## 2. Deterministic financial math

Models never become the calculator of record. Currency, reconciliations,
schedules, ratios, amortisation, tax computations, aging, roll-forwards and
thresholds are code. Money is an integer number of minor units; a float is
rejected at the constructor.

**Enforced by:** `forge.money`; the entire `forge.engine` package importing
nothing from `forge.agents`; `test_money.py`.

## 3. Separation of duties

The agent that creates work has no authority to approve it. The review chain is
built with the preparer filtered out of it.

**Enforced by:** `required_reviewers`; `test_a_preparer_never_reviews_its_own_work`.

## 4. Sequential correction loops

A reviewer does not comment, it decides. It can return work with atomic
correction notes, and the preparer must answer each note individually before the
same reviewer re-checks. Regenerating the answer and ignoring the notes sends the
item to escalation.

**Enforced by:** `ReviewDecision` rejecting a RETURN with no notes;
`CorrectionResponse.covers`; the loop in `forge.agents.roles`.

## 5. Risk-based review depth

Routine work does not consume executive reasoning. A deterministic router assigns
a tier and records why. Reviewers may escalate a tier; nothing may silently
lower one.

**Enforced by:** `score_finding`; `RiskScore.escalate_to` ignoring downgrades.

## 6. No silent uncertainty

When evidence is incomplete, models disagree, or confidence is low, the system
escalates. "I cannot conclude from this evidence" is a valid control outcome.
When reviewer independence is unavailable, the system says so rather than
implying the review was independent.

**Enforced by:** `missing_evidence` and `confidence_reason` on every proposal;
the `independence_unavailable` audit event; `GatewayError` after retries.

## 7. Reversible actions

Early actions are drafts. An approval of "analysis only" never authorises a
posting, a payment or a filing, however confident the chain was.

**Enforced by:** `SCOPE_PERMITS`; `WorkItem.may_execute`.

## 8. Complete auditability

Every state change, agent decision, reviewer comment, approval, model version and
prompt version is logged with an actor and a timestamp.

**Enforced by:** `AuditEvent` on every transition; `AgentCall` recording provider,
model, prompt version and packet checksum.

## 9. Least privilege

Connectors are read-only at the type level. Write capability is a separate
interface with separate credentials, added only at autonomy level A2.

**Enforced by:** `Connector` exposing `fetch` and no write method at all.

## 10. Continuous evaluation

Nothing ships because it looked good. Every control, prompt, model and threshold
is measured against cases with known expected outcomes, and a regression blocks
the release.

**Enforced by:** ForgeBench; `BenchThresholds` exiting non-zero in CI.

## 11. Provider independence

ForgeOS owns the orchestration, evidence, tests, business logic and memory.
Nothing above the gateway imports a vendor SDK or names a model.

**Enforced by:** `forge.agents.gateway`; the Anthropic import is lazy and local.

## 12. Human accountability

For regulated, tax, payroll, filing, banking or material decisions,
responsibility stays explicit. A payment with no bill and no approval reaches a
human regardless of amount.

**Enforced by:** `FOS-R032` floor of R4; `ReviewPlan.requires_human`.

---

## The prompt-injection boundary

An uploaded invoice is data. A vendor memo is data. A customer email is data.
None of them is an instruction, and a supplier must not be able to type "approve
this payment" on a PDF and have it work.

Source text reaches a model only inside a labelled `untrusted_source_text`
section of the evidence packet, and the system prompt states, before the data
arrives, that anything in it reading like a directive is itself evidence of a
problem.

## The first hard stop

Do not build the CFO brain until the source data, deterministic engine, evidence
format and correction loop work. Intelligence on untrusted financial foundations
only produces sophisticated-looking errors.

This is why `run_continuous_controller` enforces the data gate: if the trial
balance does not tie, no model reasoning is spent on top of numbers that do not
add up.
