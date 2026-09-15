"""A thirteen-week cash forecast from what the ledger already knows.

Nothing is guessed by a model. Receipts come from the aging with collection
rates by bucket; disbursements come from open payables by due date, the payroll
cadence observed in the ledger, recurring vendor profiles, and loan schedules.
Every week states its assumptions so the owner can argue with them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from ..canonical.enums import AccountSubtype, TxnType
from ..engine.features import recurring_profiles
from ..money import Money, msum
from ..rules.base import RuleContext

__all__ = ["CashWeek", "CashForecast", "cash_forecast", "COLLECTION_RATES"]

# Expected collection rate for open receivables by aging bucket, applied across
# the forecast horizon. Practitioner defaults; tune per client from outcomes.
COLLECTION_RATES: dict[str, Decimal] = {
    "Current": Decimal("0.95"), "1-30": Decimal("0.85"), "31-60": Decimal("0.65"),
    "61-90": Decimal("0.40"), "90+": Decimal("0.15"),
}


@dataclass(frozen=True)
class CashWeek:
    week: int
    start: date
    end: date
    opening: Money
    receipts: Money
    payroll: Money
    payables: Money
    recurring: Money
    debt_service: Money
    closing: Money
    assumptions: tuple[str, ...] = ()

    @property
    def disbursements(self) -> Money:
        return self.payroll + self.payables + self.recurring + self.debt_service


@dataclass
class CashForecast:
    as_of: date
    opening_cash: Money
    weeks: list[CashWeek] = field(default_factory=list)
    minimum_cash: Money | None = None
    minimum_week: int | None = None

    @property
    def lowest_point(self) -> tuple[int, Money] | None:
        if not self.weeks:
            return None
        w = min(self.weeks, key=lambda x: x.closing.minor_units)
        return w.week, w.closing

    @property
    def goes_negative(self) -> bool:
        return any(w.closing.minor_units < 0 for w in self.weeks)

    def to_dict(self) -> dict:
        return {
            "as_of": self.as_of.isoformat(),
            "opening_cash": str(self.opening_cash.to_decimal()),
            "goes_negative": self.goes_negative,
            "lowest_point": {"week": self.lowest_point[0], "cash": str(self.lowest_point[1].to_decimal())} if self.lowest_point else None,
            "weeks": [{
                "week": w.week, "start": w.start.isoformat(), "opening": str(w.opening.to_decimal()),
                "receipts": str(w.receipts.to_decimal()), "payroll": str(w.payroll.to_decimal()),
                "payables": str(w.payables.to_decimal()), "recurring": str(w.recurring.to_decimal()),
                "debt_service": str(w.debt_service.to_decimal()), "closing": str(w.closing.to_decimal()),
                "assumptions": list(w.assumptions),
            } for w in self.weeks],
        }


def cash_forecast(ctx: RuleContext, *, weeks: int = 13) -> CashForecast:
    cur = ctx.currency
    start = ctx.period_end + timedelta(days=1)
    cash = msum((r.presentation_balance for r in ctx.tb.rows_of(subtype=AccountSubtype.BANK)), cur)

    # Receipts: each open receivable collected at its bucket's rate, spread over
    # the weeks after its due date (or immediately if already past due).
    receipts: list[Money] = [Money.zero(cur) for _ in range(weeks)]
    for item in ctx.ar.items:
        rate = COLLECTION_RATES.get(item.bucket, Decimal("0.5"))
        expected = item.outstanding.scale(rate)
        if expected.minor_units <= 0:
            continue
        due = item.item.due_on
        first_week = 0 if due <= start else min(weeks - 1, (due - start).days // 7)
        # spread across the week it falls due and the following two
        spread = expected.allocate([3, 2, 1])
        for k, part in enumerate(spread):
            idx = first_week + k
            if idx < weeks:
                receipts[idx] = receipts[idx] + part

    payables: list[Money] = [Money.zero(cur) for _ in range(weeks)]
    for item in ctx.ap.items:
        due = item.item.due_on
        idx = 0 if due <= start else min(weeks - 1, (due - start).days // 7)
        payables[idx] = payables[idx] + item.outstanding

    # Payroll: observed cadence and typical net pay over the last 90 days.
    runs = [t for t in ctx.ledger.transactions
            if t.type is TxnType.PAYROLL and ctx.period_end - timedelta(days=90) < t.txn_date <= ctx.period_end]
    payroll: list[Money] = [Money.zero(cur) for _ in range(weeks)]
    if runs:
        runs.sort(key=lambda t: t.txn_date)
        net_pays = []
        for t in runs:
            bank_out = msum((-line.amount for line in t.lines
                             if (ctx.ledger.account(line.account_id) or type("x", (), {"subtype": None})).subtype is AccountSubtype.BANK), cur)
            net_pays.append(bank_out)
        typical = msum(net_pays, cur).scale(Decimal(1) / Decimal(len(net_pays)))
        gaps = [(b.txn_date - a.txn_date).days for a, b in zip(runs, runs[1:], strict=False)] or [14]
        cadence = max(7, round(sum(gaps) / len(gaps)))
        nxt = runs[-1].txn_date + timedelta(days=cadence)
        while (nxt - start).days < weeks * 7:
            if nxt >= start:
                payroll[(nxt - start).days // 7] = payroll[(nxt - start).days // 7] + typical
            nxt += timedelta(days=cadence)

    # Recurring monthly vendors (rent, insurance, subscriptions): fixed-amount profiles.
    recurring: list[Money] = [Money.zero(cur) for _ in range(weeks)]
    profiles = recurring_profiles(ctx.ledger, ctx.period_end - timedelta(days=200), ctx.period_end, min_occurrences=4)
    seen: set[str] = set()
    for p in profiles.values():
        if not p.is_monthly or not p.is_fixed_amount or p.median_amount.minor_units <= 0:
            continue
        # A pattern with no counterparty is a journal entry: depreciation,
        # amortisation, an accrual. None of those move cash, and a forecast that
        # counts them pays the depreciation charge out of the bank every month.
        if p.party_id is None or p.party_id in seen:
            continue
        seen.add(p.party_id)
        for m in range(4):
            due = start + timedelta(days=30 * m + 1)
            idx = (due - start).days // 7
            if idx < weeks:
                recurring[idx] = recurring[idx] + p.median_amount

    debt: list[Money] = [Money.zero(cur) for _ in range(weeks)]
    for d in ctx.ledger.debts.values():
        if d.scheduled_payment:
            for m in range(4):
                idx = min(weeks - 1, (30 * m + 4) // 7)
                debt[idx] = debt[idx] + d.scheduled_payment

    forecast = CashForecast(as_of=ctx.period_end, opening_cash=cash)
    balance = cash
    for w in range(weeks):
        ws = start + timedelta(days=7 * w)
        closing = balance + receipts[w] - payroll[w] - payables[w] - recurring[w] - debt[w]
        forecast.weeks.append(CashWeek(
            week=w + 1, start=ws, end=ws + timedelta(days=6), opening=balance, receipts=receipts[w],
            payroll=payroll[w], payables=payables[w], recurring=recurring[w], debt_service=debt[w], closing=closing,
            assumptions=(
                "receipts at bucket collection rates spread over three weeks from due date",
                "payroll at the observed cadence and average net pay of the last 90 days",
                "recurring vendors at their median fixed monthly amount",
            ),
        ))
        balance = closing
    if forecast.weeks:
        low = min(forecast.weeks, key=lambda x: x.closing.minor_units)
        forecast.minimum_cash, forecast.minimum_week = low.closing, low.week
    return forecast
