"""Deterministic anomaly features.

Controls need numbers to threshold against, and those numbers must be computed
the same way every run. Nothing here is statistical guesswork dressed up as AI:
each feature is a plain, reproducible calculation that a reviewer can redo in a
spreadsheet.
"""

from __future__ import annotations

import hashlib
import re
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from ..canonical.enums import Side
from ..canonical.models import Ledger, Transaction
from ..money import Money

__all__ = [
    "MonthKey",
    "month_key",
    "month_bounds",
    "monthly_series",
    "VarianceRow",
    "period_variance",
    "RecurringProfile",
    "recurring_profiles",
    "duplicate_fingerprint",
    "DuplicateCluster",
    "find_duplicate_clusters",
    "is_round_amount",
    "median_money",
    "robust_zscore",
]

MonthKey = str  # "YYYY-MM"


def month_key(d: date) -> MonthKey:
    return f"{d.year:04d}-{d.month:02d}"


def month_bounds(key: MonthKey) -> tuple[date, date]:
    year, month = (int(p) for p in key.split("-"))
    start = date(year, month, 1)
    end = date(year + (month == 12), (month % 12) + 1, 1) - timedelta(days=1)
    return start, end


def months_between(start: date, end: date) -> list[MonthKey]:
    out: list[MonthKey] = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + (m == 12), (m % 12) + 1)
    return out


def monthly_series(
    ledger: Ledger, account_id: str, start: date, end: date
) -> dict[MonthKey, Money]:
    """Net debit-positive movement per calendar month, zero-filled."""
    cur = ledger.currency
    series: dict[MonthKey, Money] = {k: Money.zero(cur) for k in months_between(start, end)}
    for txn, line in ledger.postings(account_id):
        if not (start <= txn.txn_date <= end):
            continue
        k = month_key(txn.txn_date)
        if k in series:
            series[k] = series[k] + line.amount
    return series


@dataclass(frozen=True)
class VarianceRow:
    """Movement in two windows, already in statement-reading direction.

    ``current`` and ``comparative`` are credit-positive for revenue, equity and
    liabilities. Comparing raw debit-positive sums would report a revenue
    *increase* as a decrease, because more revenue means a larger credit.
    """

    account_id: str
    account_name: str
    current: Money
    comparative: Money
    label: str

    @property
    def absolute_change(self) -> Money:
        return self.current - self.comparative

    @property
    def percent_change(self) -> Decimal | None:
        if self.comparative.minor_units == 0:
            return None
        return Decimal(self.absolute_change.minor_units) / Decimal(
            abs(self.comparative.minor_units)
        )

    @property
    def is_new_activity(self) -> bool:
        return self.comparative.is_zero and not self.current.is_zero

    @property
    def is_lost_activity(self) -> bool:
        return not self.comparative.is_zero and self.current.is_zero


def period_variance(
    ledger: Ledger,
    current_start: date,
    current_end: date,
    comparative_start: date,
    comparative_end: date,
    *,
    label: str = "prior period",
    account_ids: Sequence[str] | None = None,
) -> list[VarianceRow]:
    """Movement in one window against movement in another, per account."""
    ledger.build_indexes()
    cur = ledger.currency
    ids = list(account_ids) if account_ids is not None else list(ledger.accounts.keys())
    rows: list[VarianceRow] = []
    for account_id in ids:
        acct = ledger.account(account_id)
        if acct is None:
            continue
        current = Money.zero(cur)
        comparative = Money.zero(cur)
        for txn, line in ledger.postings(account_id):
            if current_start <= txn.txn_date <= current_end:
                current = current + line.amount
            elif comparative_start <= txn.txn_date <= comparative_end:
                comparative = comparative + line.amount
        if acct.type.normal_balance is Side.CREDIT:
            current, comparative = -current, -comparative
        if current.is_zero and comparative.is_zero:
            continue
        rows.append(
            VarianceRow(
                account_id=account_id,
                account_name=acct.name,
                current=current,
                comparative=comparative,
                label=label,
            )
        )
    rows.sort(key=lambda r: -abs(r.absolute_change.minor_units))
    return rows


