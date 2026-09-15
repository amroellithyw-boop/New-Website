# Scope language for legal review

Everything below is the exact wording a client or prospect will read. Send
this file to a lawyer as it stands. Each block says where it appears, what it
is meant to achieve, and the questions to ask. Change the text in the file
named, and the tests will keep it there.

## 1. Footer on every client review report

Where: `services/forge-core/forge/report/templates/diagnostic.html.j2`, the
closing `<footer>`.

> Prepared by ForgeOS for {client name}. This review identifies control
> weaknesses and accounting exceptions from the accounting records provided.
> It is not an audit, a review engagement, or a tax filing opinion, and it
> does not provide assurance on the financial statements as a whole. Tax
> positions, payroll remittances and filings identified here should be
> confirmed with the responsible professional before action.

Purpose: make clear the report is a control review on records as supplied,
not an assurance engagement under CPA Canada standards, and not tax advice.

Questions for counsel:
- Does "review" risk being read as a CSRE 2400 review engagement in Ontario?
  If so, is "control review" or "diagnostic" the safer noun?
- Is a reliance limitation needed (prepared solely for the named client, not
  for lenders or third parties)?
- Should the firm's legal name and CPA designation, if any, appear here rather
  than "ForgeOS"?

## 2. Proposal to a prospect

Where: `services/forge-core/forge/growth/proposal.py`, `render_proposal`.

> We reviewed your books with {n} controls. {n} items came out, {n} of them
> critical. {amount} is money that has been overpaid, left unbilled, or is
> overdue from customers.
>
> Every number above traces to a source record in the attached review.
> Nothing here is estimated.
>
> Against a first-year cost of {amount}, the recoverable cash identified today
> is {amount}, a {x} return before any of the ongoing benefit.

Purpose: state the diagnostic result and the engagement price plainly.

Questions for counsel:
- "Recoverable cash" is a computed figure: overpayments, unbilled and overdue
  amounts. Does presenting it alongside the fee create an implied guarantee
  of recovery? Suggested softening: "identified as recoverable, subject to
  collection and vendor response".
- "Nothing here is estimated" is true of the numbers but should it be
  qualified as "from the records provided as at {date}"?
- Is an engagement-letter reference needed before a price is quoted?

## 3. Outreach and follow-up emails

Where: `services/forge-core/forge/growth/outreach.py`, drafted by a model
under this instruction:

> You write short plain-text outreach emails for {sender} at {firm}, a
> Canadian accounting and finance firm. Plain, specific, no hype, no claims
> you cannot support, one clear ask.

Purpose: cold outreach offering a paid diagnostic.

Questions for counsel:
- CASL (Canada's Anti-Spam Legislation): what consent basis covers the first
  email to a business, and what identification and unsubscribe text must be
  present? The draft currently carries neither an unsubscribe line nor a
  mailing address; both can be added as fixed text.

## 4. Collection emails drafted on behalf of a client

Where: `services/forge-core/forge/actions/drafts.py`, `draft_for` on overdue
receivables. Drafts are addressed from the client to their customer, referring
to invoice number, amount and days past due, and asking for payment or a
date.

Questions for counsel:
- Ontario's Collection and Debt Settlement Services Act: does a bookkeeping
  firm sending reminders in the client's name, from the client's mailbox,
  fall outside "collection agency" activity? What wording keeps it there?

## 5. Client-questions email and onboarding plan

Where: `services/forge-core/forge/agents/communications.py`. Drafts ask the
client for documents and explanations; no advice is given.

No specific concern; included for completeness.

## 6. General

- The system never files, pays or posts anything. Every output is a draft or
  a report for a human to act on. Confirm the engagement letter says so.
- Data residency: client records are processed where the software runs and,
  for material items only, bounded evidence excerpts are sent to model
  vendors. The vendors and the excerpt boundaries are listed in
  `docs/07-ai-providers.md`. Confirm what the engagement letter and privacy
  policy must disclose (PIPEDA).
