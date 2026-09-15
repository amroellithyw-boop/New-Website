"""Controls 8-10: bank and credit-card reconciliation, and stale items.

An unreconciled bank account means the books are not evidence of anything. These
controls treat an unexplained difference as CRITICAL and never allow it to be
described as a rounding issue.
"""

from __future__ import annotations

from collections.abc import Iterable

from ...canonical.enums import RiskTier, Severity
from ...evidence.packet import EvidenceRef
from ...money import Money
from ..base import Finding, Rule, RuleContext, register


class _ReconBase(Rule):
    credit_card = False

    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        for bank_id, result in ctx.reconciliations.items():
            bank = ctx.ledger.bank_accounts.get(bank_id)
            if bank is None or bank.is_credit_card != self.credit_card:
                continue
            unexplained = result.unexplained_difference
            bank_only = result.bank_only_items
            if unexplained.is_zero and not bank_only:
                continue

            builder = ctx.packet(
                f"{self.rule_id}-{bank_id}",
                f"Prove the reconciliation of {bank.name}",
            )
            builder.calc("statement closing balance", "per bank statement", result.statement_closing)
            builder.calc("ledger closing balance", "per general ledger", result.ledger_closing)
            builder.calc("raw difference", "statement - ledger", result.difference)
            builder.calc(
                "outstanding items",
                "ledger entries not yet on the statement",
                result.unmatched_ledger_total,
                count=len(result.outstanding_items),
            )
            builder.calc(
                "bank-only items",
                "statement entries with no ledger record",
                result.unmatched_statement_total,
                count=len(bank_only),
            )
            builder.calc(
                "unexplained difference",
                "statement - ledger + outstanding - bank-only",
                unexplained,
            )
            for line in bank_only[:20]:
                builder.ref(
                    EvidenceRef(
                        kind="statement_line",
                        ref_id=line.statement_line_id,
                        label=f"{line.posted_on.isoformat()} {line.description}",
                        amount=line.amount,
                        on=line.posted_on,
                    )
                )
                builder.note("bank_statement", line.statement_line_id, line.description)

            exposure = abs(unexplained) if not unexplained.is_zero else abs(
                result.unmatched_statement_total
            )
            if bank_only and unexplained.is_zero:
                title = (
                    f"{len(bank_only)} bank transaction(s) on {bank.name} are missing "
                    "from the ledger"
                )
                narrative = (
                    f"{len(bank_only)} item(s) totalling {result.unmatched_statement_total.format()} "
                    f"appear on the {bank.name} statement with no matching entry in the books. "
                    "These are fees, interest, returned payments or off-book spending, and each "
                    "is a missing journal entry."
                )
                severity = Severity.HIGH
            else:
                title = f"{bank.name} reconciliation is out by {unexplained.format()}"
                narrative = (
                    f"After allowing for {len(result.outstanding_items)} outstanding item(s), "
                    f"{unexplained.format()} of the difference between the statement and the "
                    "ledger is unexplained. This must not be plugged to a suspense account."
                )
                severity = Severity.CRITICAL
            yield self.finding(
                ctx,
                suffix=bank_id,
                title=title,
                narrative=narrative,
                exposure=exposure,
                packet=builder.build(),
                severity=severity,
            )


@register
class BankReconciliationDifference(_ReconBase):
    rule_id = "FOS-R008"
    version = "1"
    title = "Bank reconciliation difference"
    purpose = "Prove every bank account agrees with its statement at period end."
    category = "reconciliation"
    severity = Severity.CRITICAL
    risk_tier_floor = RiskTier.R3
    remediation = (
        "Identify each unmatched item and post the missing entry. Do not plug the "
        "difference; an unexplained bank difference is a live control failure."
    )
    evidence_required = ("bank statement", "ledger postings", "outstanding item list")
    involves_disbursed_cash = True
    credit_card = False


@register
class CreditCardReconciliationDifference(_ReconBase):
    rule_id = "FOS-R009"
    version = "1"
    title = "Credit card reconciliation difference"
    purpose = "Prove every card account agrees with its statement at period end."
    category = "reconciliation"
    severity = Severity.HIGH
    risk_tier_floor = RiskTier.R3
    remediation = (
        "Match every card transaction to a receipt and post what is missing. Card "
        "accounts are where undocumented and personal spending accumulates."
    )
    evidence_required = ("card statement", "ledger postings", "receipts")
    involves_disbursed_cash = True
    credit_card = True


@register
class StaleUnreconciledItems(Rule):
    rule_id = "FOS-R010"
    version = "1"
    title = "Stale uncleared bank items"
    purpose = (
        "A cheque or deposit that has not cleared in months is rarely in transit. "
        "It is usually a duplicate entry, a voided payment never reversed, or "
        "revenue recorded that never arrived."
    )
    category = "reconciliation"
    severity = Severity.HIGH
    risk_tier_floor = RiskTier.R2
    remediation = (
        "Confirm with the bank whether the item will ever clear. Void and reverse "
        "stale cheques; investigate uncleared deposits as possible double-counted revenue."
    )
    evidence_required = ("outstanding item list", "bank confirmation")

    STALE_DAYS = 90

    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        for bank_id, result in ctx.reconciliations.items():
            bank = ctx.ledger.bank_accounts.get(bank_id)
            stale = result.stale_ledger_items(ctx.period_end, days=self.STALE_DAYS)
            if not stale:
                continue
            total = Money.zero(ctx.currency)
            builder = ctx.packet(
                f"{self.rule_id}-{bank_id}", f"Uncleared items older than {self.STALE_DAYS} days"
            )
            for item in sorted(stale, key=lambda i: i.posted_on)[:25]:
                total = total + item.amount
                builder.transaction(
                    item.txn,
                    label=f"Uncleared since {item.posted_on.isoformat()}",
                )
            for item in stale[25:]:
                total = total + item.amount
            builder.calc("uncleared items", "count", len(stale))
            builder.calc("net value", "sum(uncleared amounts)", total)
            builder.calc("age threshold", "days", self.STALE_DAYS)
            yield self.finding(
                ctx,
                suffix=bank_id,
                title=(
                    f"{len(stale)} item(s) on {bank.name if bank else bank_id} have not cleared "
                    f"in over {self.STALE_DAYS} days"
                ),
                narrative=(
                    f"{abs(total).format()} of entries have sat uncleared for more than "
                    f"{self.STALE_DAYS} days. Cash and the related expense or revenue are "
                    "both likely misstated."
                ),
                exposure=abs(total),
                packet=builder.build(),
            )
