# Commercial strategy

## The uncomfortable part first

You asked for something revolutionary that nobody else can build. The honest
answer is that no single clever idea does that, and anyone who tells you
otherwise is selling a demo. Frontier models are a commodity your competitors buy
from the same shop you do. A prompt is copied in an afternoon.

What cannot be copied in an afternoon is the boring compounding stuff:

1. **The control library.** Fifty controls that fire correctly and stay quiet on
   clean books represent hundreds of hours of accounting judgement encoded as
   tests. A competitor starting today does not have your false-positive rate.
2. **ForgeBench.** A growing corpus of real SMB failure modes with known correct
   answers. Every client file you review adds to it. This is the asset that makes
   your tenth year better than your first.
3. **The canonical financial graph.** A clean cross-system representation of how a
   contractor's money, jobs, customers, crews and obligations connect.
4. **Outcome data.** Which recommendations were accepted, rejected, corrected, and
   what happened next. Nobody else has this for your vertical.
5. **Trust.** In finance, a system that shows its work and is reliably right beats
   a smarter system that cannot be checked.

None of those is exciting. All of them compound. The strategy below is built to
accumulate them while being paid to do it.

## The sequencing mistake to avoid

The blueprint's twelve-month plan is technically correct and commercially
backwards. It reaches revenue at month nine. You cannot fund a year of building
on hope, and more importantly you will build the wrong thing, because nothing
disciplines a product like someone refusing to pay for it.

So: ship something sellable in weeks, and let the revenue fund the depth.

## Stage one: the paid diagnostic (weeks, not months)

**What it is.** Read-only access to a prospect's QuickBooks Online. One pass of
the control catalogue. A written review naming every defect, what it costs, and
the exact step to fix it, with every number traceable to a source record.

That artefact already exists in this repository: `forge review --out review.html`.

**Why this first.** It needs no write access, no autonomy, no automated filing and
almost no legal surface. It is the lowest-risk thing you can sell and the highest
-signal thing you can learn from.

**Price.** $1,500 to $4,000 depending on size. Price it as a fixed-fee engagement,
not an hourly one. A contractor doing $2M who is shown $40,000 of duplicate
payments, unbilled work and overdue receivables does not argue about $2,500.

**Why it sells.** You are not selling software. You are selling a specific number:
"here is money you have already lost or not yet collected." Lead the conversation
with the recoverable-cash figure, never with the technology.

**What it does for you.** Every diagnostic is a ForgeBench case, a tuning signal
for the false-positive rate, and the opening of a monthly relationship. Do twenty
of these before you build anything else.

## Stage two: Continuous Controller (months two to six)

The same engine, run continuously rather than once, with a monthly review and a
prioritised queue.

**Price.** $500 to $2,500 a month depending on transaction volume and entity
count. This is not bookkeeping pricing and it should not be sold next to
bookkeeping pricing. It is a finance function, priced against the cost of not
having one.

**Positioning.** "You have a bookkeeper. You do not have a controller. This is the
controller." Owner-managed businesses under $10M almost never have one, and the
absence is exactly what the control catalogue detects.

**Conversion.** A diagnostic that found real money converts to monthly at a high
rate, because you have already proved the thing everyone else has to promise.

## Stage three: run your own firm on it (months three onward)

This is the highest-margin money in the whole plan and it is easy to overlook
because it does not look like a product launch.

Profit Forge already does this work by hand. Running every client file through
the control catalogue before a human touches it changes the economics of your own
practice: the reviewer starts from a prioritised, evidence-backed list instead of
from a blank screen. Capacity per person rises without hiring.

Two things follow. You raise margin on work you already have, and you accumulate
the outcome data that makes stage four defensible. Do this in parallel with stage
two, not after it.

## Stage four: sell it to other firms (year two)

The largest market is not contractors. It is the accounting firms that serve
them, who have the same capacity problem you do and no ability to build this.

Do not start here. A firm buying software wants it to already work on a hundred
files, and you will only know that after stages one to three. Starting here means
selling a promise to the most sceptical buyer in the market.

**Price.** Per-client-file, in the $50 to $200 a month range, or a firm licence.

## Revenue arithmetic

Illustrative, deliberately conservative, assuming you are doing the selling:

