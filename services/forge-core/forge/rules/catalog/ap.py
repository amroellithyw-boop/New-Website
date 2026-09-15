"""Controls 32-34: payables discipline, spend anomalies and personal expense.

Control 34 is deliberately conservative. Calling an owner's expense personal is
an accusation, so the rule reports a *pattern needing confirmation* with the
evidence attached, and never asserts intent.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Iterable

from ...canonical.enums import AccountSubtype, RiskTier, Severity, TxnType
from ...engine.features import median_money, month_key, monthly_series, robust_zscore
from ...money import Money, msum
from ..base import Finding, Rule, RuleContext, register


@register
class PaymentWithoutBill(Rule):
    rule_id = "FOS-R032"
    version = "1"
    title = "Vendor payment with no underlying bill"
    purpose = (
        "A payment that does not trace to an approved bill has bypassed the "
        "approval chain entirely. This is the control fraud most often exploits."
    )
    category = "accounts payable"
    severity = Severity.CRITICAL
    risk_tier_floor = RiskTier.R3
    remediation = (
        "Obtain the invoice and the approval for the payment. If neither exists, "
        "treat this as a suspected unauthorised disbursement and escalate to the owner."
    )
    evidence_required = ("payment transaction", "bill", "approval record")

    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        applied_txns = {a.payment_txn_id for a in ctx.ledger.applications}
        offenders = [
            t
            for t in ctx.ledger.transactions
            if t.type is TxnType.BILL_PAYMENT
            and ctx.period_start <= t.txn_date <= ctx.period_end
            and t.txn_id not in applied_txns
        ]
        for txn in offenders:
            if ctx.materiality.is_trivial(txn.absolute_value):
                continue
            party = ctx.ledger.parties.get(txn.party_id or "")
            builder = ctx.packet(
                f"{self.rule_id}-{txn.txn_id}", "Show the payment and the absence of a bill"
            )
            builder.transaction(txn)
            open_bills = [i for i in ctx.ap.items if i.party_id == txn.party_id]
            builder.calc("payment amount", "payment total", txn.absolute_value)
            builder.calc("bills applied", "count(applications for this payment)", 0)
            builder.calc("open bills for vendor", "count", len(open_bills))
            builder.context(vendor=party.name if party else txn.party_id)
            yield self.finding(
                ctx,
                suffix=txn.txn_id,
                title=(
                    f"{txn.absolute_value.format()} paid to "
                    f"{party.name if party else txn.party_id} with no bill applied"
                ),
                narrative=(
                    f"A payment of {txn.absolute_value.format()} on {txn.txn_date.isoformat()} "
                    "is not applied against any recorded bill, so nothing evidences what was "
                    "bought or who approved it."
                ),
                exposure=txn.absolute_value,
                packet=builder.build(),
            )


@register
class VendorSpendSpike(Rule):
    rule_id = "FOS-R033"
    version = "1"
    title = "Expense spike by vendor or category"
    purpose = (
        "Compare this period's spend with the vendor's own history rather than a "
        "flat threshold, so a large but normal vendor does not fire every month."
    )
    category = "accounts payable"
    severity = Severity.MEDIUM
    risk_tier_floor = RiskTier.R2
    remediation = (
        "Confirm the spend relates to identified jobs and that pricing matches the "
        "agreed rate card."
    )
    evidence_required = ("vendor history", "current period bills")

    LOOKBACK_DAYS = 400
    Z_THRESHOLD = Decimal("3.0")

    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        hist_start = ctx.period_end - timedelta(days=self.LOOKBACK_DAYS)
        by_vendor_month: dict[str, dict[str, Money]] = {}
        for txn in ctx.ledger.transactions:
            if txn.type is not TxnType.BILL or txn.txn_date < hist_start:
                continue
            pid = txn.party_id
            if pid is None:
                continue
            key = month_key(txn.txn_date)
            bucket = by_vendor_month.setdefault(pid, {})
            bucket[key] = bucket.get(key, Money.zero(ctx.currency)) + txn.absolute_value

        current_key = month_key(ctx.period_end)
        for pid, months in by_vendor_month.items():
            current = months.get(current_key)
            if current is None:
                continue
            history = [v for k, v in months.items() if k != current_key]
            if len(history) < 5:
                continue
            z = robust_zscore(current, history)
            if z is None or z < self.Z_THRESHOLD:
                continue
            typical = median_money(history, ctx.currency)
            delta = current - typical
            if delta < ctx.materiality.performance:
                continue
            party = ctx.ledger.parties.get(pid)
            bills = [
                t
                for t in ctx.ledger.transactions
                if t.type is TxnType.BILL
                and t.party_id == pid
                and ctx.period_start <= t.txn_date <= ctx.period_end
            ]
            builder = ctx.packet(
                f"{self.rule_id}-{pid}", f"Compare this period's spend with history for {pid}"
            )
            builder.transactions(sorted(bills, key=lambda t: -t.absolute_value.minor_units)[:12])
            builder.calc("spend this period", "sum(bills in period)", current)
            builder.calc("typical monthly spend", "median of prior months", typical)
            builder.calc("increase", "current - typical", delta)
            builder.calc(
                "robust z-score",
                "(current - median) / (1.4826 x median absolute deviation)",
                z.quantize(Decimal("0.01")),
            )
            builder.context(months_of_history=len(history), vendor=party.name if party else pid)
            yield self.finding(
                ctx,
                suffix=pid,
                title=(
                    f"Spend with {party.name if party else pid} is {delta.format()} above its norm"
                ),
                narrative=(
                    f"{current.format()} was billed this period against a typical "
                    f"{typical.format()}, a robust z-score of {z:.1f} over {len(history)} months "
                    "of history."
                ),
                exposure=delta,
                packet=builder.build(),
                confidence=Decimal("0.65"),
            )


@register
class PersonalExpensePattern(Rule):
    rule_id = "FOS-R034"
    version = "1"
    title = "Spending pattern requiring business-purpose confirmation"
    purpose = (
        "Identify card and cash spending in categories that are commonly personal, "
        "so the business purpose can be documented before a tax authority asks. "
        "The control reports a pattern to confirm; it never concludes intent."
    )
    category = "accounts payable"
    severity = Severity.MEDIUM
    risk_tier_floor = RiskTier.R2
    remediation = (
        "Attach the business purpose and attendees to each item, or reclassify it to "
        "shareholder draws. Undocumented amounts are the first thing an audit disallows."
    )
    evidence_required = ("transaction", "receipt", "business purpose note")

    WATCH_SUBTYPES = (AccountSubtype.OPERATING_EXPENSE,)
    WATCH_NAME_WORDS = ("meals", "entertainment", "travel", "gift", "clothing", "personal")

    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        watched = [
            a
            for a in ctx.ledger.accounts.values()
            if any(w in a.name.lower() for w in self.WATCH_NAME_WORDS)
        ]
        for acct in watched:
            hits = [
                (t, l)
                for (t, l) in ctx.ledger.postings(acct.account_id)
                if ctx.period_start <= t.txn_date <= ctx.period_end and l.amount.minor_units > 0
            ]
            if not hits:
                continue
            total = msum((l.amount for _t, l in hits), ctx.currency)
            undocumented = [
                (t, l) for (t, l) in hits if not t.document_refs
            ]
            undoc_total = msum((l.amount for _t, l in undocumented), ctx.currency)
            if ctx.materiality.is_trivial(undoc_total):
                continue
            builder = ctx.packet(
                f"{self.rule_id}-{acct.account_id}",
                f"List {acct.name} spending without attached support",
            )
            for txn, _l in sorted(undocumented, key=lambda p: -p[1].amount.minor_units)[:20]:
                builder.transaction(txn)
            builder.calc("total in category", "sum(period postings)", total)
            builder.calc("without attached document", "sum(postings with no document)", undoc_total)
            builder.calc("items without support", "count", len(undocumented))
            builder.policy(
                "Meals, entertainment and travel require an attached receipt and a note "
                "recording the business purpose and attendees."
            )
            yield self.finding(
                ctx,
                suffix=acct.account_id,
                title=(
                    f"{undoc_total.format()} of {acct.name} has no attached support"
                ),
                narrative=(
                    f"{len(undocumented)} item(s) totalling {undoc_total.format()} were posted to "
                    f"{acct.name} with no supporting document. Each needs a recorded business "
                    "purpose or reclassification to shareholder draws."
                ),
                exposure=undoc_total,
                packet=builder.build(),
                confidence=Decimal("0.75"),
            )
