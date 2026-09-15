"""Controls 20-25 and 50: variance, missing recurrence and concentration.

Variance controls are where a naive system drowns the client in noise. Three
rules keep them useful: compare like with like, require the change to clear
materiality *and* a percentage floor, and never report a variance the business
obviously expects (a snow-removal contractor billing nothing in July is not a
finding).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta
from decimal import Decimal

from ...canonical.enums import AccountType, RiskTier, Severity
from ...engine.aging import concentration
from ...engine.features import (
    month_key,
    monthly_series,
    period_variance,
    recurring_profiles,
)
from ...money import Money, msum
from ..base import Finding, Rule, RuleContext, register


def _previous_month(key: str) -> str:
    year, month = (int(p) for p in key.split("-"))
    year, month = (year - 1, 12) if month == 1 else (year, month - 1)
    return f"{year:04d}-{month:02d}"


class _VarianceBase(Rule):
    min_percent = Decimal("0.35")
    comparative_label = "prior period"

    def windows(self, ctx: RuleContext) -> tuple[date, date, date, date]:
        raise NotImplementedError

    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        cur_start, cur_end, cmp_start, cmp_end = self.windows(ctx)
        rows = period_variance(
            ctx.ledger, cur_start, cur_end, cmp_start, cmp_end, label=self.comparative_label
        )
        for row in rows:
            acct = ctx.ledger.account(row.account_id)
            if acct is None or not acct.type.is_income_statement:
                continue
            change = abs(row.absolute_change)
            if change < ctx.materiality.performance:
                continue
            pct = row.percent_change
            if pct is not None and abs(pct) < self.min_percent:
                continue
            if self._seasonally_expected(ctx, acct.account_id, cur_start, cur_end):
                continue
            builder = ctx.packet(
                f"{self.rule_id}-{row.account_id}",
                f"Explain the movement in {acct.name} against the {self.comparative_label}",
            )
            drivers = ctx.drivers(row.account_id, start=cur_start, end=cur_end)
            if not drivers:
                # The movement is a disappearance. The evidence is what used to be
                # there, so cite the comparative window instead of citing nothing.
                drivers = ctx.drivers(row.account_id, start=cmp_start, end=cmp_end)
                label = f"Posting to {acct.name} in the {self.comparative_label}"
            else:
                label = f"Driver posting to {acct.name}"
            for txn, _line in drivers:
                builder.transaction(txn, label=label)
            packet = (
                builder
                .calc(
                    "current period",
                    f"movement {cur_start.isoformat()} to {cur_end.isoformat()}",
                    row.current,
                )
                .calc(
                    self.comparative_label,
                    f"movement {cmp_start.isoformat()} to {cmp_end.isoformat()}",
                    row.comparative,
                )
                .calc("change", "current - comparative", row.absolute_change)
                .calc(
                    "percentage change",
                    "change / absolute comparative",
                    pct if pct is not None else Decimal(0),
                )
                .calc(
                    "performance materiality",
                    ctx.materiality.describe(),
                    ctx.materiality.performance,
                )
                .calc("driver postings shown", "largest postings in the period", len(drivers))
                .build()
            )
            direction = "increased" if row.absolute_change.minor_units > 0 else "decreased"
            pct_text = f" ({pct:.0%})" if pct is not None else ""
            yield self.finding(
                ctx,
                suffix=row.account_id,
                title=f"{acct.name} {direction} by {change.format()}{pct_text}",
                narrative=(
                    f"{acct.name} moved from {row.comparative.format()} to {row.current.format()} "
                    f"against the {self.comparative_label}, a change of {change.format()}"
                    f"{pct_text}. The change exceeds performance materiality and needs an "
                    "explanation tied to an operational cause."
                ),
                exposure=change,
                packet=packet,
                confidence=Decimal("0.7"),
            )

    def _seasonally_expected(self, ctx, account_id, cur_start, cur_end) -> bool:
        """Suppress variances a seasonal business plainly expects."""
        seasonal = ctx.policy.get("seasonal_accounts") or ()
        return account_id in seasonal


@register
class MonthOverMonthVariance(_VarianceBase):
    rule_id = "FOS-R020"
    version = "1"
    title = "Large month-over-month account variance"
    purpose = "Surface unexplained swings against the immediately preceding period."
    category = "variance"
    severity = Severity.MEDIUM
    risk_tier_floor = RiskTier.R2
    remediation = (
        "Identify the driver transactions and confirm the movement reflects real "
        "activity rather than a coding change or a missing accrual."
    )
    evidence_required = ("account movement both periods", "driver transactions")
    comparative_label = "prior period"

    def windows(self, ctx: RuleContext):
        span = (ctx.period_end - ctx.period_start).days
        prior_end = ctx.period_start - timedelta(days=1)
        return ctx.period_start, ctx.period_end, prior_end - timedelta(days=span), prior_end


@register
class YearOverYearVariance(_VarianceBase):
    rule_id = "FOS-R021"
    version = "1"
    title = "Large year-over-year account variance"
    purpose = (
        "The right comparison for a seasonal business is the same period last year, "
        "not last month."
    )
    category = "variance"
    severity = Severity.MEDIUM
    risk_tier_floor = RiskTier.R2
    remediation = (
        "Explain the year-on-year movement using volume, pricing, crew size or cost "
        "inflation, and confirm the classification is consistent between years."
    )
    evidence_required = ("account movement both years", "driver analysis")
    comparative_label = "same period last year"
    min_percent = Decimal("0.30")

    def windows(self, ctx: RuleContext):
        def shift(d: date) -> date:
            try:
                return d.replace(year=d.year - 1)
            except ValueError:  # 29 February
                return d.replace(year=d.year - 1, day=28)

        return ctx.period_start, ctx.period_end, shift(ctx.period_start), shift(ctx.period_end)


@register
class DormantRecurringAccount(Rule):
    rule_id = "FOS-R022"
    version = "1"
    title = "Recurring account with no activity this period"
    purpose = (
        "An account that has posted every month for a year and is suddenly empty "
        "usually means a missing bill, not a saved cost."
    )
    category = "variance"
    severity = Severity.HIGH
    risk_tier_floor = RiskTier.R2
    remediation = (
        "Confirm whether the underlying obligation ended. If it did not, accrue the "
        "missing cost before closing the period."
    )
    evidence_required = ("12-month account history", "vendor contract")

    LOOKBACK_MONTHS = 6

    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        hist_start = ctx.period_start - timedelta(days=31 * self.LOOKBACK_MONTHS)
        for acct in ctx.ledger.accounts.values():
            if not acct.type.is_income_statement:
                continue
            series = monthly_series(ctx.ledger, acct.account_id, hist_start, ctx.period_end)
            current_key = month_key(ctx.period_end)
            history = {k: v for k, v in series.items() if k < current_key}
            if len(history) < self.LOOKBACK_MONTHS:
                continue
            active_months = [v for v in history.values() if not v.is_zero]
            if len(active_months) < len(history):
                continue  # not reliably recurring
            current = series.get(current_key, Money.zero(ctx.currency))
            if not current.is_zero:
                continue
            typical = msum(active_months, ctx.currency).scale(
                Decimal(1) / Decimal(len(active_months))
            )
            if ctx.materiality.is_trivial(typical):
                continue
            builder = ctx.packet(
                f"{self.rule_id}-{acct.account_id}",
                f"Show that {acct.name} posts every month and is empty this month",
            )
            for txn, _line in ctx.drivers(
                acct.account_id, start=hist_start, end=ctx.period_start - timedelta(days=1)
            ):
                builder.transaction(txn, label=f"Prior posting to {acct.name}")
            packet = (
                builder
                .calc("months with activity in history", "count", len(active_months))
                .calc("average monthly amount", "sum(history) / months", typical)
                .calc("current month amount", "movement in period", current)
                .context(history={k: str(v.to_decimal()) for k, v in history.items()})
                .build()
            )
            yield self.finding(
                ctx,
                suffix=acct.account_id,
                title=f"{acct.name} has no activity this period",
                narrative=(
                    f"{acct.name} posted in every one of the last {len(history)} months, "
                    f"averaging {typical.format()}, and has nothing this period. The cost has "
                    "most likely not been received rather than not been incurred."
                ),
                exposure=abs(typical),
                packet=packet,
                confidence=Decimal("0.7"),
            )


@register
class MissingRecurringTransaction(Rule):
    rule_id = "FOS-R023"
    version = "1"
    title = "Recurring vendor transaction missing this period"
    purpose = (
        "Track the pattern at vendor level rather than account level, so a missing "
        "rent or insurance bill is caught even when the account has other activity."
    )
    category = "variance"
    severity = Severity.HIGH
    risk_tier_floor = RiskTier.R2
    remediation = "Chase the missing invoice or accrue the expected amount before close."
    evidence_required = ("recurring profile", "vendor history")

    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        hist_start = ctx.period_start - timedelta(days=200)
        profiles = recurring_profiles(ctx.ledger, hist_start, ctx.period_end, min_occurrences=4)
        current_key = month_key(ctx.period_end)
        for profile in profiles.values():
            if profile.party_id is None or not profile.is_monthly:
                continue
            if current_key in profile.months_seen:
                continue
            # Only report a gap in a pattern that was actually running. Three
            # consecutive months immediately before the gap is the test.
            if profile.consecutive_tail < 3:
                continue
            # The pattern must have been running right up to the month being
            # closed. A gap that started three months ago is a business change
            # someone already knows about, not news.
            if profile.months_seen[-1] != _previous_month(current_key):
                continue
            if ctx.materiality.is_trivial(profile.median_amount):
                continue
            acct = ctx.ledger.account(profile.account_id)
            party = ctx.ledger.parties.get(profile.party_id)
            builder = ctx.packet(
                f"{self.rule_id}-{profile.key}",
                "Show the recurring pattern and the gap in it",
            )
            for txn, _line in ctx.drivers(
                profile.account_id, start=hist_start, end=ctx.period_start - timedelta(days=1)
            ):
                builder.transaction(txn, label="Prior occurrence of the pattern")
            packet = (
                builder
                .calc("months observed", "count(distinct months)", len(profile.months_seen))
                .calc("typical amount", "median of historical amounts", profile.median_amount)
                .calc("current month", "month being closed", current_key)
                .context(months_seen=list(profile.months_seen))
                .build()
            )
            yield self.finding(
                ctx,
                suffix=profile.key.replace("|", "-"),
                title=(
                    f"Recurring {profile.median_amount.format()} from "
                    f"{party.name if party else profile.party_id} is missing"
                ),
                narrative=(
                    f"{party.name if party else profile.party_id} has posted to "
                    f"{acct.name if acct else profile.account_id} in "
                    f"{len(profile.months_seen)} months, typically "
                    f"{profile.median_amount.format()}, but nothing was recorded this period."
                ),
                exposure=abs(profile.median_amount),
                packet=packet,
                confidence=Decimal("0.65"),
            )


@register
class RecurringAmountDeviation(Rule):
    rule_id = "FOS-R024"
    version = "1"
    title = "Recurring amount deviates from its established pattern"
    purpose = (
        "A rent or subscription that suddenly doubles is either a price change "
        "nobody recorded, a duplicate inside one bill, or a keying error."
    )
    category = "variance"
    severity = Severity.MEDIUM
    risk_tier_floor = RiskTier.R2
    remediation = "Compare the invoice to the contract and confirm the new rate is agreed."
    evidence_required = ("recurring profile", "current invoice", "contract")

    DEVIATION = Decimal("0.35")

    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        hist_start = ctx.period_start - timedelta(days=200)
        profiles = recurring_profiles(ctx.ledger, hist_start, ctx.period_start - timedelta(days=1),
                                      min_occurrences=4)
        for profile in profiles.values():
            acct = ctx.ledger.account(profile.account_id)
            if acct is None or profile.party_id is None:
                continue
            # Only amounts that are genuinely fixed can meaningfully "deviate".
            if not profile.is_fixed_amount:
                continue
            current = [
                (t, line)
                for (t, line) in ctx.ledger.postings(profile.account_id)
                if ctx.period_start <= t.txn_date <= ctx.period_end
                and (line.party_id or t.party_id) == profile.party_id
            ]
            for txn, line in current:
                dev = profile.deviation(line.amount)
                if dev is None or dev < self.DEVIATION:
                    continue
                delta = line.amount - profile.median_amount
                if ctx.materiality.is_trivial(delta):
                    continue
                party = ctx.ledger.parties.get(profile.party_id or "")
                packet = (
                    ctx.packet(
                        f"{self.rule_id}-{line.line_id}",
                        "Compare the current amount with the established pattern",
                    )
                    .transaction(txn)
                    .calc("typical amount", "median of prior occurrences", profile.median_amount)
                    .calc("current amount", "line amount", line.amount)
                    .calc("difference", "current - typical", delta)
                    .calc("deviation", "abs(difference) / typical", dev)
                    .context(occurrences=profile.occurrences)
                    .build()
                )
                yield self.finding(
                    ctx,
                    suffix=line.line_id,
                    title=(
                        f"{party.name if party else 'Vendor'} charged {line.amount.format()} "
                        f"against a typical {profile.median_amount.format()}"
                    ),
                    narrative=(
                        f"The amount is {dev:.0%} away from the established pattern over "
                        f"{profile.occurrences} prior occurrences, a difference of "
                        f"{delta.format()}."
                    ),
                    exposure=abs(delta),
                    packet=packet,
                    confidence=Decimal("0.7"),
                )


@register
class CounterpartyConcentration(Rule):
    rule_id = "FOS-R025"
    version = "1"
    title = "Customer or vendor concentration"
    purpose = (
        "Concentration is the risk an owner-managed business is least likely to "
        "measure and most likely to be destroyed by."
    )
    category = "risk"
    severity = Severity.MEDIUM
    risk_tier_floor = RiskTier.R2
    remediation = (
        "Quantify the cash and profit impact of losing the counterparty, and set a "
        "diversification or contractual protection plan."
    )
    evidence_required = ("revenue by customer", "balances by counterparty")

    THRESHOLD = Decimal("0.25")

    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        revenue_by_customer: dict[str, Money] = {}
        for txn in ctx.ledger.transactions:
            if txn.type.value not in ("invoice", "sales_receipt"):
                continue
            if not (ctx.fiscal_year_start <= txn.txn_date <= ctx.period_end):
                continue
            for line in txn.lines:
                acct = ctx.ledger.account(line.account_id)
                if acct is None or acct.type is not AccountType.REVENUE:
                    continue
                pid = line.party_id or txn.party_id or "unknown"
                revenue_by_customer[pid] = revenue_by_customer.get(
                    pid, Money.zero(ctx.currency)
                ) + abs(line.amount)
        if not revenue_by_customer:
            return
        ranked = concentration(revenue_by_customer, ctx.currency)
        top_id, top_amount, top_share = ranked[0]
        if top_share < self.THRESHOLD:
            return
        party = ctx.ledger.parties.get(top_id)
        total = msum(revenue_by_customer.values(), ctx.currency)
        builder = ctx.packet(
            f"{self.rule_id}-{top_id}", "Rank revenue by customer year to date"
        )
        for txn in [
            t for t in ctx.ledger.transactions
            if t.party_id == top_id and ctx.fiscal_year_start <= t.txn_date <= ctx.period_end
        ][:8]:
            builder.transaction(txn, label="Revenue from the largest customer")
        packet = (
            builder
            .calc("largest customer revenue", "sum(revenue lines year to date)", top_amount)
            .calc("total revenue", "sum(all revenue year to date)", total)
            .calc("share", "largest / total", top_share)
            .context(
                top_five=[
                    {
                        "customer": (ctx.ledger.parties.get(pid).name
                                     if ctx.ledger.parties.get(pid) else pid),
                        "revenue": str(amt.to_decimal()),
                        "share": f"{share:.1%}",
                    }
                    for pid, amt, share in ranked[:5]
                ]
            )
            .build()
        )
        yield self.finding(
            ctx,
            suffix=top_id,
            title=(
                f"{party.name if party else top_id} is {top_share:.0%} of revenue"
            ),
            narrative=(
                f"{party.name if party else top_id} accounts for {top_amount.format()} of "
                f"{total.format()} revenue year to date. Losing this relationship would remove "
                f"{top_share:.0%} of the top line."
            ),
            exposure=top_amount,
            packet=packet,
            confidence=Decimal("1.0"),
        )


@register
class BudgetVarianceOutlier(Rule):
    rule_id = "FOS-R050"
    version = "1"
    title = "Budget to actual variance outlier"
    purpose = "Report where the period materially missed an approved budget."
    category = "planning"
    severity = Severity.MEDIUM
    risk_tier_floor = RiskTier.R2
    remediation = (
        "Explain the variance by driver and update the remaining forecast rather "
        "than leaving the budget stale."
    )
    evidence_required = ("approved budget", "actual movement")

    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        budget = ctx.policy.get("budget") or {}
        if not budget:
            return
        for account_id, budgeted in budget.items():
            row = ctx.tb.row(account_id)
            if row is None:
                continue
            actual = row.presentation_movement
            variance = actual - budgeted
            if abs(variance) < ctx.materiality.performance:
                continue
            acct = row.account
            pct = variance.ratio_to(budgeted)
            builder = ctx.packet(
                f"{self.rule_id}-{account_id}", f"Budget variance for {acct.name}"
            )
            for txn, _line in ctx.drivers(account_id):
                builder.transaction(txn, label=f"Posting to {acct.name}")
            packet = (
                builder
                .calc("budget", "approved budget for the period", budgeted)
                .calc("actual", "movement in period", actual)
                .calc("variance", "actual - budget", variance)
                .calc("variance percent", "variance / budget", pct if pct is not None else Decimal(0))
                .build()
            )
            yield self.finding(
                ctx,
                suffix=account_id,
                title=f"{acct.name} is {abs(variance).format()} against budget",
                narrative=(
                    f"{acct.name} came in at {actual.format()} against a budget of "
                    f"{budgeted.format()}."
                ),
                exposure=abs(variance),
                packet=packet,
            )
