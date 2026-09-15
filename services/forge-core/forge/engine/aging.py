"""AR / AP aging, DSO, DPO and counterparty concentration.

Working capital is where an SMB actually feels finance, so these numbers have to
be right to the cent and reproducible on any date. Outstanding balance is always
derived as ``original - applications on or before the as-of date``, never stored,
so a back-dated payment re-ages history correctly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal, Sequence

from ..canonical.models import Ledger, OpenItem
from ..money import Money, msum

__all__ = [
    "DEFAULT_BUCKETS",
    "AgedItem",
    "AgingBucket",
    "AgingReport",
    "build_aging",
    "days_sales_outstanding",
    "concentration",
]

# (label, inclusive lower bound of days past due, exclusive upper bound or None)
DEFAULT_BUCKETS: tuple[tuple[str, int, int | None], ...] = (
    ("Current", -10_000, 1),
    ("1-30", 1, 31),
    ("31-60", 31, 61),
    ("61-90", 61, 91),
    ("90+", 91, None),
)


@dataclass(frozen=True)
class AgedItem:
    item: OpenItem
    outstanding: Money
    days_past_due: int
    bucket: str

    @property
    def party_id(self) -> str:
        return self.item.party_id


@dataclass(frozen=True)
class AgingBucket:
    label: str
    items: tuple[AgedItem, ...]
    total: Money

    @property
    def count(self) -> int:
        return len(self.items)


@dataclass(frozen=True)
class AgingReport:
    entity_id: str
    kind: Literal["receivable", "payable"]
    as_of: date
    currency: str
    buckets: tuple[AgingBucket, ...]
    items: tuple[AgedItem, ...]

    @property
    def total(self) -> Money:
        return msum((b.total for b in self.buckets), self.currency)

    def bucket(self, label: str) -> AgingBucket | None:
        for b in self.buckets:
            if b.label == label:
                return b
        return None

    def past_due(self, min_days: int = 1) -> tuple[AgedItem, ...]:
        return tuple(i for i in self.items if i.days_past_due >= min_days)

    def past_due_total(self, min_days: int = 1) -> Money:
        return msum((i.outstanding for i in self.past_due(min_days)), self.currency)

    def by_party(self) -> dict[str, Money]:
        out: dict[str, Money] = {}
        for i in self.items:
            out[i.party_id] = out.get(i.party_id, Money.zero(self.currency)) + i.outstanding
        return out

    @property
    def percent_past_due(self) -> Decimal | None:
        return self.past_due_total().ratio_to(self.total)


def _bucket_for(days: int, buckets: Sequence[tuple[str, int, int | None]]) -> str:
    for label, lo, hi in buckets:
        if days >= lo and (hi is None or days < hi):
            return label
    return buckets[-1][0]


def build_aging(
    ledger: Ledger,
    as_of: date,
    kind: Literal["receivable", "payable"] = "receivable",
    *,
    buckets: Sequence[tuple[str, int, int | None]] = DEFAULT_BUCKETS,
    include_zero: bool = False,
) -> AgingReport:
    """Age open items as at ``as_of``.

    Credit balances (over-applications, unapplied customer credits) are kept
    rather than clamped to zero: a negative AR balance is itself a control
    finding, and hiding it would defeat the point.
    """
    ledger.build_indexes()
    cur = ledger.currency
    aged: list[AgedItem] = []
    for item in ledger.open_items:
        if item.kind != kind:
            continue
        if item.issued_on > as_of:
            continue
        applied = msum(
            (
                a.amount
                for a in ledger.applications_for(item.open_item_id)
                if a.applied_on <= as_of
            ),
            cur,
        )
        outstanding = item.original_amount - applied
        if outstanding.is_zero and not include_zero:
            continue
        dpd = item.days_past_due(as_of)
        aged.append(
            AgedItem(
                item=item,
                outstanding=outstanding,
                days_past_due=dpd,
                bucket=_bucket_for(dpd, buckets),
            )
        )

    aged.sort(key=lambda a: (-a.days_past_due, -a.outstanding.minor_units))
    bucket_objs = []
    for label, _lo, _hi in buckets:
        members = tuple(a for a in aged if a.bucket == label)
        bucket_objs.append(
            AgingBucket(
                label=label,
                items=members,
                total=msum((m.outstanding for m in members), cur),
            )
        )

    return AgingReport(
        entity_id=ledger.entity.entity_id,
        kind=kind,
        as_of=as_of,
        currency=cur,
        buckets=tuple(bucket_objs),
        items=tuple(aged),
    )


def days_sales_outstanding(
    receivables: Money, credit_sales: Money, days_in_period: int
) -> Decimal | None:
    """Classic DSO: AR / credit sales * days. ``None`` when there were no sales."""
    ratio = receivables.ratio_to(credit_sales)
    if ratio is None:
        return None
    return ratio * Decimal(days_in_period)


def concentration(balances: dict[str, Money], currency: str = "CAD") -> list[tuple[str, Money, Decimal]]:
    """Rank counterparties by share of total, largest first.

    Customer concentration is one of the few SMB risks that is both easy to
    measure and routinely ignored until the largest customer leaves.
    """
    total = msum(balances.values(), currency)
    rows: list[tuple[str, Money, Decimal]] = []
    for party_id, amount in balances.items():
        share = amount.ratio_to(total)
        rows.append((party_id, amount, share if share is not None else Decimal(0)))
    rows.sort(key=lambda r: -r[1].minor_units)
    return rows
