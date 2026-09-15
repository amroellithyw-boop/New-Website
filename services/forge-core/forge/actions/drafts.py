"""Draft actions: autonomy level A1.

A finding that ends in a paragraph is advice. A finding that ends in a draft
journal entry, a collection letter with the invoice attached, or a remittance
reminder with the amount is work someone can approve in one click. Drafts are
produced by code from the evidence wherever the arithmetic is determinable,
and are marked as estimates where it is not. Nothing is posted, sent or paid.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from ..canonical.models import Transaction
from ..money import Money
from ..workitems.state import WorkItem

__all__ = ["DraftAction", "draft_for"]


@dataclass(frozen=True)
class DraftAction:
    kind: str  # draft_journal_entry | draft_email | investigate | request_document | escalate_to_owner | remit_or_file | recover_payment
    title: str
    body: str = ""
    lines: tuple[dict[str, Any], ...] = ()
    is_estimate: bool = False
    authorised_now: bool = False
    """Whether the work item's approved scope and autonomy level permit this
    action to proceed without another approval."""
    reason_not_authorised: str = ""

    @property
    def balanced(self) -> bool:
        if not self.lines:
            return True
        d = sum(Decimal(ln["amount"]) for ln in self.lines if ln["side"] == "debit")
        c = sum(Decimal(ln["amount"]) for ln in self.lines if ln["side"] == "credit")
        return d == c

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "title": self.title, "body": self.body, "lines": list(self.lines),
                "is_estimate": self.is_estimate, "authorised_now": self.authorised_now,
                "reason_not_authorised": self.reason_not_authorised, "balanced": self.balanced}


def _line(account_id: str, name: str, side: str, amount: Money, memo: str = "") -> dict[str, Any]:
    return {"account_number": account_id, "account_name": name, "side": side, "amount": str(amount.to_decimal()), "memo": memo}


def _txn_from_packet(item: WorkItem, ledger) -> Transaction | None:
    for ref in item.packet.refs:
        if ref.kind == "transaction":
            for t in ledger.transactions:
                if t.txn_id == ref.ref_id:
                    return t
    return None


def draft_for(item: WorkItem, ledger, *, today: date | None = None) -> DraftAction:
    """Produce the most specific draft the evidence supports for this work item."""
    f = item.finding
    rule = f.rule_id
    today = today or item.period_end

    def gate(kind: str, draft: DraftAction) -> DraftAction:
        ok, why = item.may_execute(kind)
        return DraftAction(draft.kind, draft.title, draft.body, draft.lines, draft.is_estimate, ok, "" if ok else why)

    if rule == "FOS-R044":  # unreversed accrual: reverse it
        txn = _txn_from_packet(item, ledger)
        if txn:
            lines = tuple(_line(ln.account_id, (ledger.account(ln.account_id).name if ledger.account(ln.account_id) else ln.account_id),
                                "credit" if ln.amount.minor_units > 0 else "debit", abs(ln.amount), f"Reverse {txn.doc_number or txn.txn_id}")
                          for ln in txn.lines)
            return gate("draft_journal_entry", DraftAction("draft_journal_entry", f"Reverse accrual {txn.doc_number or txn.txn_id}",
                                                           f"Reversal dated {item.period_start.isoformat()} of the accrual that was never reversed.", lines))

    if rule == "FOS-R042":  # missing depreciation: post the expected charge
        expected = next((c for c in f.packet.calculations if c.name == "expected charge"), None)
        if expected and isinstance(expected.result, Money):
            dep = next((a for a in ledger.accounts.values() if a.subtype.value == "depreciation_expense"), None)
            acc = next((a for a in ledger.accounts.values() if a.subtype.value == "accumulated_depreciation"), None)
            if dep and acc:
                lines = (_line(dep.account_id, dep.name, "debit", expected.result, "Monthly depreciation"),
                         _line(acc.account_id, acc.name, "credit", expected.result, "Monthly depreciation"))
                return gate("draft_journal_entry", DraftAction("draft_journal_entry", "Post the period's depreciation",
                                                               "Estimated from the gross asset base at the policy rate; replace with the register figure if one exists.", lines, is_estimate=True))

    if rule == "FOS-R043":  # prepaid not amortised
        closing = next((c for c in f.packet.calculations if c.name == "closing balance"), None)
        if closing and isinstance(closing.result, Money):
            monthly = closing.result.scale(Decimal(1) / Decimal(12))
            prepaid_id = f.finding_id.partition(":")[2]
            acct = ledger.account(prepaid_id)
            exp = next((a for a in ledger.accounts.values() if "insurance" in a.name.lower() and a.type.value == "expense"), None)
            if acct and exp:
                lines = (_line(exp.account_id, exp.name, "debit", monthly, "Prepaid amortisation"),
                         _line(acct.account_id, acct.name, "credit", monthly, "Prepaid amortisation"))
                return gate("draft_journal_entry", DraftAction("draft_journal_entry", f"Amortise {acct.name}",
                                                               "One twelfth of the balance; confirm the policy term before posting.", lines, is_estimate=True))

    if rule == "FOS-R027":  # overdue invoice: collection letter
        ref = next((r for r in f.packet.refs if r.kind == "open_item"), None)
        customer = f.packet.context.get("customer", "the customer")
        body = (f"Hi,\n\nA quick note on invoice {ref.label if ref else ''} for {ref.amount.format() if ref and ref.amount else f.exposure.format()}, "
                f"which was due on {ref.on.isoformat() if ref and ref.on else 'its due date'} and shows as outstanding on our side.\n\n"
                f"Could you confirm it is approved for payment, or let me know if anything about it needs sorting out? "
                f"Happy to resend a copy.\n\nThanks,\n")
        return gate("draft_email", DraftAction("draft_email", f"Collection note to {customer}", body))

    if rule == "FOS-R038":
        return gate("remit_or_file", DraftAction("remit_or_file", f"Remit {f.exposure.format()} of source deductions",
                                                 "Overdue trust funds. Remit today and confirm the assigned remitter frequency on the CRA account. This action requires human authorisation."))

    if rule == "FOS-R006":
        return gate("recover_payment", DraftAction("recover_payment", f"Recover duplicate payment of {f.exposure.format()}",
                                                   "Void the duplicate bill; if paid, request a vendor credit and apply it to the next invoice. Attach both documents."))

    if rule == "FOS-R032":
        return gate("escalate_to_owner", DraftAction("escalate_to_owner", f"Owner review: {f.exposure.format()} paid with no bill",
                                                     "A disbursement with no bill and no approval. Obtain the invoice and the approval; if neither exists, treat as suspected unauthorised payment."))

    if rule == "FOS-R028":
        return gate("investigate", DraftAction("investigate", f"Apply unapplied receipt of {f.exposure.format()}",
                                               "Match the receipt to an open invoice; if none exists, determine whether the work was billed."))

    if rule in ("FOS-R019", "FOS-R040"):
        return gate("request_document", DraftAction("request_document", "Obtain the purchase invoice",
                                                    "Needed to decide capital versus expense and to set the CCA class and in-service date."))

    return gate("investigate", DraftAction("investigate", f.title, f.remediation))
