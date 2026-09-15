"""Deterministic bank and credit-card reconciliation.

Reconciliation is the control that catches missing revenue, duplicated expense,
and fraud. It is also the task SMB bookkeepers most often fake by plugging a
difference to a suspense account, so ForgeOS treats an unexplained difference as
a hard failure rather than a rounding note.

The matcher runs in passes, cheapest and most certain first. Each match records
*why* it matched so a reviewer can reproduce it:

1. exact amount + same date + reference/cheque number agreement;
2. exact amount within a date tolerance window;
3. one statement deposit against a group of ledger receipts summing to it
   (the classic undeposited-funds batch), bounded to keep it tractable.

Anything unmatched on either side is surfaced, never silently absorbed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from itertools import combinations
from typing import Iterable, Sequence

from ..canonical.models import Ledger, Transaction, TransactionLine
from ..money import Money, msum

__all__ = [
    "StatementLine",
    "BankStatement",
    "LedgerItem",
    "Match",
    "ReconciliationResult",
    "reconcile",
]


@dataclass(frozen=True)
class StatementLine:
    """One line from a bank or credit-card statement.

    ``amount`` uses the ledger's debit-positive convention from the *entity's*
    point of view: money arriving in the bank is a positive (debit) amount.
    """

    statement_line_id: str
    posted_on: date
    amount: Money
    description: str
    reference: str | None = None


@dataclass(frozen=True)
class BankStatement:
    bank_account_id: str
    start: date
    end: date
    opening_balance: Money
    closing_balance: Money
    lines: tuple[StatementLine, ...] = ()

    @property
    def net_movement(self) -> Money:
        return msum((ln.amount for ln in self.lines), self.opening_balance.currency)

    @property
    def internal_difference(self) -> Money:
        """Opening + movement - closing. Non-zero means a bad import."""
        return self.opening_balance + self.net_movement - self.closing_balance


@dataclass(frozen=True)
class LedgerItem:
    txn: Transaction
    line: TransactionLine

    @property
    def amount(self) -> Money:
        return self.line.amount

    @property
    def posted_on(self) -> date:
        return self.txn.txn_date

    @property
    def key(self) -> str:
        return self.line.line_id


@dataclass(frozen=True)
class Match:
    statement_line_ids: tuple[str, ...]
    ledger_line_ids: tuple[str, ...]
    amount: Money
    method: str
    date_gap_days: int = 0

    @property
    def is_group(self) -> bool:
        return len(self.statement_line_ids) > 1 or len(self.ledger_line_ids) > 1


@dataclass
class ReconciliationResult:
    bank_account_id: str
    account_id: str
    start: date
    end: date
    currency: str
    statement_closing: Money
    ledger_closing: Money
    matches: tuple[Match, ...] = ()
    unmatched_statement: tuple[StatementLine, ...] = ()
    unmatched_ledger: tuple[LedgerItem, ...] = ()
    statement_import_difference: Money = field(default_factory=lambda: Money.zero())

    @property
    def difference(self) -> Money:
        """Statement closing minus ledger closing.

        A non-zero value is normal: cheques written near period end have not
        cleared. What matters is whether the difference is *fully explained* by
        identified outstanding items, which is what
        :attr:`unexplained_difference` measures.
        """
        return self.statement_closing - self.ledger_closing

    @property
    def outstanding_items(self) -> tuple[LedgerItem, ...]:
        """Ledger entries not yet on the statement: outstanding cheques and
        deposits in transit."""
        return self.unmatched_ledger

    @property
    def bank_only_items(self) -> tuple[StatementLine, ...]:
        """Statement entries with no ledger record.

        These are never timing differences. They are bank fees, interest,
        NSF returns, or payments made outside the books, and every one of them
        is a missing journal entry.
        """
        return self.unmatched_statement

    @property
    def unexplained_difference(self) -> Money:
        """The reconciliation proof, in the form a controller signs.

        ``statement closing - outstanding items + bank-only items - ledger
        closing``. Zero means every dollar of difference is accounted for by an
        identified item. Anything else is a genuine break and must not be
        plugged.
        """
        return (
            self.difference
            + self.unmatched_ledger_total
            - self.unmatched_statement_total
        )

    @property
    def is_reconciled(self) -> bool:
        """True when the difference is fully explained and nothing is bank-only."""
        return self.unexplained_difference.is_zero and not self.unmatched_statement

    @property
    def unmatched_statement_total(self) -> Money:
        return msum((s.amount for s in self.unmatched_statement), self.currency)

    @property
    def unmatched_ledger_total(self) -> Money:
        return msum((l.amount for l in self.unmatched_ledger), self.currency)

    @property
    def match_rate(self) -> float:
        total = len(self.matches) * 2 + len(self.unmatched_statement) + len(self.unmatched_ledger)
        if total == 0:
            return 1.0
        return (len(self.matches) * 2) / total

    def stale_ledger_items(self, as_of: date, days: int = 90) -> tuple[LedgerItem, ...]:
        """Ledger items that have sat unmatched longer than ``days``.

        Long-uncleared cheques and deposits are where real problems hide: a
        cheque that never cleared is usually an expense recorded twice, and a
        deposit that never cleared is usually revenue recorded twice.
        """
        cutoff = as_of - timedelta(days=days)
        return tuple(i for i in self.unmatched_ledger if i.posted_on <= cutoff)


def _normalise_ref(value: str | None) -> str:
    if not value:
        return ""
    return "".join(ch for ch in value if ch.isalnum()).upper().lstrip("0")


def reconcile(
    ledger: Ledger,
    statement: BankStatement,
    *,
    account_id: str | None = None,
    date_tolerance_days: int = 5,
    max_group_size: int = 4,
) -> ReconciliationResult:
    """Match a statement against the ledger postings for one bank account.

    ``max_group_size`` bounds the combinatorial pass. Four is deliberate: it
    covers realistic deposit batches without letting a pathological account turn
    reconciliation into an exponential search.
    """
    ledger.build_indexes()
    bank = ledger.bank_accounts.get(statement.bank_account_id)
    acct_id = account_id or (bank.account_id if bank else statement.bank_account_id)
    cur = statement.opening_balance.currency

    ledger_items = [
        LedgerItem(txn=t, line=l)
        for (t, l) in ledger.postings(acct_id)
        if statement.start <= t.txn_date <= statement.end
    ]
    ledger_closing = Money.zero(cur)
    for t, l in ledger.postings(acct_id):
        if t.txn_date <= statement.end:
            ledger_closing = ledger_closing + l.amount

    remaining_stmt: list[StatementLine] = list(statement.lines)
    remaining_ldg: list[LedgerItem] = list(ledger_items)
    matches: list[Match] = []

    def take(stmt: StatementLine, items: Sequence[LedgerItem], method: str, gap: int) -> None:
        matches.append(
            Match(
                statement_line_ids=(stmt.statement_line_id,),
                ledger_line_ids=tuple(i.key for i in items),
                amount=stmt.amount,
                method=method,
                date_gap_days=gap,
            )
        )
        remaining_stmt.remove(stmt)
        for i in items:
            remaining_ldg.remove(i)

    # Pass 1 - amount + date + reference agreement.
    for stmt in list(remaining_stmt):
        ref = _normalise_ref(stmt.reference)
        if not ref:
            continue
        for item in list(remaining_ldg):
            if item.amount != stmt.amount:
                continue
            if _normalise_ref(item.txn.doc_number) != ref:
                continue
            if abs((item.posted_on - stmt.posted_on).days) > date_tolerance_days:
                continue
            take(stmt, [item], "amount+reference", abs((item.posted_on - stmt.posted_on).days))
            break

    # Pass 2 - exact amount, same date.
    for stmt in list(remaining_stmt):
        for item in list(remaining_ldg):
            if item.amount == stmt.amount and item.posted_on == stmt.posted_on:
                take(stmt, [item], "amount+date", 0)
                break

    # Pass 3 - exact amount within the tolerance window, nearest date wins.
    for stmt in list(remaining_stmt):
        candidates = [
            i
            for i in remaining_ldg
            if i.amount == stmt.amount
            and abs((i.posted_on - stmt.posted_on).days) <= date_tolerance_days
        ]
        if candidates:
            best = min(candidates, key=lambda i: abs((i.posted_on - stmt.posted_on).days))
            take(stmt, [best], "amount+window", abs((best.posted_on - stmt.posted_on).days))

    # Pass 4 - one statement line against a small group of ledger items.
    for stmt in list(remaining_stmt):
        window = [
            i
            for i in remaining_ldg
            if abs((i.posted_on - stmt.posted_on).days) <= date_tolerance_days
            and (i.amount.minor_units > 0) == (stmt.amount.minor_units > 0)
        ]
        if len(window) < 2 or len(window) > 20:
            continue
        found = None
        for size in range(2, min(max_group_size, len(window)) + 1):
            for combo in combinations(window, size):
                if msum((c.amount for c in combo), cur) == stmt.amount:
                    found = combo
                    break
            if found:
                break
        if found:
            gap = max(abs((c.posted_on - stmt.posted_on).days) for c in found)
            take(stmt, list(found), f"grouped[{len(found)}]", gap)

    return ReconciliationResult(
        bank_account_id=statement.bank_account_id,
        account_id=acct_id,
        start=statement.start,
        end=statement.end,
        currency=cur,
        statement_closing=statement.closing_balance,
        ledger_closing=ledger_closing,
        matches=tuple(matches),
        unmatched_statement=tuple(remaining_stmt),
        unmatched_ledger=tuple(remaining_ldg),
        statement_import_difference=statement.internal_difference,
    )
