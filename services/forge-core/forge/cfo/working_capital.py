"""Working capital scorecard and the business health score.

The legacy scorecard and health score are carried over in intent and rebuilt on
the deterministic engine: every input is a number the engine already proves,
so the score cannot disagree with the statements it is scored on.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from ..canonical.enums import AccountSubtype
from ..engine.reconcile import ReconciliationResult
from ..money import Money, msum
from ..rules.base import RuleContext

__all__ = ["WorkingCapital", "working_capital", "HealthFactor", "HealthScore", "health_score"]


@dataclass(frozen=True)
class WorkingCapital:
    as_of: date
    cash: Money
    receivables: Money
    payables: Money
    inventory: Money
    current_assets: Money
    current_liabilities: Money
    revenue_ttm: Money
    cost_of_sales_ttm: Money

    @property
    def dso(self) -> Decimal | None:
        r = self.receivables.ratio_to(self.revenue_ttm)
        return (r * 365).quantize(Decimal("0.1")) if r is not None else None

    @property
    def dpo(self) -> Decimal | None:
        r = self.payables.ratio_to(self.cost_of_sales_ttm)
        return (r * 365).quantize(Decimal("0.1")) if r is not None else None

    @property
    def dio(self) -> Decimal | None:
        r = self.inventory.ratio_to(self.cost_of_sales_ttm)
        return (r * 365).quantize(Decimal("0.1")) if r is not None else None

    @property
    def cash_conversion_cycle(self) -> Decimal | None:
        if self.dso is None or self.dpo is None:
            return None
        return self.dso + (self.dio or Decimal(0)) - self.dpo

    @property
    def current_ratio(self) -> Decimal | None:
        r = self.current_assets.ratio_to(self.current_liabilities)
        return r.quantize(Decimal("0.01")) if r is not None else None

    @property
    def quick_ratio(self) -> Decimal | None:
        r = (self.cash + self.receivables).ratio_to(self.current_liabilities)
        return r.quantize(Decimal("0.01")) if r is not None else None

    @property
    def net_working_capital(self) -> Money:
        return self.current_assets - self.current_liabilities

    def to_dict(self) -> dict:
        return {
            "as_of": self.as_of.isoformat(),
            "cash": str(self.cash.to_decimal()),
            "receivables": str(self.receivables.to_decimal()),
            "payables": str(self.payables.to_decimal()),
            "net_working_capital": str(self.net_working_capital.to_decimal()),
            "dso_days": str(self.dso) if self.dso is not None else None,
            "dpo_days": str(self.dpo) if self.dpo is not None else None,
            "cash_conversion_cycle_days": str(self.cash_conversion_cycle) if self.cash_conversion_cycle is not None else None,
            "current_ratio": str(self.current_ratio) if self.current_ratio is not None else None,
            "quick_ratio": str(self.quick_ratio) if self.quick_ratio is not None else None,
        }


def working_capital(ctx: RuleContext) -> WorkingCapital:
    cur = ctx.currency
    bs = ctx.bs
    cash = msum((r.presentation_balance for r in ctx.tb.rows_of(subtype=AccountSubtype.BANK)), cur)
    inventory = msum((r.presentation_balance for r in ctx.tb.rows_of(subtype=AccountSubtype.INVENTORY)), cur)
    return WorkingCapital(
        as_of=ctx.period_end, cash=cash, receivables=ctx.ar.total, payables=ctx.ap.total,
        inventory=inventory, current_assets=bs.current_assets.total,
        current_liabilities=bs.current_liabilities.total,
        revenue_ttm=ctx.pl_ttm.revenue.total, cost_of_sales_ttm=ctx.pl_ttm.cost_of_sales.total,
    )


@dataclass(frozen=True)
class HealthFactor:
    label: str
    points: int
    maximum: int
    detail: str


@dataclass(frozen=True)
class HealthScore:
    score: int
    grade: str
    factors: tuple[HealthFactor, ...]

    def to_dict(self) -> dict:
        return {"score": self.score, "grade": self.grade,
                "factors": [{"label": f.label, "points": f.points, "max": f.maximum, "detail": f.detail} for f in self.factors]}


def _grade(score: int) -> str:
    return "A" if score >= 85 else "B" if score >= 70 else "C" if score >= 55 else "D" if score >= 40 else "F"


def health_score(ctx: RuleContext, *, findings=(), reconciliations: dict[str, ReconciliationResult] | None = None) -> HealthScore:
    """A 0 to 100 score from facts the engine has already proved.

    Weights, carried from the legacy design and re-based on proven inputs:
    gross margin against benchmark 20, net margin 15, receivables health 15,
    reconciliation 15, control findings 20, data integrity 15.
    """
    from ..industry import pack_for

    factors: list[HealthFactor] = []
    pack = pack_for(ctx.policy.get("naics_code"))
    rev = ctx.pl_ttm.revenue.total

    # Gross margin against the industry band.
    gm = ctx.pl_ttm.gross_margin
    band = pack.effective_gross_margin(rev) if pack else None
    if gm is None:
        factors.append(HealthFactor("Gross margin", 0, 20, "No revenue in the trailing twelve months"))
    elif band is None:
        pts = 14 if gm >= Decimal("0.30") else 8 if gm >= Decimal("0.20") else 3
        factors.append(HealthFactor("Gross margin", pts, 20, f"{gm * 100:.1f}% with no industry benchmark configured"))
    else:
        pos = band.position(gm)
        pts = 20 if pos != "below" else (12 if gm >= band.low * Decimal("0.85") else 4)
        factors.append(HealthFactor("Gross margin", pts, 20, f"{gm * 100:.1f}% against {band.format_percent()}"))

    nm = ctx.pl_ttm.net_margin
    nband = pack.effective_net_margin(rev) if pack else None
    if nm is None:
        factors.append(HealthFactor("Net margin", 0, 15, "No revenue"))
    elif nm.is_signed() and nm < 0:
        factors.append(HealthFactor("Net margin", 0, 15, f"{nm * 100:.1f}%; the business is losing money"))
    elif nband and nband.position(nm) != "below":
        factors.append(HealthFactor("Net margin", 15, 15, f"{nm * 100:.1f}% against {nband.format_percent()}"))
    else:
        factors.append(HealthFactor("Net margin", 8, 15, f"{nm * 100:.1f}% positive but below the band"))

    pct = ctx.ar.percent_past_due or Decimal(0)
    pts = 15 if pct <= Decimal("0.10") else 10 if pct <= Decimal("0.25") else 5 if pct <= Decimal("0.45") else 0
    factors.append(HealthFactor("Receivables", pts, 15, f"{pct * 100:.0f}% of receivables past due; {ctx.ar.past_due_total().format()}"))

    recs = reconciliations if reconciliations is not None else ctx.reconciliations
    if not recs:
        factors.append(HealthFactor("Bank reconciliation", 5, 15, "No statements provided for the period"))
    else:
        ok = sum(1 for r in recs.values() if r.is_reconciled)
        pts = 15 if ok == len(recs) else int(15 * ok / len(recs))
        factors.append(HealthFactor("Bank reconciliation", pts, 15, f"{ok} of {len(recs)} accounts reconcile"))

    crit = sum(1 for f in findings if getattr(f, "severity", None) and f.severity.value == "critical")
    high = sum(1 for f in findings if getattr(f, "severity", None) and f.severity.value == "high")
    pts = 20 if crit == 0 and high <= 2 else 12 if crit == 0 else 4 if crit <= 2 else 0
    factors.append(HealthFactor("Control findings", pts, 20, f"{crit} critical, {high} high"))

    ties = ctx.tb.closing_imbalance.is_zero and ctx.bs.equation_difference.is_zero and not ctx.tb.unbalanced_transactions
    factors.append(HealthFactor("Data integrity", 15 if ties else 0, 15, "Books tie out" if ties else "Books do not tie out"))

    total = min(100, sum(f.points for f in factors))
    return HealthScore(total, _grade(total), tuple(factors))
