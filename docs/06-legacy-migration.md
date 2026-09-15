# Migrating ProfitForge into ForgeOS

An assessment of `amroellithyw-boop/forge-os-legacy`, what has already been
carried across, and what should happen to the rest.

## What the legacy system actually is

Considerably more than a half-built prototype. It is a working, deployed
platform with a real client on it.

| | |
| --- | --- |
| Backend | Express, 7,500 lines in one file, 105 endpoints |
| Frontend | React, 33,195 lines in one file, 132 components |
| Data | Supabase Postgres, 17 tables, plus browser local storage |
| Integrations | QuickBooks Online in production, Anthropic with vision, Resend |
| Domain | Canadian payroll, HST, WSIB, capital cost allowance, 22 industries |

The instinct to start clean is right for the code and wrong for the knowledge.
Three things in that repository took real expertise to produce and would take
months to reproduce. Everything else is scaffolding.

## Already migrated

### Industry intelligence, the most valuable thing in the repository

`client/src/lib/business-data.js` holds 22 NAICS industries with WSIB rate
groups, seasonality, deferred-revenue treatment, capital cost allowance classes
and the specific errors each trade makes, joined to 33 cost structures across
five revenue tiers. It is a practising firm's accumulated judgement written down.

It now lives in `forge/industry/` as versioned data with a typed loader, and it
drives five new controls. On the demonstration contractor those controls produce
the two most valuable findings in the whole report:

- gross margin 2.2 points under its industry band, worth $37,830 a year;
- seasonal contract revenue recognised with no deferred balance carried.

Neither of those is detectable from a ledger alone. They need to know what this
kind of business at this size is supposed to look like, and that is precisely
the part a competitor cannot copy in an afternoon.

One parsing subtlety worth recording: the ranges use an en dash as separator and
an ASCII hyphen for a negative lower bound, so `-10–15%` means minus ten to plus
fifteen. Splitting on the first dash inverts the band.

### Canadian payroll

The 2026 constants and bracket structure are carried over and rebuilt on exact
integer cents in `forge/payroll/`, versioned by tax year and province. The rate
table records whether anyone has verified it against CRA T4127; the 2026 table
is currently marked unverified rather than implying an authority it does not
have.

### QuickBooks Online

The OAuth flow, token refresh and query mechanics are proven code and were
lifted directly. What is new is that the connector is read-only, normalises into
the canonical model with lineage, and ties out against QuickBooks' own trial
balance report before anything downstream is treated as fact.

## Defects found in the legacy code

These were found while porting. Each one is live in the deployed system.

### 1. Employer CPP2 is remitted twice

`server.js:6913`. `cpp_employer` is computed as `cpp_employee + cpp2_employee`,
and then `cpp2_employer` is added to the remittance separately. The employer's
second-tier CPP contribution is therefore counted twice.

For any employee earning above the year's maximum pensionable earnings, the
over-remittance reaches the full annual CPP2 maximum of **$416 per employee per
year**. It is now fixed, with a regression test that reproduces the legacy
formula and asserts the difference is exactly one employee CPP2 amount.

### 2. Income tax is under-withheld

The basic personal amount is subtracted from income before the brackets are
applied. It is a non-refundable credit at the lowest rate, not a deduction.
Treating it as a deduction shifts every bracket downward.

At $100,000 of income the federal under-withholding exceeds **$900 a year**, and
the employee discovers it when they file. Fixed and tested.

### 3. Contribution caps are applied per period

Annual CPP, CPP2 and EI maxima are enforced by dividing the annual cap by the
number of pay periods. That only produces the right annual figure when every
period's pay is identical. Overtime, a bonus or a seasonal crew breaks it.

The replacement tracks year to date, and a test proves a full year lands exactly
on $4,230.45, $416.00 and $1,123.07 even when one period is fourteen times the
others.

### 4. Journal entries are posted to QuickBooks without a balance check

`server.js:6176`. The endpoint resolves accounts, aborts if resolution fails,
and then posts. It never checks that debits equal credits. QuickBooks will
reject a clearly unbalanced entry, but the check belongs on the near side, and a
rounding difference inside a multi-line entry is exactly what slips through.

