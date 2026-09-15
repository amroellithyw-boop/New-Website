# Which tool, which model, which job

A practical map of what to use where, and what not to use at all.

## Models

Do not hard-code a model to a role. The gateway routes by risk tier so the
expensive reasoning is spent where it changes the outcome.

| Job | Model class | Why |
| --- | --- | --- |
| Document extraction, classification, memo parsing | Small and fast (Haiku class) | High volume, low judgement, cheap per call |
| R1 and R2 first-pass preparation | Small to mid | Routine, batched, deterministically validated anyway |
| R2 manager review, variance narratives | Mid (Sonnet class) | Needs reasoning, not depth |
| R3 controller and specialist review | Mid to large | Material judgement, cross-domain |
| R3 and R4 adversarial review | Large (Opus class) | The job is to break a conclusion, which is the hardest reasoning in the system |
| R4 synthesis, CFO-level trade-offs | Large | Rare, material, worth the cost |

**Reviewer independence.** For R3 and above, the reviewer should run on a
different model family from the preparer. Two instances of one model fail the
same way, and correlated failure is indistinguishable from agreement. The
gateway prefers an independent family and records an `independence_unavailable`
audit event when none is configured, rather than implying an independence you do
not have.

**Cost control.** Precompute everything. Never send a ledger to a model to total
a column that SQL can total exactly. Send evidence packets, not data dumps. Cache
stable context such as chart semantics, vendor profiles and prior approved
treatments. Batch routine reviews and drill into outliers only.

## Platform

| Need | Choice | Why |
| --- | --- | --- |
| Finance and AI backend | Python with FastAPI | Best ecosystem for financial logic, numerical work, testing |
| Web application | Next.js with TypeScript | Mature dashboard, auth and routing patterns |
| Database | Managed PostgreSQL | Transactional integrity, row-level security, JSON where needed |
| Background work | A managed job runner, Redis-compatible queue if needed | Syncs, document processing and bench runs must not block requests |
| Object storage | Encrypted S3-compatible | Raw payloads, documents, evidence artefacts |
| Auth | Managed provider with MFA and organisations | Do not build authentication primitives |
| Secrets | Managed vault | Never in source, never in a prompt |
| Observability | Structured logs, traces, error reporting, cost telemetry | Work-item-level visibility across agents and tools |
| Repository and CI | GitHub with protected main | Every change reviewable, testable, revertible |

**On hosting.** Canadian data residency matters for this client base. Any major
cloud with a Canadian region works. Pick one and stop thinking about it; this is
not where the product is won.

**On Temporal.** The original blueprint specifies it. It is genuinely good for
long-running workflows, and it is also a significant operational commitment. A
managed job runner plus the work-item state machine already in this repository
covers the first year. Revisit when you have multi-day workflows with real
compensation logic.

## AI tooling for building it

Treat AI as a software organisation with enforced handoffs, not as one chat.

| Build role | What it does | What it must not do |
| --- | --- | --- |
| Architect | Turns intent into decisions, tickets, acceptance criteria | Write large unreviewed production changes |
| Implementer | One scoped ticket at a time, with tests | Touch neighbouring modules without scope |
| Code reviewer | Reviews the diff for correctness, security, edge cases | Approve its own implementation |
| Accounting reviewer | Validates debits, credits, periods, materiality, semantics | Assume correctness because tests pass |
| Security reviewer | Threat-models connectors, tenancy, secrets, injection | Trade a control for demo speed silently |
| QA | Adversarial, boundary and failure-mode cases | Test only happy paths |

**The rule that matters.** Never point three coding agents at the same module at
once unless you deliberately want competing prototypes. Scope each ticket to
named files and state what is out of scope.

## What not to build

| Do not build | Why |
| --- | --- |
| A custom foundation model | You will never out-train a frontier lab, and you do not need to |
| Twenty agent personas with shallow prompts | Depth beats role-play; the schema does the work, not the adjectives |
| Your own auth, secrets or queue | Solved problems with worse failure modes when home-made |
| A general ledger | Not until integrations stop being enough |
| Every connector | One, perfected, until the canonical model has survived contact |
| A large dashboard | Prioritised decisions beat charts; a queue someone clears beats a screen someone looks at |

## The connector that comes next

The only piece between this repository and a sellable artefact is a read-only
QuickBooks Online connector. It has to:

- use OAuth with a read-only scope, and store tokens encrypted;
- write every raw payload to the vault before normalising anything;
- sync incrementally and idempotently, so a resync never double-counts;
- map into the canonical model with lineage and a mapping version on every row;
- reproduce the trial balance, profit and loss and balance sheet exactly.

That last point is the gate. `Connector` in `forge/connectors/base.py` is the
interface, and `fixture_contractor.py` is the reference implementation showing
exactly what a connector is expected to produce.