| | Month 3 | Month 6 | Month 12 | Month 24 |
| --- | --- | --- | --- | --- |
| Diagnostics per month | 4 | 6 | 8 | 10 |
| Diagnostic revenue | $8,000 | $14,000 | $20,000 | $25,000 |
| Continuous Controller clients | 2 | 10 | 25 | 60 |
| Subscription revenue | $2,000 | $11,000 | $30,000 | $78,000 |
| Firm licences | 0 | 0 | 2 | 12 |
| Licence revenue | 0 | 0 | $4,000 | $30,000 |
| **Monthly total** | **$10,000** | **$25,000** | **$54,000** | **$133,000** |

The number that matters is not the total. It is that subscription revenue
overtakes diagnostic revenue around month six, because that is the point where
the business stops being a consultancy and starts being a product.

## Pricing principles

**Sell the outcome, never the access.** "AI-powered financial analysis" is a
commodity. "We found $47,000 and here is where" is not.

**Never price against bookkeeping.** The moment you are compared to a $400 a month
bookkeeper you have lost, regardless of who is better. You are replacing the
controller and CFO layer that the business does not have.

**Fixed fee, not hourly.** Hourly pricing punishes you precisely for the
automation you are building.

**Price the first one low on purpose.** Your first three clients are buying
unproven software from a firm of one. Charge enough that they take it seriously
and little enough that saying yes is easy. Raise it on client four.

## What actually kills this

**Breadth before depth.** Twenty shallow workflows is the failure mode the
blueprint warns about, and it is the most tempting mistake because breadth demos
well. One workflow that is boringly reliable beats ten that are impressive once.

**False positives.** A queue that cries wolf gets ignored, and a queue that gets
ignored has negative value because the client now believes the problem is solved.
The clean-book noise floor is a test with a hard ceiling for exactly this reason.

**Selling autonomy too early.** The first time ForgeOS posts a wrong entry or
misses a remittance, the conversation stops being about software. Earn each
autonomy level with production evidence.

**Building for the enterprise.** The moment you add multi-entity consolidation for
one prospect, you have a different, worse company.

**Hiring to compensate for unclear specs.** Hire when AI plus your own skill hits
a real bottleneck, and not before. The first version is genuinely buildable by one
domain expert with AI tooling.

## The first ninety days

**Days 1 to 14.** Get a real QuickBooks Online connection working read-only
against your own test company. The connector is the only unfinished piece between
this repository and a sellable artefact. Reproduce the trial balance, profit and
loss and balance sheet exactly. If they do not tie, stop and fix it; nothing
downstream matters.

**Days 15 to 30.** Run the control catalogue against five of your own client files
in shadow mode. You already know what is wrong with those files. Every control
that misses something you know about is a tuning ticket. Every control that
raises something you know is fine is a false-positive ticket. This fortnight is
worth more than three months of feature work.

**Days 31 to 45.** Tune until the noise floor is acceptable to you as a
practitioner. Then sell three paid diagnostics at a deliberately low price.

**Days 46 to 75.** Deliver them. Watch which findings the client acts on and which
they shrug at. The ones they shrug at are either wrong, badly explained, or not
worth detecting; find out which.

**Days 76 to 90.** Convert at least one to a monthly Continuous Controller
engagement. Raise the diagnostic price. Start running your own files through it.

At the end of ninety days you have revenue, a tuned control library, real
ForgeBench cases from live files, and evidence for what to build next. That is a
materially stronger position than a half-built platform.

## Honest risks

**The QuickBooks dependency.** You are building on someone else's platform and
they can change terms. Mitigated by the canonical model: the ledger is an
integration surface, not the foundation.

**Professional liability.** You are giving accounting and tax-adjacent advice in a
regulated profession. The report already carries scope language, but get it
reviewed by counsel before the first external client, and confirm your
professional indemnity cover extends to software-assisted work.

**Client data.** Financial data for other people's businesses, under Canadian
privacy law. Encryption, tenant isolation, retention and deletion policies are
not a later feature. The architecture supports this; the operational practice has
to match before client three, not before client thirty.

**Your own time.** The largest risk in the plan is that selling and delivering
stage one consumes the time needed to build stage two. Budget for it explicitly,
and do not take a diagnostic client in a vertical you do not already understand.