### 5. Posting is not idempotent

There is no idempotency key and no duplicate check. A network timeout followed
by a retry posts the entry twice. The code comments record that silent posting
failures already happened once, for a different reason, across many sessions.

### 6. Money is floats throughout

Both the payroll engine and the posting path use JavaScript numbers and
`Math.round(x*100)/100`. This is the defect that produces one-cent close
differences nobody can find.

### 7. Authentication is a single shared secret

`server.js:232`. One environment variable, compared with `===`, protecting
financial data for multiple businesses. No user accounts, no roles, no audit of
who did what, and a non-constant-time comparison.

For a single-operator tool this is a deliberate trade. Before a second client's
data is on the system it is the highest-priority item in the repository.

### 8. QuickBooks tokens are stored in plaintext

`server.js:285` writes every client's access and refresh tokens to a JSON file
on disk **and** to Supabase, both unencrypted. A refresh token is a standing
grant of read and write access to a client's accounting file.

### 9. Client financial data lives in browser local storage

554 local storage calls in `App.js`, scoped per client by a key prefix. Tenant
isolation implemented in the browser is not tenant isolation. It also means the
data is unavailable on another device and invisible to any server-side control.

## What to drop

| Drop | Size | Why |
| --- | --- | --- |
| `client/src/App.js` | 2.1 MB, 33,195 lines, 132 components | Beyond maintenance. Rebuild the interface against the API |
| `attached_assets/` | 3.5 MB, 85 pasted prompt files | Historical record of build sessions, not code |
| `archive/patches/` | 2.3 MB | One-off patch scripts already applied |
| `fix-phase0-*.js` | 7 files at the root | Same |
| Local storage persistence | — | Replaced by server-side tenant isolation |
| Shared-secret auth | — | Replaced by a managed provider with accounts and roles |

Dropping these removes roughly 8 MB and about 35,000 lines while losing nothing
that took judgement to produce.

## What still needs porting

In priority order.

**1. The remaining QuickBooks entities.** Only `JournalEntry` is normalised into
the canonical ledger. Invoice, Bill, Payment, BillPayment, Purchase, Deposit and
Transfer are fetched and stored but not mapped, and the tie-out will fail while
they are missing, which is the correct behaviour. This is the single blocking
item before a real client file can be reviewed.

**2. The three-pass document pipeline.** Vision extraction, then vendor research,
then accounting treatment, with a different model class at each stage. The design
is sound and matches the gateway's routing model; it needs rebuilding against the
evidence-packet contract so that extracted document text arrives as data rather
than as instructions.

**3. Chart-of-account mapping and vendor research.** `resolveGLAccount` and the
vendor cache encode real mapping judgement and should become a normalisation
concern with a persistent, reviewable mapping table.

**4. HST and WSIB filing workflows.** Currently endpoints plus Supabase tables.
They become close workflows with evidence and a review chain.

**5. The Supabase schema.** Seventeen tables that name the right nouns. Use them
as a starting point for the persistence layer rather than a specification; the
canonical model is the specification now.

## Order of work

1. Finish the QuickBooks entity mappers until a real company file ties out
   exactly. Nothing else matters until this passes.
2. Connect one client in read-only mode and run the control catalogue in shadow
   mode against a file whose problems you already know.
3. Tune the false-positive rate against that file.
4. Replace the shared secret with real authentication and move tokens into a
   managed secrets store, before a second client's data is present.
5. Sell three paid diagnostics.
6. Port the document pipeline, because by then you will know which documents
   actually matter.

## What this means commercially

The legacy system is worth more than it looks, and less than it feels. The
interface, the endpoints and the wiring are replaceable. The industry knowledge,
the Canadian statutory logic and the proven QuickBooks integration are not, and
all three are now in ForgeOS on a foundation that ties out to the cent.

The nine defects above are not an indictment. They are the ordinary cost of
building a real system quickly and alone, and every one of them is the kind of
error a deterministic engine with a tie-out gate and a test suite exists to make
impossible rather than unlikely.
