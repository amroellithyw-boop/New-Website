"""Materiality.

Materiality is what separates a control system from a noise generator. A
$40 coding error in a $6M business is not a finding; the same error in a
$120k business might be. ForgeOS computes a benchmark-based materiality the
way an auditor does, then applies a clearly-trivial floor below which nothing
is reported at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..money import Money

__all__ = ["Materiality", "compute_materiality"]


@dataclass(frozen=True)
class Materiality:
    """Three thresholds, plus the benchmark that produced them."""

    overall: Money
    performance: Money
    trivial: Money
    benchmark_name: str
    benchmark_amount: Money
    rate: Decimal

    def is_material(self, amount: Money) -> bool:
        return abs(amount) >= self.performance

    def is_trivial(self, amount: Money) -> bool:
        return abs(amount) < self.trivial

    def significance(self, amount: Money) -> Decimal | None:
        """Amount as a multiple of performance materiality."""
        return abs(amount).ratio_to(self.performance)

    def describe(self) -> str:
        return (
            f"{self.rate:.2%} of {self.benchmark_name} "
            f"({self.benchmark_amount.format()}) = {self.overall.format()} overall; "
            f"performance {self.performance.format()}; "
            f"trivial below {self.trivial.format()}"
        )


def compute_materiality(
    *,
    revenue: Money,
    total_assets: Money,
    net_income: Money,
    currency: str = "CAD",
    performance_ratio: Decimal = Decimal("0.75"),
    trivial_ratio: Decimal = Decimal("0.05"),
    floor: Money | None = None,
) -> Materiality:
    """Pick a benchmark and derive thresholds from it.

    Benchmark choice follows normal practice for an owner-managed business:
    revenue at 0.5% is the primary anchor because it is the most stable measure
    in a company whose profit swings with one job. Profit is used only when the
    business is large and consistently profitable, and total assets act as a
    backstop for an asset-heavy or loss-making year. Performance materiality is
    set at 75% of overall to leave room for aggregation of smaller errors.
    """
    # Revenue is the primary benchmark for an owner-managed business. Profit is
    # deliberately NOT used as a candidate: an SMB whose profit swings between
    # 1% and 12% of revenue with one job would get a materiality that moves by an
    # order of magnitude month to month, which makes the control queue unusable
    # and, worse, unstable across periods.
    candidates: list[tuple[str, Money, Decimal]] = []
    if revenue.minor_units > 0:
        candidates.append(("revenue", revenue, Decimal("0.005")))
    elif total_assets.minor_units > 0:
        candidates.append(("total assets", total_assets, Decimal("0.01")))

    if not candidates:
        base = Money.from_decimal("5000.00", currency)
        return Materiality(
            overall=base,
            performance=base.scale(performance_ratio),
            trivial=base.scale(trivial_ratio),
            benchmark_name="default floor",
            benchmark_amount=Money.zero(currency),
            rate=Decimal(0),
        )

    name, benchmark, rate = candidates[0]
    overall = benchmark.scale(rate)

    default_floor = floor or Money.from_decimal("500.00", currency)
    if overall < default_floor:
        overall = default_floor

    return Materiality(
        overall=overall,
        performance=overall.scale(performance_ratio),
        trivial=overall.scale(trivial_ratio),
        benchmark_name=name,
        benchmark_amount=benchmark,
        rate=rate,
    )
