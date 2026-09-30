# The fourplex engagement: what is automated, what is judged, what is still yours

This maps every deliverable in a four-phase development-and-rental
engagement for a non-resident owner onto ForgeOS. The example facts file
carries the engagement's own figures with the names replaced, so every
number below is reproducible with `forge engagement plan`.

The standard it is built to: what the best bookkeeper, tax accountant,
controller, analyst, real-estate strategist and CFO would each insist on,
combined into one deterministic system that a lender, an appraiser and the
CRA could each audit. Where the answer needs a document, an appraisal or a
professional's sign-off, the plan says *confirm* rather than pretending.

## Phase 1: Tax, ownership and GST/HST action plan

| Letter deliverable | Automated | Judged by a person |
| --- | --- | --- |
| Departure and residency review, 2020 return and later filings | `forge engagement residency`: unreported departure, T1161 requirement and penalty, which later years needed a return, the change-of-use deemed disposition and its filing date | Residency itself. The system lists the ties to document and says when an NR73 is worth the exposure |
| GST/HST account review and 2021 catch-up | Outstanding periods, the co-owner registration gap, invoices addressed to one owner, credits unclaimed to date | Whether the 2021 return is a routine nil |
| PBRH, NRRP and Ontario rebate per building | `forge engagement hst`: the enhanced rebate test per building, self-supply tax at FMV, per-unit standard rebates with the federal phase-out and the Ontario cap, input tax credits over the build, the rebate application deadline | Whether a detached garden suite is its own residential complex; FMV at completion (appraisal) |
| Ownership comparison and written recommendation | `forge engagement ownership`: land transfer tax (province and Toronto), speculation taxes by citizenship, s.216 tax with the 48% surtax against a non-CCPC corporation with treaty withholding, the requirements and risks of each route including s.85, s.116 and lender consent | Citizenship of both owners; Italian tax consequences; the lawyer on partnership formation and land transfer tax |
| Construction accounting structure, financing schedules, document workflow, GST/HST support schedule | `forge engagement chart` (the chart with CCA classes), `forge engagement project` (cost to complete, headroom, owner equity by month), the workflow and support-schedule text in the plan | Setting up the QuickBooks company from the chart |
| Written action plan and meeting | `forge engagement plan --out plan.md`: eleven sections, every number computed, sources cited, questions listed | Reading it, and the meeting |

## Phase 2: Construction accounting and the monthly Owner Finance Pack

| Letter deliverable | Automated |
| --- | --- |
| Monthly project books and reconciliations | The engine: tie-out, four-pass bank reconciliation with the unexplained-difference proof, period control |
| Costs by category, contributions, draws, interest, HST | `forge engagement pack`: cost to date by category against budget, draws against each facility, capitalised interest, HST recoverable, holdbacks, owner contributions |
| GST/HST support file | The recoverable account and the per-bill schedule the pack describes; `forge tax return` for the working paper |
| Missing support | Bills over $500 with no document attached, listed by vendor and amount |
| Projected cash requirement | The month-by-month spend, draw and owner contribution schedule with interest on the drawn balance |
| Items requiring attention | Six development controls (FOS-R057 to R062): interest expensed during construction, HST buried in cost, holdback not retained, draw beyond facility, cost to land after ground-breaking, soft costs expensed. Plus the fifty-six general controls |

The pack runs today on a synthetic project ledger built from the facts
file. It runs on the real file the day the QuickBooks entity mappers are
finished, which is the same blocker as every other client.

## Phase 3: Completion, self-supply and rebate filing

Computed: the self-supply tax per building, the enhanced and standard
rebates per unit from floor-area shares, the net payable, the return period
and the two-year application deadline. The forms (GST524, GST525,
RC7524-ON) are not rendered; the numbers to enter on them are. What a person
does: the appraisal, the floor-area certificate, the occupancy dates, and
the filing.

## Phase 4: Rental accounting and non-resident administration

Computed: `forge engagement rental` gives the stabilised pro forma (gross
rent, vacancy, operating expenses, management, net operating income, cap
rate on the appraisal), debt service at the takeout rate, coverage, the debt
the income can support at a 1.2x covenant and the shortfall against the
facilities, CCA at 4% and at the proposed 10% with the rental-loss limit,
the s.216 tax split between the owners, and withholding with and without an
NR6. `forge engagement residency` gives the NR6, Part XIII, NR4 and s.216
calendar. The chart carries the Part XIII payable and tenant deposit
accounts. The rental industry pack (NAICS 531110) sets the benchmarks and
the common errors.

Not computed: the T1159 return itself, NR4 slips, and the year-end package;
the schedules they need are.

## What a first-class team would add that this does not yet do

- **Read the actual documents.** The facts file is typed. The document
  pipeline can extract the purchase agreement, the loan commitment and the
  appraisal into it; that wiring is a day's work once a real file exists.
- **Research with citations on demand.** The plan cites the rules as
  written into the code. A model-assisted research step that fetches the
  current CRA guidance for each *confirm* item and returns a cited memo is
  designed (the gateway supports web search on models that offer it) but not
  wired to this command.
- **Draw requests.** The pack has every number a lender's draw request
  needs; the lender's form is not rendered.
- **Condominium sale pro forma.** The sale route is modelled as conditions,
  not as a unit-by-unit cash flow, because prices and timing are unknown.
- **Italian side.** Out of scope by the letter and by competence.

## Sources applied

- ETA s.191 (self-supply), s.256.2 (rental rebate), RC4231; the September
  2023 federal enhancement and the November 2023 Ontario mirror
- ITA s.18(3.1) (construction soft costs and interest), s.40(2)(b) and s.45
  (principal residence and change of use), s.116, s.128.1, s.216 and the 48%
  surtax, Reg. 1100(11) (rental CCA limit)
- Budget 2024 accelerated 10% CCA for purpose-built rental (flagged confirm)
- Ontario Land Transfer Tax Act and Toronto MLTT; Ontario NRST 25% and
  Toronto MNRST 10%; designated land includes fourplexes
- Ontario Construction Act s.22 (10% holdback)
- Canada-Italy tax convention, dividends and residence articles
