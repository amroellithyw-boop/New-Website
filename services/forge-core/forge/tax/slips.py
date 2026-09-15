"""Information-slip obligations derived from vendor payments.

Which subcontractors need a T5018 or T4A this year, with the amounts, from the
ledger rather than from memory. The $500 threshold is the statutory one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from ..canonical.enums import AccountSubtype, TxnType
from ..money import Money
from ..rules.base import RuleContext

__all__ = ["SlipObligation", "slip_obligations"]

THRESHOLD = Money.from_decimal("500.00")


@dataclass(frozen=True)
class SlipObligation:
    party_id: str
    party_name: str
    slip: str  # "T5018" | "T4A"
    amount_paid: Money
    year: int


def slip_obligations(ctx: RuleContext, *, year: int, construction: bool) -> list[SlipObligation]:
    start, end = date(year, 1, 1), date(year, 12, 31)
    by_party: dict[str, Money] = {}
    for acct in ctx.ledger.accounts_of(subtype=AccountSubtype.SUBCONTRACTOR):
        for txn, line in ctx.ledger.postings(acct.account_id):
            if start <= txn.txn_date <= end and txn.type in (TxnType.BILL, TxnType.EXPENSE, TxnType.CHEQUE):
                pid = line.party_id or txn.party_id
                if pid:
                    by_party[pid] = by_party.get(pid, Money.zero(ctx.currency)) + line.amount
    out = []
    for pid, amt in by_party.items():
        if amt < THRESHOLD:
            continue
        party = ctx.ledger.parties.get(pid)
        out.append(SlipObligation(pid, party.name if party else pid, "T5018" if construction else "T4A", amt, year))
    return sorted(out, key=lambda s: -s.amount_paid.minor_units)
