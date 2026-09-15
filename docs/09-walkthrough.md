# Walkthrough: what you now have and how to run it

Written for you, the operator. It assumes nothing about the code.

## What was built this round

You asked for four things. Here is where each landed.

**Every advanced model, as cheaply as possible, without losing capability.**
One gateway now routes to Claude through the official SDK and to every other
vendor through a single OpenAI-compatible adapter: OpenAI, xAI's Grok, Gemini,
Groq, OpenRouter, Together, Fireworks, Mistral, DeepSeek, and local Ollama or
vLLM running open-weight models like Hermes, Llama and Qwen for free. Routing
picks the cheapest model that clears each risk tier's quality bar, prefers a free
local model when it qualifies, escalates cheap first passes only when they
report low confidence, falls over to another vendor on an outage, and stops
loudly at a budget. Material reviews are spread across vendors so a preparer, a
controller and an adversary never share a failure mode. See `docs/07-ai-providers.md`.

**Every useful legacy feature kept.** Fifty-three features inventoried and
mapped in `docs/08-feature-inventory.md`. The ones with real judgement in them
are ported and tested: the intake, the industry packs, payroll, the document
pipeline with its refund and cardholder logic, the vendor table and cache, the
CCA, deferred revenue and loan schedules, the working capital scorecard, the
health score, the cash forecast, the client-questions email and the onboarding
plan. What was dropped is scaffolding, with the reason stated.

**Hyper-personalisation preserved and made consistent.** One `ClientProfile`
holds every intake field. Its `to_policy()` produces the settings every control,
benchmark, tax calculation and workflow reads. Change the province and every
tax control re-anchors. Change the revenue and the benchmark tier shifts. Add a
service and its controls come into scope. Nothing is personalised by hand, so
nothing is personalised inconsistently.

**Accuracy first.** Everything a model proposes is validated by code before it
is shown to anyone: entries must balance, tax is computed from the client's
province rather than by the model, and every finding carries the calculation
and source records behind it.

**Every role, every workflow, tax included (this round).** The audit against
your two blueprints found four roles without a mandate and four workflows with
no code: month-end close, tax readiness, FP&A and the owner brief. All are
built now. Tax has its own package: the HST return working paper reconciled to
the ledger, the filing calendar for every obligation the profile implies,
the corporate provision with the add-backs, and T5018 and T4A slip obligations.
The close is a sixteen-task checklist where every task proves its own status
from the engine. Job profitability and a margin-outlier control cover FP&A.

**Knowledge that compounds per client.** Every decision you record on a finding
goes into that client's outcome log. Three dismissals of one pattern on one
counterparty suppress it; an acceptance makes it a known pattern, which lowers
its novelty score next time so it routes to a lighter review. Critical findings
are never suppressed. The log, the knowledge store and the precedents all live
in one directory per client and are read at the start of every run.

**The selling machine and the operations machine.** `forge run-all` runs every
client, builds the firm's prioritised queue and writes the daily brief. `forge
sales` keeps the pipeline, drafts outreach and follow-ups, and `forge brief
--proposal` turns a diagnostic into a priced proposal. Pricing is a function of
the profile, so a proposal is never made up.

## The numbers

| | Before this round | Now |
| --- | --- | --- |
| Controls | 55 | 56 |
| Tests | 170 | 277 |
| Model providers reachable | 1 | 12 |
| Independent model families | 1 | up to 12 |
| ForgeBench detection | 15 of 15 | 15 of 15 |
| Bank statements reconciling | 36 of 36 | 36 of 36 |

## Running it

Everything below runs with no keys and no network, using the offline stub for
model calls. With keys set, the same commands use real models.

```bash
cd services/forge-core
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

**See what is configured.**

```bash
forge providers
```

Shows every provider, whether its key is set, how many independent families you
have, and exactly which model each tier and role will use.

**Onboard a client from an intake.**

```bash
forge onboard intake.json --out clients/eternal.json
forge onboard intake.json --out clients/eternal.json --plan   # drafts the onboarding plan
```

The intake can be in the legacy questionnaire shape or the profile shape. The
output profile is what every other command takes.

**Run the control review.**

```bash
forge review --seeded --out review.html --evidence evidence.json
```

**Working capital, health score and thirteen-week cash.**

```bash
forge forecast --profile clients/eternal.json
```

**Process a document.**

```bash
forge document statement.pdf --profile clients/eternal.json --out result.json
```

Extracts, identifies vendors, proposes balanced tax-split entries, and shows what
was rejected and why. Nothing is posted anywhere.

**Run everything for one client.**

```bash
forge run --profile clients/eternal.json --data-dir data
forge run --seeded            # the same, on a book with fifteen planted defects
forge run --json              # every number, machine-readable
```

One command: controls, health score, working capital, cash forecast, close
checklist, sales tax return, corporate provision, filing deadlines, and a
drafted action for every finding. The data directory is where this client's
knowledge and decisions accumulate.

