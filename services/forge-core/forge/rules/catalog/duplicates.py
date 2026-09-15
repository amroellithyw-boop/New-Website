"""Controls 6-7: duplicate vendor bills and duplicate customer invoices.

Duplicate payment is the single most recoverable loss in an SMB. It is also the
easiest finding to get wrong: recurring monthly bills for the same amount from
the same vendor are *not* duplicates, so clustering alone produces noise. The
controls below combine document-number identity, amount identity and date
proximity, and explicitly stand down when a recurring profile explains the
repetition.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Iterable

from ...canonical.enums import RiskTier, Severity, TxnType
from ...engine.features import find_duplicate_clusters, recurring_profiles
from ...money import Money
from ..base import Finding, Rule, RuleContext, register


class _DuplicateBase(Rule):
    txn_type: TxnType = TxnType.BILL
    noun = "bill"
    party_word = "vendor"

    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        population = [
            t
            for t in ctx.ledger.transactions
            if t.type is self.txn_type and ctx.period_start <= t.txn_date <= ctx.period_end
        ]
        if not population:
            return
        profiles = recurring_profiles(ctx.ledger, ctx.period_start, ctx.period_end)
        recurring_amounts = {
            (p.account_id, p.party_id, p.median_amount.minor_units) for p in profiles.values()
        }

        # Two passes: identical document numbers anywhere in the window, then
        # identical amounts from the same counterparty within a short window.
        exact = find_duplicate_clusters(population, within_days=365, use_doc_number=True)
        near = find_duplicate_clusters(population, within_days=21, use_doc_number=False)

        emitted: set[str] = set()
        for cluster, method, confidence, severity in (
            [(c, "same document number", Decimal("0.95"), Severity.CRITICAL) for c in exact]
            + [(c, "same amount within 21 days", Decimal("0.6"), Severity.HIGH) for c in near]
        ):
            key = "+".join(sorted(t.txn_id for t in cluster.transactions))
            if key in emitted:
                continue
            first = cluster.transactions[0]
            if method != "same document number" and self._looks_recurring(
                ctx, first, recurring_amounts
            ):
                continue
            if ctx.materiality.is_trivial(cluster.exposure):
                continue
            emitted.add(key)
            party = ctx.ledger.parties.get(first.party_id or "")
            party_name = party.name if party else (first.party_id or "unknown")
            builder = ctx.packet(
                f"{self.rule_id}-{cluster.fingerprint}",
                f"Show the candidate duplicate {self.noun}s and their differences",
            )
            builder.transactions(cluster.transactions)
            builder.calc("copies found", "count(matching documents)", cluster.count)
            builder.calc("value of each", "document total", cluster.amount)
            builder.calc(
                "amount at risk", "document total x (copies - 1)", cluster.exposure
            )
            builder.calc("days between first and last", "date span", cluster.max_days_apart)
            builder.calc("match basis", "clustering method", method)
            builder.context(counterparty=party_name, match_method=method)
            yield self.finding(
                ctx,
                suffix=cluster.fingerprint,
                title=(
                    f"{cluster.count} matching {self.noun}s from {party_name} "
                    f"totalling {cluster.exposure.format()} at risk"
                ),
                narrative=(
                    f"{cluster.count} {self.noun}s of {cluster.amount.format()} from {party_name} "
                    f"appear within {cluster.max_days_apart} days, matched on {method}. "
                    f"If these are copies, {cluster.exposure.format()} is recorded twice."
                ),
                exposure=cluster.exposure,
                packet=builder.build(),
                severity=severity,
                confidence=confidence,
            )

    def _looks_recurring(self, ctx: RuleContext, txn, recurring_amounts) -> bool:
        """Stand down when a monthly subscription explains the repetition."""
        for line in txn.lines:
            key = (line.account_id, txn.party_id, line.amount.minor_units)
            if key in recurring_amounts:
                return True
        return False


@register
class DuplicateVendorBills(_DuplicateBase):
    rule_id = "FOS-R006"
    version = "1"
    title = "Potential duplicate vendor bills"
    purpose = (
        "Find bills recorded more than once, before they are paid more than once."
    )
    category = "accounts payable"
    severity = Severity.CRITICAL
    risk_tier_floor = RiskTier.R3
    remediation = (
        "Compare the source documents. Void the duplicate, and if payment already "
        "went out, raise a vendor credit and request recovery."
    )
    evidence_required = ("both bills", "vendor statement")
    txn_type = TxnType.BILL
    noun = "bill"
    party_word = "vendor"


@register
class DuplicateCustomerInvoices(_DuplicateBase):
    rule_id = "FOS-R007"
    version = "1"
    title = "Potential duplicate customer invoices"
    purpose = (
        "Duplicated invoices overstate revenue and receivables and damage the "
        "customer relationship when chased."
    )
    category = "accounts receivable"
    severity = Severity.HIGH
    risk_tier_floor = RiskTier.R3
    remediation = (
        "Confirm with the job record whether both were genuinely billed. Credit the "
        "duplicate and notify the customer before it reaches collections."
    )
    evidence_required = ("both invoices", "job record")
    txn_type = TxnType.INVOICE
    noun = "invoice"
    party_word = "customer"
