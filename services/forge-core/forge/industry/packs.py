"""Industry packs: what a healthy business of this type and size looks like.

This is the layer that turns "your gross margin is 22%" into "your gross margin
is 22% against 35 to 45 percent for a landscaping contractor of your size, which
is roughly $310,000 of margin a year". The second sentence is the product.

The data is a working Canadian bookkeeping practice's accumulated knowledge:
WSIB rate groups, seasonality and deferred-revenue treatment by trade, capital
cost allowance classes, the errors each industry actually makes, and cost
structures across five size tiers. It is the least copyable asset in the system,
which is why it is loaded as data rather than written into prompts.

Sourcing note: these are practitioner benchmarks, not published statistics. They
are good enough to prioritise a conversation and not good enough to assert as
fact to a lender. Controls built on them therefore report a *signal* with a
confidence below one, never a defect.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..money import Money
from .ranges import MoneyRange, Range, parse_money_range, parse_percent_range

__all__ = [
    "SizeTier",
    "CostLine",
    "IndustryPack",
    "BenchmarkComparison",
    "load_packs",
    "pack_for",
    "TIER_ORDER",
]

DATA_DIR = Path(__file__).parent / "data"
TIER_ORDER: tuple[str, ...] = ("startup", "micro", "small", "mid", "upper_mid")

TIER_LABELS: dict[str, str] = {
    "startup": "start-up",
    "micro": "micro",
    "small": "small",
    "mid": "mid-size",
    "upper_mid": "upper mid-market",
}


@dataclass(frozen=True)
class SizeTier:
    """Expected economics for one revenue band within an industry."""

    name: str
    revenue_range: MoneyRange | None
    gross_margin: Range | None
    net_margin: Range | None
    labour_percent: Range | None
    owner_compensation_percent: Range | None
    employees: str = ""
    notes: str = ""

    @property
    def label(self) -> str:
        return TIER_LABELS.get(self.name, self.name)


@dataclass(frozen=True)
class CostLine:
    """One expected cost category, with the account range it should land in."""

    name: str
    typical_percent: Range | None
    account_range: tuple[int, int] | None
    behaviour: str  # "fixed" | "semi-fixed" | "variable"

    def covers_account(self, account_number: str) -> bool:
        if self.account_range is None:
            return False
        try:
            number = int("".join(ch for ch in account_number if ch.isdigit()))
        except ValueError:
            return False
        return self.account_range[0] <= number <= self.account_range[1]


@dataclass(frozen=True)
class IndustryPack:
    """Everything known about how one kind of business should look."""

    naics_code: str
    label: str
    wsib_rate: Decimal | None = None
    wsib_group: str = ""
    gross_margin: Range | None = None
    net_margin: Range | None = None
    labour_percent: Range | None = None
    fuel_percent: Range | None = None
    revenue_per_employee: MoneyRange | None = None
    owner_compensation: MoneyRange | None = None
    revenue_streams: tuple[str, ...] = ()
    cogs_drivers: tuple[str, ...] = ()
    key_expenses: tuple[str, ...] = ()
    seasonal: str = ""
    seasonal_note: str = ""
    deferred_revenue: bool = False
    deferred_note: str = ""
    cca_classes: tuple[tuple[str, str], ...] = ()
    hst_note: str = ""
    fiscal_year_note: str = ""
    common_errors: tuple[str, ...] = ()
    job_types: tuple[str, ...] = ()
    fixed_costs: tuple[CostLine, ...] = ()
    variable_costs: tuple[CostLine, ...] = ()
    size_tiers: tuple[SizeTier, ...] = ()

    # ---- lookup --------------------------------------------------------

    def tier_for(self, annual_revenue: Money) -> SizeTier | None:
        """The size tier whose revenue band contains this business.

        Falls back to the nearest tier rather than returning nothing, because a
        business sitting in a gap between two published bands still deserves a
        benchmark.
        """
        if not self.size_tiers:
            return None
        for tier in self.size_tiers:
            if tier.revenue_range and tier.revenue_range.contains(annual_revenue):
                return tier
        below = [t for t in self.size_tiers if t.revenue_range and t.revenue_range.high]
        for tier in below:
            if annual_revenue <= tier.revenue_range.high:  # type: ignore[union-attr]
                return tier
        return self.size_tiers[-1]

    def cost_line_for(self, account_number: str) -> CostLine | None:
        for line in self.fixed_costs + self.variable_costs:
            if line.covers_account(account_number):
                return line
        return None

    @property
    def has_benchmarks(self) -> bool:
        return bool(self.size_tiers) or self.gross_margin is not None

    def effective_gross_margin(self, annual_revenue: Money | None = None) -> Range | None:
        """Size-specific gross margin where available, else the industry range."""
        if annual_revenue is not None:
            tier = self.tier_for(annual_revenue)
            if tier and tier.gross_margin:
                return tier.gross_margin
        return self.gross_margin

    def effective_net_margin(self, annual_revenue: Money | None = None) -> Range | None:
        if annual_revenue is not None:
            tier = self.tier_for(annual_revenue)
            if tier and tier.net_margin:
                return tier.net_margin
        return self.net_margin

    def effective_labour_percent(self, annual_revenue: Money | None = None) -> Range | None:
        if annual_revenue is not None:
            tier = self.tier_for(annual_revenue)
            if tier and tier.labour_percent:
                return tier.labour_percent
        return self.labour_percent


@dataclass(frozen=True)
class BenchmarkComparison:
    """One measured ratio set against its expected range."""

    metric: str
    actual: Decimal
    expected: Range
    tier_label: str
    industry_label: str

    @property
    def position(self) -> str:
        return self.expected.position(self.actual)

    @property
    def is_outside(self) -> bool:
        return self.position != "within"

    @property
    def gap(self) -> Decimal:
        """Signed distance to the nearest bound; zero when inside the range."""
        if self.position == "below":
            return -self.expected.shortfall(self.actual)
        if self.position == "above":
            return self.expected.excess(self.actual)
        return Decimal(0)

    def value_of_gap(self, base: Money) -> Money:
        """The gap expressed in money against a base such as revenue."""
        return base.scale(abs(self.gap))

    def describe(self) -> str:
        return (
            f"{self.metric} is {self.actual * 100:.1f}% against "
            f"{self.expected.format_percent()} for a {self.tier_label} "
            f"{self.industry_label} business"
        )


def _cost_lines(raw: Iterable[dict[str, Any]]) -> tuple[CostLine, ...]:
    out: list[CostLine] = []
    for item in raw or ():
        acct = item.get("acct_range")
        out.append(
            CostLine(
                name=item.get("name", ""),
                typical_percent=parse_percent_range(item.get("typical_pct")),
                account_range=(int(acct[0]), int(acct[1])) if acct and len(acct) == 2 else None,
                behaviour=item.get("type", "variable"),
            )
        )
    return tuple(out)


def _size_tiers(raw: dict[str, Any] | None) -> tuple[SizeTier, ...]:
    if not raw:
        return ()
    out: list[SizeTier] = []
    for name in TIER_ORDER:
        entry = raw.get(name)
        if not entry:
            continue
        out.append(
            SizeTier(
                name=name,
                revenue_range=parse_money_range(entry.get("rev_range")),
                gross_margin=parse_percent_range(entry.get("target_gm")),
                net_margin=parse_percent_range(entry.get("target_net")),
                labour_percent=parse_percent_range(entry.get("target_labour_pct")),
                owner_compensation_percent=parse_percent_range(
                    entry.get("typical_owner_salary_pct")
                ),
                employees=entry.get("employees", ""),
                notes=entry.get("notes", ""),
            )
        )
    return tuple(out)


def _read(name: str) -> Any:
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def load_packs() -> dict[str, IndustryPack]:
    """Load and join the industry data, keyed by NAICS code.

    Intelligence is keyed by NAICS and cost structures by label, so the two are
    joined on the label. Nineteen of the twenty-two industries have both; the
    rest keep their intelligence and simply have no size tiers, which the
    controls handle by falling back to the industry-wide range.
    """
    intelligence: dict[str, Any] = _read("intelligence.json")
    structures: dict[str, Any] = _read("cost_structure.json")

    packs: dict[str, IndustryPack] = {}
    for code, entry in intelligence.items():
        label = entry.get("label", code)
        structure = structures.get(label, {})
        packs[code] = IndustryPack(
            naics_code=code,
            label=label,
            wsib_rate=Decimal(str(entry["wsib_rate"])) if entry.get("wsib_rate") is not None else None,
            wsib_group=entry.get("wsib_group", ""),
            gross_margin=parse_percent_range(entry.get("gp_benchmark")),
            net_margin=parse_percent_range(entry.get("net_benchmark")),
            labour_percent=parse_percent_range(entry.get("labour_pct")),
            fuel_percent=parse_percent_range(entry.get("fuel_pct")),
            revenue_per_employee=parse_money_range(entry.get("revenue_per_employee")),
            owner_compensation=parse_money_range(entry.get("owner_comp_range")),
            revenue_streams=tuple(entry.get("revenue_streams", ())),
            cogs_drivers=tuple(entry.get("cogs_drivers", ())),
            key_expenses=tuple(entry.get("key_expenses", ())),
            seasonal=entry.get("seasonal", ""),
            seasonal_note=entry.get("seasonal_note", ""),
            deferred_revenue=bool(entry.get("deferred_revenue")),
            deferred_note=entry.get("deferred_note", ""),
            cca_classes=tuple(
                (c[0], c[1]) for c in entry.get("cca_classes", ()) if isinstance(c, list) and len(c) == 2
            ),
            hst_note=entry.get("hst_note", ""),
            fiscal_year_note=entry.get("fiscal_year_note", ""),
            common_errors=tuple(entry.get("common_errors", ())),
            job_types=tuple(entry.get("job_types", ())),
            fixed_costs=_cost_lines(structure.get("fixed", ())),
            variable_costs=_cost_lines(structure.get("variable", ())),
            size_tiers=_size_tiers(structure.get("size_benchmarks")),
        )
    return packs


def pack_for(naics_code: str | None) -> IndustryPack | None:
    """Look up an industry pack, tolerating a missing or unknown code."""
    if not naics_code:
        return None
    return load_packs().get(str(naics_code))


def packs_with_benchmarks() -> list[IndustryPack]:
    return [p for p in load_packs().values() if p.has_benchmarks]