**Tell it what happened, so it learns.**

```bash
forge outcome FOS-R027:CUST-0007 accepted  --data-dir data --by amro
forge outcome FOS-R015:bookkeeper dismissed --data-dir data --reason "she is our bookkeeper"
```

**Close the month, prepare tax, brief the owner.**

```bash
forge close --profile clients/eternal.json
forge tax return    --profile clients/eternal.json --period-end 2026-06-30 --months 3
forge tax calendar  --profile clients/eternal.json
forge tax provision --profile clients/eternal.json
forge tax slips     --profile clients/eternal.json
forge brief         --profile clients/eternal.json
```

**Run the whole firm.**

```bash
forge run-all clients/ --pipeline data/pipeline.json
```

Every profile in the directory is run; you get the daily brief and the queue
ordered by what matters: approvals and broken books first, then cash and
deadlines inside a week, then material findings, then sales follow-ups.

**Sell.**

```bash
forge sales add  --prospect-id P1 --name "Acme Paving" --naics 238210
forge sales outreach --prospect-id P1          # drafts the first email with your models
forge sales move --prospect-id P1 --stage diagnostic_booked
forge brief --proposal --profile clients/acme.json
forge sales due                                # who to follow up today
```

**Prove the books and the quality gate.**

```bash
forge tieout
forge bench
```

## Setting up providers

Set the keys you have. Start with two families so reviews are independent.

```bash
export ANTHROPIC_API_KEY=...     # Claude, official SDK
export XAI_API_KEY=...           # Grok, a cheap independent reviewer
export OLLAMA_HOST=http://localhost:11434   # free local extraction, optional
forge providers                  # confirm
```

Then any command that reaches a model uses them. To bound spend on a run, the
gateway takes a `Budget`; the CLI runs unbounded because it defaults to the
offline stub.

## How a document becomes an entry

1. A vision model reads the file and returns structured data. Statement
   sections and cardholders are tracked; negative card amounts are forced to
   refunds by code even if the model missed it.
2. Every vendor is checked against a deterministic Canadian vendor table, then
   the client's own precedents. Only unknown vendors go to a model with web
   search, and the answer is cached for that client.
3. A model proposes accounts and a tax *treatment*. Code computes the tax split
   from the client's province, adds the tax line, and refuses any entry that
   does not balance.
4. The result is proposed entries with evidence, ready to become work items in
   the same review hierarchy as every finding. Nothing posts.

## How personalisation flows

```
intake questionnaire
   -> ClientProfile          every field, JSON round-trip
   -> to_policy()            one dictionary
   -> RuleContext.policy     read by all 55 controls
   -> industry pack          benchmarks by NAICS and revenue tier
   -> sales_tax_for()        rate by province
   -> workflows_in_scope     from the services engaged
   -> knowledge store        precedents lower review depth on known patterns
```

## What I need from you

These are things only you can supply. Nothing here blocks running the
demonstration; all of it blocks running on a real client.

1. **API keys for at least two model vendors.** Anthropic plus one of xAI,
   OpenAI or Gemini. That is what makes reviews independent. If you want free
   local extraction, install Ollama and pull a model.
2. **A QuickBooks sandbox app.** Client ID, secret and redirect URI from the
   Intuit developer portal. Without it the connector can be tested only against
   recorded payloads.
3. **One real client file with problems you already know about.** Anonymised is
   fine. Ten known defects in a real ledger are worth more to ForgeBench than a
   month of synthetic cases, and it is the only way to tune the false-positive
   rate against reality.
4. **Your own vendor and account decisions.** The vendor table encodes common
   Canadian patterns; your corrections over a few weeks are what make it yours.
5. **A decision on hosting.** Any cloud with a Canadian region. I will not pick
   this for you; it affects your data residency obligations.
6. **Legal review of the report's scope language** before the first external
   client sees one.
7. **What OpenClaw is.** I could not identify it. If it exposes an
   OpenAI-compatible endpoint it is a one-line catalogue entry.

## What is still not built

- Ten QuickBooks entity mappers beyond journal entries, which block a real
  file tying out.
- The year-end working-paper binder and slip rendering; the obligations,
  dates and provision are computed.
- WSIB premium calculation; the dates and the rate-group control exist.
- Driver-based budgets; the cash forecast and variance control exist.
- A persistence layer and an API over the pipeline; the CLI covers everything
  today.
- Any write-back, sending or filing. Those are autonomy level A2 and wait on
  supervised production evidence, as the constitution requires.

## Where to look

| Question | File |
| --- | --- |
| Which model does what and why | `docs/07-ai-providers.md` |
| Where every legacy feature went | `docs/08-feature-inventory.md` |
| The rules that do not bend | `docs/00-product-constitution.md` |
| How to make money with it | `docs/02-commercial-strategy.md` |
| What was wrong in the legacy code | `docs/06-legacy-migration.md` |