@dataclass(frozen=True)
class RecurringProfile:
    """A repeating transaction pattern learned from history, not from a prompt."""

    key: str
    account_id: str
    party_id: str | None
    occurrences: int
    months_seen: tuple[MonthKey, ...]
    median_amount: Money
    amounts: tuple[Money, ...]

    @property
    def is_monthly(self) -> bool:
        """True when the pattern appears in most months of its own span."""
        if len(self.months_seen) < 3:
            return False
        first, last = self.months_seen[0], self.months_seen[-1]
        expected = len(months_between(month_bounds(first)[0], month_bounds(last)[1]))
        return expected > 0 and len(self.months_seen) / expected >= 0.75

    @property
    def consecutive_tail(self) -> int:
        """How many months in a row the pattern ran up to its last appearance.

        A snow-removal contract that bills every winter is *monthly* across its
        own span but is not running right now. Requiring a consecutive run before
        reporting a gap is what stops a seasonal business being told every July
        that its snow revenue is missing.
        """
        if not self.months_seen:
            return 0
        run = 1
        for earlier, later in zip(
            self.months_seen[-2::-1], self.months_seen[::-1], strict=False
        ):
            # Two months span exactly two entries when they are adjacent.
            gap = len(months_between(month_bounds(earlier)[0], month_bounds(later)[1]))
            if gap == 2:
                run += 1
            else:
                break
        return run

    @property
    def dispersion(self) -> Decimal | None:
        """Median absolute deviation over the median: how tight the pattern is.

        Rent and insurance sit near zero. Materials purchases from the same
        supplier do not, and treating them as a fixed recurring amount is the
        main way a deviation control turns into noise.
        """
        if self.median_amount.minor_units == 0 or len(self.amounts) < 3:
            return None
        deviations = [abs(a.minor_units - self.median_amount.minor_units) for a in self.amounts]
        mad = statistics.median(deviations)
        return Decimal(mad) / Decimal(abs(self.median_amount.minor_units))

    @property
    def is_fixed_amount(self) -> bool:
        """True when the amount is stable enough that a change is meaningful."""
        d = self.dispersion
        return d is not None and d <= Decimal("0.15")

    def deviation(self, amount: Money) -> Decimal | None:
        """How far an amount sits from the pattern's median, as a ratio."""
        if self.median_amount.minor_units == 0:
            return None
        return Decimal(abs((amount - self.median_amount).minor_units)) / Decimal(
            abs(self.median_amount.minor_units)
        )


