"""Account roll-forwards: opening + movement = closing, proved not assumed.

A roll-forward is the workpaper a controller actually signs. Fixed assets,
prepaids, accrued liabilities, deferred revenue and debt all share the same
shape, so they share one implementation with different movement classifiers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable, Iterable, Sequence

from ..canonical.enums import AccountSubtype
from ..canonical.models import Ledger, Transaction, TransactionLine
from ..money import Money, msum
from .trial_balance import balance_of

__all__ = ["Movement", "RollForward", "build_rollforward", "debt_rollforward", "DebtRollForward"]


@dataclass(frozen=True)
class Movement:
    txn: Transaction
    line: TransactionLine
    category: str

    @property
    def amount(self) -> Money:
        return self.line.amount

    @property
    def on(self) -> date:
        return self.txn.txn_date


@dataclass(frozen=True)
class RollForward:
    """Opening balance, classified movements, closing balance, and the proof."""

    account_id: str
    label: str
    currency: str
    start: date
    end: date
    opening: Money
    movements: tuple[Movement, ...]
    closing: Money

    @property
    def total_movement(self) -> Money:
        return msum((m.amount for m in self.movements), self.currency)

    @property
    def difference(self) -> Money:
        """Opening + movement - closing. Anything non-zero is a data defect."""
        return self.opening + self.total_movement - self.closing

    @property
    def ties(self) -> bool:
        return self.difference.is_zero

    def by_category(self) -> dict[str, Money]:
        out: dict[str, Money] = {}
        for m in self.movements:
            out[m.category] = out.get(m.category, Money.zero(self.currency)) + m.amount
        return out

    def category_total(self, category: str) -> Money:
        return self.by_category().get(category, Money.zero(self.currency))


def default_classifier(txn: Transaction, line: TransactionLine) -> str:
    """Classify a movement using only facts already on the record."""
    if txn.reverses_txn_id:
        return "reversal"
    if txn.is_adjusting:
        return "adjustment"
    if txn.is_manual:
        return "manual entry"
    return "addition" if line.amount.minor_units > 0 else "reduction"


def build_rollforward(
    ledger: Ledger,
    account_id: str,
    start: date,
    end: date,
    *,
    label: str | None = None,
    classifier: Callable[[Transaction, TransactionLine], str] = default_classifier,
) -> RollForward:
    ledger.build_indexes()
    acct = ledger.account(account_id)
    opening = balance_of(ledger, account_id, as_of=_day_before(start))
    closing = balance_of(ledger, account_id, as_of=end)
    movements = tuple(
        Movement(txn=t, line=l, category=classifier(t, l))
        for (t, l) in sorted(
            ledger.postings(account_id), key=lambda tl: (tl[0].txn_date, tl[1].line_id)
        )
        if start <= t.txn_date <= end
    )
    return RollForward(
        account_id=account_id,
        label=label or (acct.name if acct else account_id),
        currency=ledger.currency,
        start=start,
        end=end,
        opening=opening,
        movements=movements,
        closing=closing,
    )


def _day_before(d: date) -> date:
    from datetime import timedelta

    return d - timedelta(days=1)


@dataclass(frozen=True)
class DebtRollForward:
    """Debt-specific roll-forward with the principal/interest split proved out."""

    debt_id: str
    name: str
    currency: str
    start: date
    end: date
    opening_principal: Money
    principal_repaid: Money
    new_borrowings: Money
    closing_principal: Money
    interest_expensed: Money
    payments_total: Money

    @property
    def difference(self) -> Money:
        return (
            self.opening_principal + self.new_borrowings - self.principal_repaid
            - self.closing_principal
        )

    @property
    def ties(self) -> bool:
        return self.difference.is_zero

    @property
    def implied_split_difference(self) -> Money:
        """Cash paid minus (principal + interest). Non-zero means a coding error.

        Almost every SMB file has this defect: the whole loan payment is coded to
        principal, or the whole thing to interest expense. It quietly misstates
        both the balance sheet and profit.
        """
        return self.payments_total - (self.principal_repaid + self.interest_expensed)


def debt_rollforward(ledger: Ledger, debt_id: str, start: date, end: date) -> DebtRollForward:
    debt = ledger.debts[debt_id]
    cur = ledger.currency
    principal_rf = build_rollforward(ledger, debt.liability_account_id, start, end)
    # Liability accounts carry credit balances; flip into positive principal.
    opening_principal = -principal_rf.opening
    closing_principal = -principal_rf.closing
    repaid = msum(
        (m.amount for m in principal_rf.movements if m.amount.minor_units > 0), cur
    )
    borrowed = msum(
        (-m.amount for m in principal_rf.movements if m.amount.minor_units < 0), cur
    )
    interest = Money.zero(cur)
    if debt.interest_account_id:
        interest = msum(
            (
                l.amount
                for (t, l) in ledger.postings(debt.interest_account_id)
                if start <= t.txn_date <= end
            ),
            cur,
        )
    # Cash actually paid on the debt = principal lines plus interest lines that
    # appear in the same transactions.
    payment_txns = {
        m.txn.txn_id for m in principal_rf.movements if m.amount.minor_units > 0
    }
    payments = Money.zero(cur)
    for txn in ledger.transactions:
        if txn.txn_id not in payment_txns:
            continue
        for line in txn.lines:
            acct = ledger.account(line.account_id)
            if acct and acct.subtype in (AccountSubtype.BANK, AccountSubtype.CREDIT_CARD):
                payments = payments - line.amount  # cash out is a credit
    return DebtRollForward(
        debt_id=debt_id,
        name=debt.name,
        currency=cur,
        start=start,
        end=end,
        opening_principal=opening_principal,
        principal_repaid=repaid,
        new_borrowings=borrowed,
        closing_principal=closing_principal,
        interest_expensed=interest,
        payments_total=payments,
    )
