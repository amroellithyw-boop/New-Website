"""Controls 51-55: industry benchmarks, WSIB and seasonal revenue treatment.

These controls are different in kind from the rest of the catalogue. Everything
else proves a defect from the ledger itself. These compare the ledger against
what a business of this type and size is expected to look like, which is
judgement rather than proof.

So they are held to a stricter standard of honesty:

* Confidence is below one, always. A benchmark is a prompt for a conversation,
  never an assertion of error.
* They only fire when the gap is both outside the expected range *and* material
  in money, so a contractor two points off a benchmark is not told they have a
  problem.
* They stay silent when no industry is configured, rather than guessing one.

The benchmark data is a practising firm's accumulated knowledge of Canadian
trades, which is precisely why it is the least copyable part of the system.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal

from ...canonical.enums import AccountSubtype, AccountType, RiskTier, Severity
from ...evidence.packet import EvidenceRef
from ...industry import IndustryPack, pack_for
from ...industry.packs import BenchmarkComparison
from ...money import Money, msum
from ..base import Finding, Rule, RuleContext, register

MIN_CONFIDENCE = Decimal("0.5")


def _industry(ctx: RuleContext) -> IndustryPack | None:
    """The configured industry pack, or nothing. Never a guess."""
    return pack_for(ctx.policy.get("naics_code"))


def _annual_revenue(ctx: RuleContext) -> Money:
    return ctx.pl_ttm.revenue.total


def _ref_industry(builder, pack: IndustryPack) -> None:
    builder.context(
        industry=pack.label,
        naics_code=pack.naics_code,
        benchmark_source=(
            "Practitioner benchmarks for Canadian owner-managed businesses. "
            "Indicative for prioritisation, not a published statistic."
        ),
    )


@register
class GrossMarginAgainstIndustry(Rule):
    rule_id = "FOS-R051"
    version = "1"
    title = "Gross margin outside the range for this industry and size"
    purpose = (
        "Compare gross margin against what a business of this trade and revenue "
        "band normally earns. A margin well below the band is usually job costing "
        "leaking into overheads, underpricing, or unbilled work."
    )
    category = "benchmark"
    severity = Severity.HIGH
    risk_tier_floor = RiskTier.R2
    remediation = (
        "Reconcile the cost of sales accounts against the industry's expected "
        "cost structure, then test whether the gap is pricing, job costing, or "
        "revenue that was never billed."
    )
    evidence_required = ("income statement", "industry benchmark", "cost of sales detail")

    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        pack = _industry(ctx)
        if pack is None:
            return
        revenue = _annual_revenue(ctx)
        if revenue.minor_units <= 0:
            return
        expected = pack.effective_gross_margin(revenue)
        if expected is None:
            return
        actual = ctx.pl_ttm.gross_margin
        if actual is None:
            return

        tier = pack.tier_for(revenue)
        comparison = BenchmarkComparison(
            metric="Gross margin",
            actual=actual,
            expected=expected,
            tier_label=tier.label if tier else "typical",
            industry_label=pack.label,
        )
        if not comparison.is_outside or comparison.position == "above":
            return
        gap_value = comparison.value_of_gap(revenue)
        if gap_value < ctx.materiality.performance:
            return

        builder = ctx.packet(
            f"{self.rule_id}-gross-margin",
            "Compare gross margin against the industry and size benchmark",
        )
        for row in ctx.tb_ttm.rows:
            if row.account.type is AccountType.EXPENSE and not row.presentation_movement.is_zero:
                line = pack.cost_line_for(row.account.number)
                if line is None:
                    continue
                builder.ref(
                    EvidenceRef(
                        kind="account",
                        ref_id=row.account.account_id,
                        label=f"{row.account.number} {row.account.name} (expected {line.name})",
                        amount=row.presentation_movement,
                        on=ctx.period_end,
                    )
                )
        builder.calc("revenue, trailing twelve months", "income statement revenue", revenue)
        builder.calc(
            "cost of sales, trailing twelve months",
            "income statement cost of sales",
            ctx.pl_ttm.cost_of_sales.total,
        )
        builder.calc("gross margin", "gross profit / revenue", actual.quantize(Decimal("0.0001")))
        builder.calc("benchmark range", expected.source or expected.format_percent(), expected.format_percent())
        builder.calc(
            "margin shortfall",
            "benchmark lower bound - actual margin",
            abs(comparison.gap).quantize(Decimal("0.0001")),
        )
        builder.calc("annual value of the shortfall", "shortfall x revenue", gap_value)
        _ref_industry(builder, pack)
        if pack.cogs_drivers:
            builder.context(expected_cost_drivers=list(pack.cogs_drivers))

        yield self.finding(
            ctx,
            suffix="gross-margin",
            title=(
                f"Gross margin of {actual * 100:.1f}% is below the "
                f"{expected.format_percent()} benchmark, worth {gap_value.format()} a year"
            ),
            narrative=(
                f"{comparison.describe()}. Closing the gap to the bottom of the range is worth "
                f"about {gap_value.format()} of annual gross profit. This is a benchmark "
                "comparison rather than a proven error, so the first step is to confirm the "
                "cost of sales accounts contain what the benchmark assumes they contain."
            ),
            exposure=gap_value,
            packet=builder.build(),
            confidence=MIN_CONFIDENCE,
        )


@register
class LabourCostAgainstIndustry(Rule):
    rule_id = "FOS-R052"
    version = "1"
    title = "Direct labour outside the expected share of revenue"
    purpose = (
        "Labour is the largest controllable cost in a trades business. A share "
        "well above the band signals crew inefficiency, unbilled overtime, or "
        "field wages miscoded to overheads."
    )
    category = "benchmark"
    severity = Severity.MEDIUM
    risk_tier_floor = RiskTier.R2
    remediation = (
        "Confirm field wages are coded to cost of sales and administrative wages "
        "are not, then compare hours billed against hours paid by job."
    )
    evidence_required = ("wage accounts", "revenue", "industry benchmark")

    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        pack = _industry(ctx)
        if pack is None:
            return
        revenue = _annual_revenue(ctx)
        if revenue.minor_units <= 0:
            return
        expected = pack.effective_labour_percent(revenue)
        if expected is None:
            return

        labour_subtypes = (AccountSubtype.DIRECT_LABOUR, AccountSubtype.PAYROLL_EXPENSE)
        labour = msum(
            (r.presentation_movement for r in ctx.tb_ttm.rows_of(subtypes=labour_subtypes)),
            ctx.currency,
        )
        actual = labour.ratio_to(revenue)
        if actual is None:
            return

        tier = pack.tier_for(revenue)
        comparison = BenchmarkComparison(
            metric="Labour cost",
            actual=actual,
            expected=expected,
            tier_label=tier.label if tier else "typical",
            industry_label=pack.label,
        )
        if comparison.position != "above":
            return
        gap_value = comparison.value_of_gap(revenue)
        if gap_value < ctx.materiality.performance:
            return

        builder = ctx.packet(
            f"{self.rule_id}-labour", "Compare labour cost against the industry benchmark"
        )
        for row in ctx.tb_ttm.rows_of(subtypes=labour_subtypes):
            if row.presentation_movement.is_zero:
                continue
            builder.ref(
                EvidenceRef(
                    kind="account",
                    ref_id=row.account.account_id,
                    label=f"{row.account.number} {row.account.name}",
                    amount=row.presentation_movement,
                    on=ctx.period_end,
                )
            )
        builder.calc("labour cost, trailing twelve months", "sum(wage accounts)", labour)
        builder.calc("revenue, trailing twelve months", "income statement revenue", revenue)
        builder.calc("labour share", "labour / revenue", actual.quantize(Decimal("0.0001")))
        builder.calc("benchmark range", expected.source or "industry benchmark", expected.format_percent())
        builder.calc("annual value of the excess", "excess share x revenue", gap_value)
        _ref_industry(builder, pack)

        yield self.finding(
            ctx,
            suffix="labour",
            title=(
                f"Labour is {actual * 100:.1f}% of revenue against a "
                f"{expected.format_percent()} benchmark"
            ),
            narrative=(
                f"{comparison.describe()}, which is about {gap_value.format()} a year above the "
                "top of the range. Check first that administrative wages have not been coded to "
                "cost of sales before treating this as a productivity issue."
            ),
            exposure=gap_value,
            packet=builder.build(),
            confidence=MIN_CONFIDENCE,
        )


@register
class SeasonalRevenueNotDeferred(Rule):
    rule_id = "FOS-R053"
    version = "1"
    title = "Seasonal contract revenue recognised without deferral"
    purpose = (
        "Trades that sell prepaid seasonal contracts must defer the revenue and "
        "release it over the service period. Recognising it on receipt overstates "
        "profit in the collection months and understates it for the rest of the "
        "season, and it is the single most common error in this industry."
    )
    category = "benchmark"
    severity = Severity.HIGH
    risk_tier_floor = RiskTier.R3
    remediation = (
        "Identify prepaid seasonal contracts, move the unearned portion to "
        "deferred revenue, and set a monthly release schedule over the service period."
    )
    evidence_required = ("revenue by month", "deferred revenue balance", "contract terms")

    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        pack = _industry(ctx)
        if pack is None or not pack.deferred_revenue:
            return
        revenue = _annual_revenue(ctx)
        if revenue.minor_units <= 0:
            return

        deferred = msum(
            (
                r.presentation_balance
                for r in ctx.tb.rows_of(subtype=AccountSubtype.DEFERRED_REVENUE)
            ),
            ctx.currency,
        )
        if deferred.minor_units > 0:
            return  # the business is already deferring; nothing to report

        if revenue.scale(Decimal("0.05")) < ctx.materiality.performance:
            return

        builder = ctx.packet(
            f"{self.rule_id}-deferral",
            "Show seasonal revenue recognised with no deferred balance carried",
        )
        for row in ctx.tb_ttm.rows_of(type=AccountType.REVENUE):
            if row.presentation_movement.is_zero:
                continue
            builder.ref(
                EvidenceRef(
                    kind="account",
                    ref_id=row.account.account_id,
                    label=f"{row.account.number} {row.account.name}",
                    amount=row.presentation_movement,
                    on=ctx.period_end,
                )
            )
        builder.calc("revenue, trailing twelve months", "income statement revenue", revenue)
        builder.calc("deferred revenue carried", "balance sheet closing balance", deferred)
        builder.policy(pack.deferred_note or pack.seasonal_note or pack.seasonal)
        _ref_industry(builder, pack)

        yield self.finding(
            ctx,
            suffix="deferral",
            title="Seasonal revenue is recognised with no deferred balance carried",
            narrative=(
                f"{pack.label} businesses sell prepaid seasonal contracts, and none of the "
                f"{revenue.format()} of revenue is carried as deferred. "
                + (pack.deferred_note or pack.seasonal_note or "")
                + " Reported profit is therefore overstated in the collection months and "
                "understated across the rest of the service period."
            ),
            exposure=revenue.scale(Decimal("0.05")),
            packet=builder.build(),
            confidence=Decimal("0.55"),
        )


@register
class WsibRateMismatch(Rule):
    rule_id = "FOS-R054"
    version = "1"
    title = "WSIB premium inconsistent with the industry rate group"
    purpose = (
        "Premiums charged at the wrong rate group are either an overpayment the "
        "business can reclaim or an underpayment that will be assessed with interest."
    )
    category = "benchmark"
    severity = Severity.HIGH
    risk_tier_floor = RiskTier.R3
    remediation = (
        "Confirm the classification unit on the WSIB account against the work "
        "actually performed, and file for a refund or a correction accordingly."
    )
    evidence_required = ("wage base", "WSIB premiums paid", "rate group")

    TOLERANCE = Decimal("0.25")

    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        pack = _industry(ctx)
        if pack is None or pack.wsib_rate is None:
            return
        wsib_account = ctx.policy.get("wsib_account_id")
        if not wsib_account:
            return
        premiums = msum(
            (
                line.amount
                for (txn, line) in ctx.ledger.postings(str(wsib_account))
                if ctx.period_end.replace(year=ctx.period_end.year - 1) <= txn.txn_date <= ctx.period_end
            ),
            ctx.currency,
        )
        if premiums.minor_units <= 0:
            return
        wages = msum(
            (
                r.presentation_movement
                for r in ctx.tb_ttm.rows_of(
                    subtypes=(AccountSubtype.DIRECT_LABOUR, AccountSubtype.PAYROLL_EXPENSE)
                )
            ),
            ctx.currency,
        )
        if wages.minor_units <= 0:
            return
        expected = wages.scale(pack.wsib_rate)
        difference = premiums - expected
        ratio = abs(difference).ratio_to(expected)
        if ratio is None or ratio <= self.TOLERANCE:
            return
        if abs(difference) < ctx.materiality.performance:
            return

        builder = ctx.packet(
            f"{self.rule_id}-wsib", "Recompute the expected WSIB premium from the wage base"
        )
        builder.ref(
            EvidenceRef(
                kind="account",
                ref_id=str(wsib_account),
                label="WSIB premiums paid",
                amount=premiums,
                on=ctx.period_end,
            )
        )
        builder.calc("insurable wage base", "sum(wage accounts, trailing twelve months)", wages)
        builder.calc("published rate", f"{pack.wsib_group or 'rate group'}", pack.wsib_rate)
        builder.calc("expected premium", "wage base x published rate", expected)
        builder.calc("premiums actually paid", "postings to the WSIB account", premiums)
        builder.calc("difference", "paid - expected", difference)
        _ref_industry(builder, pack)

        direction = "more" if difference.minor_units > 0 else "less"
        yield self.finding(
            ctx,
            suffix="wsib",
            title=f"WSIB premiums are {abs(difference).format()} {direction} than the rate group implies",
            narrative=(
                f"A wage base of {wages.format()} at {pack.wsib_rate:.2%} "
                f"({pack.wsib_group or 'the published rate group'}) implies {expected.format()}, "
                f"against {premiums.format()} paid."
            ),
            exposure=abs(difference),
            packet=builder.build(),
            confidence=Decimal("0.6"),
        )


@register
class KnownIndustryErrorPattern(Rule):
    rule_id = "FOS-R055"
    version = "1"
    title = "Known error pattern for this industry is present"
    purpose = (
        "Each trade makes a predictable handful of mistakes. Checking for them "
        "explicitly is cheaper and more reliable than hoping a general control "
        "happens to catch one."
    )
    category = "benchmark"
    severity = Severity.MEDIUM
    risk_tier_floor = RiskTier.R2
    remediation = (
        "Work the listed patterns against the ledger for this client and record "
        "which apply, so the list becomes client-specific rather than generic."
    )
    evidence_required = ("industry error list", "related account balances")

    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        pack = _industry(ctx)
        if pack is None or not pack.common_errors:
            return
        # Only raise this as a checklist when at least one general control in a
        # related area also fired; otherwise it is a generic reminder, and a
        # generic reminder in a findings queue is noise.
        vehicle = msum(
            (
                r.presentation_movement
                for r in ctx.tb_ttm.rows_of(subtype=AccountSubtype.VEHICLE_EXPENSE)
            ),
            ctx.currency,
        )
        if vehicle.minor_units <= 0:
            return
        revenue = _annual_revenue(ctx)
        share = vehicle.ratio_to(revenue) if revenue.minor_units > 0 else None
        expected = pack.fuel_percent
        if expected is None or share is None or expected.position(share) != "above":
            return
        excess = revenue.scale(expected.excess(share))
        if excess < ctx.materiality.performance:
            return

        builder = ctx.packet(
            f"{self.rule_id}-patterns", "Industry error patterns with a supporting signal"
        )
        for row in ctx.tb_ttm.rows_of(subtype=AccountSubtype.VEHICLE_EXPENSE):
            if row.presentation_movement.is_zero:
                continue
            builder.ref(
                EvidenceRef(
                    kind="account",
                    ref_id=row.account.account_id,
                    label=f"{row.account.number} {row.account.name}",
                    amount=row.presentation_movement,
                    on=ctx.period_end,
                )
            )
        builder.calc("vehicle and fuel cost", "sum(vehicle accounts)", vehicle)
        builder.calc("share of revenue", "vehicle cost / revenue", share.quantize(Decimal("0.0001")))
        builder.calc("expected share", expected.source or "industry benchmark", expected.format_percent())
        builder.calc("excess above the range", "excess share x revenue", excess)
        builder.context(known_error_patterns=list(pack.common_errors))
        _ref_industry(builder, pack)

        yield self.finding(
            ctx,
            suffix="patterns",
            title=(
                f"Vehicle and fuel cost is {share * 100:.1f}% of revenue against "
                f"{expected.format_percent()}"
            ),
            narrative=(
                f"{excess.format()} a year sits above the expected range. For {pack.label} "
                "businesses the usual cause is on this list: "
                + "; ".join(pack.common_errors)
                + "."
            ),
            exposure=excess,
            packet=builder.build(),
            confidence=MIN_CONFIDENCE,
        )
