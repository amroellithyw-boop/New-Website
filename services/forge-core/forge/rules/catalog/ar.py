"""Controls 26-31: receivables, collections and unapplied cash.

Cash is the only thing an owner-managed business runs out of, so these controls
are the ones that most directly convert into money on the client's side.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import timedelta
from decimal import Decimal

from ...canonical.enums import RiskTier, Severity, TxnType
from ...engine.aging import build_aging, days_sales_outstanding
from ...evidence.packet import EvidenceRef
from ...money import msum
from ..base import Finding, Rule, RuleContext, register


@register
class AgingDeterioration(Rule):
    rule_id = "FOS-R026"
    version = "1"
    title = "Receivables aging is deteriorating"
    purpose = "Detect a worsening collection profile before it becomes a cash crisis."
    category = "accounts receivable"
    severity = Severity.HIGH
    risk_tier_floor = RiskTier.R2
    remediation = (
        "Work the 60-plus bucket by value, confirm each balance is agreed by the "
        "customer, and escalate anything disputed to the job owner."
    )
    evidence_required = ("aging this period", "aging prior period", "days sales outstanding")

    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        prior_date = ctx.period_start - timedelta(days=1)
        prior = build_aging(ctx.ledger, prior_date, "receivable")
        now_over60 = msum(
            (b.total for b in ctx.ar.buckets if b.label in ("61-90", "90+")), ctx.currency
        )
        prior_over60 = msum(
            (b.total for b in prior.buckets if b.label in ("61-90", "90+")), ctx.currency
        )
        growth = now_over60 - prior_over60
        dso = days_sales_outstanding(ctx.ar.total, ctx.pl_ytd.revenue.total, 365)
        if growth <= ctx.zero() or ctx.materiality.is_trivial(growth):
            return
        builder = ctx.packet(
            f"{self.rule_id}-aging", "Compare the aging profile against the prior period"
        )
        builder.calc("over 60 days now", "sum(61-90 and 90+ buckets)", now_over60)
        builder.calc(
            f"over 60 days at {prior_date.isoformat()}", "sum(61-90 and 90+ buckets)", prior_over60
        )
        builder.calc("increase", "now - prior", growth)
        builder.calc("total receivables", "sum(outstanding open items)", ctx.ar.total)
        if dso is not None:
            builder.calc(
                "days sales outstanding",
                "receivables / revenue year to date x 365",
                dso.quantize(Decimal("1")),
            )
        for item in ctx.ar.past_due(61)[:15]:
            party = ctx.ledger.parties.get(item.party_id)
            builder.ref(
                EvidenceRef(
                    kind="open_item",
                    ref_id=item.item.open_item_id,
                    label=(
                        f"{item.item.doc_number or item.item.open_item_id} "
                        f"{party.name if party else item.party_id} "
                        f"{item.days_past_due} days past due"
                    ),
                    amount=item.outstanding,
                    on=item.item.due_on,
                )
            )
        yield self.finding(
            ctx,
            suffix="over-60-growth",
            title=f"Receivables over 60 days grew by {growth.format()}",
            narrative=(
                f"Balances over 60 days past due rose from {prior_over60.format()} to "
                f"{now_over60.format()}"
                + (f", with days sales outstanding at {dso:.0f} days" if dso is not None else "")
                + ". Older balances collect at a materially lower rate."
            ),
            exposure=growth,
            packet=builder.build(),
        )


@register
class OverdueInvoicesAboveThreshold(Rule):
    rule_id = "FOS-R027"
    version = "1"
    title = "Individually significant overdue invoices"
    purpose = "Put the specific invoices worth chasing in front of a human, by value."
    category = "accounts receivable"
    severity = Severity.HIGH
    risk_tier_floor = RiskTier.R2
    remediation = (
        "Contact the customer with the invoice and proof of delivery attached. Record "
        "a promised payment date and diarise it."
    )
    evidence_required = ("invoice", "aging", "customer contact history")

    MIN_DAYS = 45

    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        for item in ctx.ar.past_due(self.MIN_DAYS):
            if item.outstanding < ctx.materiality.performance:
                continue
            party = ctx.ledger.parties.get(item.party_id)
            oi = item.item
            builder = ctx.packet(
                f"{self.rule_id}-{oi.open_item_id}",
                "Prove the invoice is outstanding and quantify the exposure",
            )
            builder.ref(
                EvidenceRef(
                    kind="open_item",
                    ref_id=oi.open_item_id,
                    label=f"Invoice {oi.doc_number or oi.open_item_id}",
                    source_ref=oi.lineage.raw_ref if oi.lineage else None,
                    amount=oi.original_amount,
                    on=oi.issued_on,
                )
            )
            applied = msum(
                (a.amount for a in ctx.ledger.applications_for(oi.open_item_id)), ctx.currency
            )
            builder.calc("invoice total", "original amount", oi.original_amount)
            builder.calc("payments applied", "sum(applications to date)", applied)
            builder.calc("outstanding", "invoice total - payments applied", item.outstanding)
            builder.calc("days past due", "as-of date - due date", item.days_past_due)
            builder.context(
                customer=party.name if party else item.party_id,
                terms_days=party.payment_terms_days if party else None,
                job=oi.job_id,
            )
            yield self.finding(
                ctx,
                suffix=oi.open_item_id,
                title=(
                    f"{party.name if party else item.party_id} owes {item.outstanding.format()}, "
                    f"{item.days_past_due} days past due"
                ),
                narrative=(
                    f"Invoice {oi.doc_number or oi.open_item_id} for {oi.original_amount.format()} "
                    f"was due {oi.due_on.isoformat()} and {item.outstanding.format()} remains "
                    "outstanding."
                ),
                exposure=item.outstanding,
                packet=builder.build(),
            )


@register
class UnmatchedCashReceipt(Rule):
    rule_id = "FOS-R028"
    version = "1"
    title = "Customer payment received without a matched invoice"
    purpose = (
        "Unapplied cash overstates receivables, triggers wrong collection calls, and "
        "hides revenue that was never invoiced."
    )
    category = "accounts receivable"
    severity = Severity.HIGH
    risk_tier_floor = RiskTier.R2
    remediation = (
        "Apply the payment to the correct invoice. If no invoice exists, determine "
        "whether the work was billed at all."
    )
    evidence_required = ("payment transaction", "open invoice list")

    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        applied_txns = {a.payment_txn_id for a in ctx.ledger.applications}
        offenders = [
            t
            for t in ctx.ledger.transactions
            if t.type is TxnType.PAYMENT_RECEIVED
            and ctx.period_start <= t.txn_date <= ctx.period_end
            and t.txn_id not in applied_txns
        ]
        for txn in offenders:
            if txn.absolute_value < ctx.materiality.trivial:
                continue
            party = ctx.ledger.parties.get(txn.party_id or "")
            open_for_party = [
                i for i in ctx.ar.items if i.party_id == txn.party_id
            ][:10]
            builder = ctx.packet(
                f"{self.rule_id}-{txn.txn_id}",
                "Show the unapplied receipt and the customer's open invoices",
            )
            builder.transaction(txn)
            for item in open_for_party:
                builder.ref(
                    EvidenceRef(
                        kind="open_item",
                        ref_id=item.item.open_item_id,
                        label=f"Open invoice {item.item.doc_number or item.item.open_item_id}",
                        amount=item.outstanding,
                        on=item.item.due_on,
                    )
                )
            builder.calc("receipt amount", "payment total", txn.absolute_value)
            builder.calc("open invoices for customer", "count", len(open_for_party))
            yield self.finding(
                ctx,
                suffix=txn.txn_id,
                title=(
                    f"{txn.absolute_value.format()} received from "
                    f"{party.name if party else txn.party_id} is unapplied"
                ),
                narrative=(
                    f"A receipt of {txn.absolute_value.format()} on {txn.txn_date.isoformat()} "
                    "is not applied to any invoice, so receivables and the collection queue are "
                    "both overstated."
                ),
                exposure=txn.absolute_value,
                packet=builder.build(),
            )


@register
class CreditBalancesInReceivables(Rule):
    rule_id = "FOS-R029"
    version = "1"
    title = "Credit balances in accounts receivable"
    purpose = (
        "A customer showing a credit is either owed a refund, has been double-"
        "credited, or has paid an invoice that was never raised."
    )
    category = "accounts receivable"
    severity = Severity.MEDIUM
    risk_tier_floor = RiskTier.R2
    remediation = (
        "Identify whether the credit is a genuine overpayment, an unbilled job, or a "
        "duplicated credit note, and clear it before it becomes a liability dispute."
    )
    evidence_required = ("customer balance", "open items")

    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        for party_id, balance in ctx.ar.by_party().items():
            if balance.minor_units >= 0 or ctx.materiality.is_trivial(balance):
                continue
            party = ctx.ledger.parties.get(party_id)
            items = [i for i in ctx.ar.items if i.party_id == party_id]
            builder = ctx.packet(
                f"{self.rule_id}-{party_id}", f"Show the credit balance for {party_id}"
            )
            for item in items[:12]:
                builder.ref(
                    EvidenceRef(
                        kind="open_item",
                        ref_id=item.item.open_item_id,
                        label=item.item.doc_number or item.item.open_item_id,
                        amount=item.outstanding,
                        on=item.item.issued_on,
                    )
                )
            builder.calc("net balance", "sum(outstanding open items)", balance)
            builder.calc("open items", "count", len(items))
            yield self.finding(
                ctx,
                suffix=party_id,
                title=(
                    f"{party.name if party else party_id} shows a credit balance of "
                    f"{abs(balance).format()}"
                ),
                narrative=(
                    f"Net receivables for {party.name if party else party_id} are "
                    f"{balance.format()}. A customer cannot owe a negative amount; either a "
                    "refund is due or revenue was never billed."
                ),
                exposure=abs(balance),
                packet=builder.build(),
            )


@register
class DebitBalancesInPayables(Rule):
    rule_id = "FOS-R030"
    version = "1"
    title = "Debit balances in accounts payable"
    purpose = (
        "A vendor showing a debit means an overpayment, a duplicate payment, or an "
        "unrecorded credit note. All three are recoverable cash."
    )
    category = "accounts payable"
    severity = Severity.HIGH
    risk_tier_floor = RiskTier.R2
    remediation = (
        "Request a vendor statement, confirm the overpayment, and recover it or "
        "apply it against the next invoice."
    )
    evidence_required = ("vendor balance", "open items", "vendor statement")
    involves_disbursed_cash = True

    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        for party_id, balance in ctx.ap.by_party().items():
            if balance.minor_units >= 0 or ctx.materiality.is_trivial(balance):
                continue
            party = ctx.ledger.parties.get(party_id)
            items = [i for i in ctx.ap.items if i.party_id == party_id]
            builder = ctx.packet(
                f"{self.rule_id}-{party_id}", f"Show the debit balance for {party_id}"
            )
            for item in items[:12]:
                builder.ref(
                    EvidenceRef(
                        kind="open_item",
                        ref_id=item.item.open_item_id,
                        label=item.item.doc_number or item.item.open_item_id,
                        amount=item.outstanding,
                        on=item.item.issued_on,
                    )
                )
            builder.calc("net balance", "sum(outstanding open items)", balance)
            yield self.finding(
                ctx,
                suffix=party_id,
                title=(
                    f"{party.name if party else party_id} has been overpaid by "
                    f"{abs(balance).format()}"
                ),
                narrative=(
                    f"Payables for {party.name if party else party_id} are {balance.format()}, "
                    "meaning more has been paid than billed. This is recoverable cash."
                ),
                exposure=abs(balance),
                packet=builder.build(),
            )


@register
class StaleUnappliedCredits(Rule):
    rule_id = "FOS-R031"
    version = "1"
    title = "Old unapplied cash and credits"
    purpose = (
        "Unapplied amounts older than a quarter are never going to resolve "
        "themselves and quietly distort both aging and revenue."
    )
    category = "accounts receivable"
    severity = Severity.MEDIUM
    risk_tier_floor = RiskTier.R2
    remediation = "Match, refund or write off each item with a documented conclusion."
    evidence_required = ("unapplied items", "customer correspondence")

    STALE_DAYS = 90

    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        applied = {a.payment_txn_id for a in ctx.ledger.applications}
        cutoff = ctx.period_end - timedelta(days=self.STALE_DAYS)
        stale = [
            t
            for t in ctx.ledger.transactions
            if t.type is TxnType.PAYMENT_RECEIVED
            and t.txn_id not in applied
            and t.txn_date <= cutoff
        ]
        if not stale:
            return
        total = msum((t.absolute_value for t in stale), ctx.currency)
        if ctx.materiality.is_trivial(total):
            return
        builder = ctx.packet(
            f"{self.rule_id}-stale", f"Unapplied receipts older than {self.STALE_DAYS} days"
        )
        builder.transactions(sorted(stale, key=lambda t: t.txn_date)[:20])
        builder.calc("unapplied receipts", "count", len(stale))
        builder.calc("total value", "sum(receipt amounts)", total)
        builder.calc("age threshold", "days", self.STALE_DAYS)
        yield self.finding(
            ctx,
            suffix="stale-unapplied",
            title=f"{len(stale)} receipt(s) totalling {total.format()} remain unapplied",
            narrative=(
                f"{total.format()} of customer receipts older than {self.STALE_DAYS} days have "
                "never been matched to an invoice."
            ),
            exposure=total,
            packet=builder.build(),
        )
