"""Trial balance construction and integrity tests.

Everything downstream — statements, controls, close schedules, forecasts — is
built on this module, so it is written to be boring, exact and fast. All
balances are carried in *debit-positive* convention: a positive balance is a net
debit, a negative balance is a net credit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Iterable, Sequence

from ..canonical.enums import AccountSubtype, AccountType, Side
from ..canonical.models import Account, Ledger, Transaction
from ..money import Money, msum

__all__ = ["AccountBalance", "TrialBalance", "build_trial_balance", "balance_of"]


@dataclass(frozen=True)
class AccountBalance:
    """One line of a trial balance, with activity split for roll-forward use."""

    account: Account
    opening: Money
    debits: Money
    credits: Money
    closing: Money
    line_count: int = 0

    @property
    def movement(self) -> Money:
        return self.debits - self.credits

    @property
    def presentation_balance(self) -> Money:
        """Closing balance in statement-reading direction, by account *type*.

        Assets and expenses read debit-positive; liabilities, equity and revenue
        read credit-positive. The flip deliberately keys off ``account.type``
        and not ``account.normal_balance``: a contra account such as
        accumulated depreciation must stay negative inside the asset section so
        that it reduces total assets. Flipping contras here would make each
        section look tidy and quietly break the accounting equation, which is
        exactly the class of bug this system exists to catch.
        """
        if self.account.type.normal_balance is Side.CREDIT:
            return -self.closing
        return self.closing

    @property
    def presentation_movement(self) -> Money:
        """Period activity only, in statement-reading direction.

        The income statement must use this rather than
        :attr:`presentation_balance`. Closing balance includes the opening
        balance, so a P&L built from closing balances silently reports
        inception-to-date results whenever the period does not start at
        inception.
        """
        if self.account.type.normal_balance is Side.CREDIT:
            return -self.movement
        return self.movement

    @property
    def is_abnormal(self) -> bool:
        """True when the account carries a balance on its unnatural side.

        Uses the contra-aware normal balance, so a debit balance in accumulated
        depreciation or a credit balance in owner draws is flagged.
        """
        if self.account.normal_balance is Side.CREDIT:
            return self.closing.minor_units > 0
        return self.closing.minor_units < 0


@dataclass
class TrialBalance:
    """A period trial balance plus the integrity facts a controller asks for."""

    entity_id: str
    currency: str
    start: date
    end: date
    rows: tuple[AccountBalance, ...] = ()
    transaction_count: int = 0
    unbalanced_transactions: tuple[Transaction, ...] = ()

    # ---- integrity ----------------------------------------------------

    @property
    def total_debits(self) -> Money:
        return msum((r.debits for r in self.rows), self.currency)

    @property
    def total_credits(self) -> Money:
        return msum((r.credits for r in self.rows), self.currency)

    @property
    def activity_imbalance(self) -> Money:
        return self.total_debits - self.total_credits

    @property
    def closing_imbalance(self) -> Money:
        """Sum of all closing balances. Must be zero in a double-entry book."""
        return msum((r.closing for r in self.rows), self.currency)

    @property
    def opening_imbalance(self) -> Money:
        return msum((r.opening for r in self.rows), self.currency)

    @property
    def is_in_balance(self) -> bool:
        return self.closing_imbalance.is_zero and self.activity_imbalance.is_zero

    # ---- lookup -------------------------------------------------------

    def row(self, account_id: str) -> AccountBalance | None:
        for r in self.rows:
            if r.account.account_id == account_id:
                return r
        return None

    def rows_of(
        self,
        *,
        type: AccountType | None = None,
        subtype: AccountSubtype | None = None,
        subtypes: Sequence[AccountSubtype] | None = None,
    ) -> list[AccountBalance]:
        want_subs = set(subtypes or ())
        if subtype is not None:
            want_subs.add(subtype)
        out = []
        for r in self.rows:
            if type is not None and r.account.type is not type:
                continue
            if want_subs and r.account.subtype not in want_subs:
                continue
            out.append(r)
        return out

    def total_of(
        self,
        *,
        type: AccountType | None = None,
        subtype: AccountSubtype | None = None,
        subtypes: Sequence[AccountSubtype] | None = None,
        presentation: bool = True,
    ) -> Money:
        rows = self.rows_of(type=type, subtype=subtype, subtypes=subtypes)
        vals = (r.presentation_balance if presentation else r.closing for r in rows)
        return msum(vals, self.currency)

    def nonzero_rows(self) -> list[AccountBalance]:
        return [r for r in self.rows if not r.closing.is_zero or not r.movement.is_zero]


def balance_of(
    ledger: Ledger,
    account_id: str,
    *,
    as_of: date | None = None,
    since: date | None = None,
) -> Money:
    """Net debit-positive balance of one account, optionally date-bounded."""
    total = Money.zero(ledger.currency)
    for txn, line in ledger.postings(account_id):
        if as_of is not None and txn.txn_date > as_of:
            continue
        if since is not None and txn.txn_date < since:
            continue
        total = total + line.amount
    return total


def build_trial_balance(
    ledger: Ledger,
    start: date,
    end: date,
    *,
    accounts: Iterable[Account] | None = None,
) -> TrialBalance:
    """Build a trial balance for ``[start, end]`` with opening balances.

    Opening balance is every posting strictly before ``start``; activity is every
    posting inside the window. Transactions outside the window never contribute
    to activity, which is what makes period tie-outs reproducible.
    """
    ledger.build_indexes()
    currency = ledger.currency
    acct_list = list(accounts) if accounts is not None else list(ledger.accounts.values())
    acct_list.sort(key=lambda a: (a.number, a.account_id))

    rows: list[AccountBalance] = []
    for acct in acct_list:
        opening = Money.zero(currency)
        debits = Money.zero(currency)
        credits = Money.zero(currency)
        count = 0
        for txn, line in ledger.postings(acct.account_id):
            if txn.txn_date < start:
                opening = opening + line.amount
            elif txn.txn_date <= end:
                debits = debits + line.debit
                credits = credits + line.credit
                count += 1
        closing = opening + debits - credits
        rows.append(
            AccountBalance(
                account=acct,
                opening=opening,
                debits=debits,
                credits=credits,
                closing=closing,
                line_count=count,
            )
        )

    in_window = ledger.transactions_between(start, end)
    unbalanced = tuple(t for t in in_window if not t.is_balanced)

    return TrialBalance(
        entity_id=ledger.entity.entity_id,
        currency=currency,
        start=start,
        end=end,
        rows=tuple(rows),
        transaction_count=len(in_window),
        unbalanced_transactions=unbalanced,
    )