def median_money(values: Sequence[Money], currency: str = "CAD") -> Money:
    if not values:
        return Money.zero(currency)
    units = sorted(v.minor_units for v in values)
    mid = len(units) // 2
    if len(units) % 2:
        return Money(units[mid], values[0].currency)
    return Money((units[mid - 1] + units[mid]) // 2, values[0].currency)


def robust_zscore(value: Money, population: Sequence[Money]) -> Decimal | None:
    """Median-absolute-deviation z-score. Resistant to the outlier being tested.

    A plain standard deviation is worthless here because the anomaly itself
    inflates the denominator and hides the very thing being looked for.
    """
    if len(population) < 4:
        return None
    med = median_money(population, value.currency)
    deviations = [abs(p.minor_units - med.minor_units) for p in population]
    mad = statistics.median(deviations)
    if mad == 0:
        return None
    # 1.4826 scales MAD to a normal-equivalent sigma.
    scaled = Decimal(mad) * Decimal("1.4826")
    return Decimal(value.minor_units - med.minor_units) / scaled


def recurring_profiles(
    ledger: Ledger, start: date, end: date, *, min_occurrences: int = 3
) -> dict[str, RecurringProfile]:
    """Group postings into (account, party) patterns seen in several months."""
    ledger.build_indexes()
    cur = ledger.currency
    buckets: dict[tuple[str, str | None], list[tuple[date, Money]]] = {}
    for txn in ledger.transactions:
        if not (start <= txn.txn_date <= end):
            continue
        for line in txn.lines:
            acct = ledger.account(line.account_id)
            if acct is None or not acct.type.is_income_statement:
                continue
            party = line.party_id or txn.party_id
            buckets.setdefault((line.account_id, party), []).append((txn.txn_date, line.amount))

    profiles: dict[str, RecurringProfile] = {}
    for (account_id, party_id), entries in buckets.items():
        if len(entries) < min_occurrences:
            continue
        months = sorted({month_key(d) for d, _ in entries})
        if len(months) < min_occurrences:
            continue
        amounts = tuple(a for _, a in entries)
        key = f"{account_id}|{party_id or '-'}"
        profiles[key] = RecurringProfile(
            key=key,
            account_id=account_id,
            party_id=party_id,
            occurrences=len(entries),
            months_seen=tuple(months),
            median_amount=median_money(amounts, cur),
            amounts=amounts,
        )
    return profiles


_WS = re.compile(r"[^a-z0-9]+")


def _normalise_text(value: str | None) -> str:
    if not value:
        return ""
    return _WS.sub(" ", value.lower()).strip()


def duplicate_fingerprint(
    *,
    party_id: str | None,
    amount: Money,
    doc_number: str | None = None,
    memo: str | None = None,
    include_doc: bool = True,
) -> str:
    """Stable hash used to cluster likely-duplicate documents.

    Document number is included when present because a repeated vendor invoice
    number is near-conclusive. When absent, party plus exact amount plus
    normalised memo is the next-best signal, and the date proximity test in the
    control decides whether the cluster is actually a defect.
    """
    parts = [
        party_id or "-",
        str(amount.minor_units),
        amount.currency,
        _normalise_text(doc_number) if include_doc else "",
        _normalise_text(memo) if not doc_number else "",
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:24]


@dataclass(frozen=True)
class DuplicateCluster:
    fingerprint: str
    transactions: tuple[Transaction, ...]
    amount: Money
    max_days_apart: int

    @property
    def count(self) -> int:
        return len(self.transactions)

    @property
    def exposure(self) -> Money:
        """Value at risk: everything beyond the first, legitimate copy."""
        return self.amount.scale(self.count - 1)


def find_duplicate_clusters(
    transactions: Iterable[Transaction],
    *,
    within_days: int = 90,
    use_doc_number: bool = True,
) -> list[DuplicateCluster]:
    groups: dict[str, list[Transaction]] = {}
    for txn in transactions:
        fp = duplicate_fingerprint(
            party_id=txn.party_id,
            amount=txn.absolute_value,
            doc_number=txn.doc_number if use_doc_number else None,
            memo=txn.memo,
            include_doc=use_doc_number,
        )
        groups.setdefault(fp, []).append(txn)

    clusters: list[DuplicateCluster] = []
    for fp, members in groups.items():
        if len(members) < 2:
            continue
        members.sort(key=lambda t: (t.txn_date, t.txn_id))
        spread = (members[-1].txn_date - members[0].txn_date).days
        if spread > within_days:
            continue
        clusters.append(
            DuplicateCluster(
                fingerprint=fp,
                transactions=tuple(members),
                amount=members[0].absolute_value,
                max_days_apart=spread,
            )
        )
    clusters.sort(key=lambda c: -c.exposure.minor_units)
    return clusters


def is_round_amount(amount: Money, *, threshold_minor: int = 100_000) -> bool:
    """Round-dollar amounts above a threshold are a classic estimate/plug tell."""
    units = abs(amount.minor_units)
    if units < threshold_minor:
        return False
    return units % 100_000 == 0
