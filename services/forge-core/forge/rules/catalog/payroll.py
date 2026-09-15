"""Controls 37-39: payroll tie-out, remittance aging and accrual reasonableness.

Payroll is the highest-consequence area in an SMB file. A missed source-deduction
remittance is one of the few liabilities a director is personally on the hook
for, so control 38 is CRITICAL by design rather than by amount.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Iterable

from ...canonical.enums import AccountSubtype, RiskTier, Severity, TxnType
from ...money import Money, msum
from ..base import Finding, Rule, RuleContext, register


@register
class PayrollToLedgerTie(Rule):
    rule_id = "FOS-R037"
    version = "1"
    title = "Payroll expense ties to payroll runs"
    purpose = (
        "Wages in the ledger must equal the sum of the payroll registers. A "
        "difference means a run was posted twice, missed, or manually adjusted."
    )
    category = "payroll"
    severity = Severity.HIGH
    risk_tier_floor = RiskTier.R3
    remediation = (
        "Reconcile each payroll run to its register and identify the entry that "
        "created the difference."
    )
    evidence_required = ("payroll runs", "wage account movement")

    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        wage_accounts = [
            a.account_id
            for a in ctx.ledger.accounts.values()
            if a.subtype in (AccountSubtype.PAYROLL_EXPENSE, AccountSubtype.DIRECT_LABOUR)
        ]
        if not wage_accounts:
            return
        from_runs = Money.zero(ctx.currency)
        run_count = 0
        for txn in ctx.ledger.transactions:
            if txn.type is not TxnType.PAYROLL:
                continue
            if not (ctx.period_start <= txn.txn_date <= ctx.period_end):
                continue
            run_count += 1
            for line in txn.lines:
                if line.account_id in wage_accounts:
                    from_runs = from_runs + line.amount
        if run_count == 0:
            return
        in_ledger = Money.zero(ctx.currency)
        non_payroll = []
        for account_id in wage_accounts:
            for txn, line in ctx.ledger.postings(account_id):
                if not (ctx.period_start <= txn.txn_date <= ctx.period_end):
                    continue
                in_ledger = in_ledger + line.amount
                if txn.type is not TxnType.PAYROLL:
                    non_payroll.append((txn, line))
        diff = in_ledger - from_runs
        if diff.is_zero or ctx.materiality.is_trivial(diff):
            return
        builder = ctx.packet(
            f"{self.rule_id}-tie", "Tie wage accounts to the payroll runs in the period"
        )
        for txn, _l in sorted(non_payroll, key=lambda p: -abs(p[1].amount).minor_units)[:15]:
            builder.transaction(txn, label="Wage account posting outside a payroll run")
        builder.calc("payroll runs in period", "count", run_count)
        builder.calc("wages per payroll runs", "sum(wage lines on payroll transactions)", from_runs)
        builder.calc("wages per ledger", "sum(all postings to wage accounts)", in_ledger)
        builder.calc("difference", "ledger - payroll runs", diff)
        builder.calc("postings outside a payroll run", "count", len(non_payroll))
        yield self.finding(
            ctx,
            suffix="payroll-tie",
            title=f"Wage accounts differ from payroll runs by {abs(diff).format()}",
            narrative=(
                f"{run_count} payroll run(s) total {from_runs.format()} but the wage accounts "
                f"moved {in_ledger.format()}, a difference of {diff.format()} arising from "
                f"{len(non_payroll)} posting(s) made outside a payroll run."
            ),
            exposure=abs(diff),
            packet=builder.build(),
        )


@register
class PayrollRemittanceAging(Rule):
    rule_id = "FOS-R038"
    version = "1"
    title = "Source deduction liability not remitted"
    purpose = (
        "Employee source deductions are trust funds. An aged balance is a personal "
        "liability for the directors and attracts penalties immediately."
    )
    category = "payroll"
    severity = Severity.CRITICAL
    risk_tier_floor = RiskTier.R4
    remediation = (
        "Remit immediately and confirm the filing frequency assigned by the tax "
        "authority. Escalate to the owner the same day; this does not wait for close."
    )
    evidence_required = ("payroll liability roll-forward", "remittance confirmations")
    involves_disbursed_cash = True

    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        for acct in ctx.ledger.accounts_of(subtype=AccountSubtype.PAYROLL_LIABILITY):
            row = ctx.tb.row(acct.account_id)
            if row is None:
                continue
            balance = row.presentation_balance
            if balance.minor_units <= 0:
                continue
            # Expected residual: deductions from runs in the final month only.
            recent_start = ctx.period_end - timedelta(days=31)
            accrued_recent = Money.zero(ctx.currency)
            for txn, line in ctx.ledger.postings(acct.account_id):
                if txn.type is TxnType.PAYROLL and recent_start <= txn.txn_date <= ctx.period_end:
                    accrued_recent = accrued_recent - line.amount
            excess = balance - accrued_recent
            if excess.minor_units <= 0 or ctx.materiality.is_trivial(excess):
                continue
            builder = ctx.packet(
                f"{self.rule_id}-{acct.account_id}", "Age the source deduction liability"
            )
            recent = [
                t
                for (t, _l) in ctx.ledger.postings(acct.account_id)
                if ctx.period_start <= t.txn_date <= ctx.period_end
            ][-12:]
            builder.transactions(recent)
            builder.calc("closing liability", "trial balance closing balance", balance)
            builder.calc(
                "deductions from the final month's runs",
                "sum(payroll postings in last 31 days)",
                accrued_recent,
            )
            builder.calc("aged beyond one cycle", "closing - recent accrual", excess)
            yield self.finding(
                ctx,
                suffix=acct.account_id,
                title=f"{excess.format()} of source deductions are overdue for remittance",
                narrative=(
                    f"{acct.name} closed at {balance.format()} while only {accrued_recent.format()} "
                    "relates to the most recent payroll cycle. The remainder should already have "
                    "been remitted and is accruing penalties and interest."
                ),
                exposure=excess,
                packet=builder.build(),
            )


@register
class AccruedPayrollReasonableness(Rule):
    rule_id = "FOS-R039"
    version = "1"
    title = "Accrued payroll and vacation reasonableness"
    purpose = (
        "Recompute the wages earned between the last pay date and period end, and "
        "compare with what was accrued."
    )
    category = "payroll"
    severity = Severity.MEDIUM
    risk_tier_floor = RiskTier.R2
    remediation = "Post or correct the accrual so the period carries its own labour cost."
    evidence_required = ("last payroll date", "daily wage run rate", "accrual entry")

    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        runs = [
            t
            for t in ctx.ledger.transactions
            if t.type is TxnType.PAYROLL and t.txn_date <= ctx.period_end
        ]
        if len(runs) < 2:
            return
        runs.sort(key=lambda t: t.txn_date)
        last_run = runs[-1]
        uncovered_days = (ctx.period_end - last_run.txn_date).days
        if uncovered_days <= 2:
            return
        # Daily run rate from the last three runs.
        recent = runs[-3:]
        total = msum((t.absolute_value for t in recent), ctx.currency)
        span_days = max((recent[-1].txn_date - recent[0].txn_date).days, 1)
        daily = total.scale(Decimal(1) / Decimal(span_days + 15))
        expected = daily.scale(uncovered_days)
        accrued = Money.zero(ctx.currency)
        for acct in ctx.ledger.accounts_of(subtype=AccountSubtype.ACCRUED_LIABILITY):
            row = ctx.tb.row(acct.account_id)
            if row:
                accrued = accrued + row.presentation_balance
        shortfall = expected - accrued
        if shortfall.minor_units <= 0 or shortfall < ctx.materiality.performance:
            return
        builder = ctx.packet(
            f"{self.rule_id}-accrual", "Recompute wages earned but unpaid at period end"
        )
        builder.transactions(recent)
        builder.calc("last payroll date", "most recent payroll run", last_run.txn_date.isoformat())
        builder.calc("days to period end", "period end - last payroll date", uncovered_days)
        builder.calc("daily wage run rate", "recent payroll total / days covered", daily)
        builder.calc("expected accrual", "daily rate x uncovered days", expected)
        builder.calc("accrued liabilities recorded", "trial balance closing", accrued)
        builder.calc("shortfall", "expected - recorded", shortfall)
        yield self.finding(
            ctx,
            suffix="payroll-accrual",
            title=f"Payroll accrual appears understated by {shortfall.format()}",
            narrative=(
                f"The last payroll ran {last_run.txn_date.isoformat()}, leaving {uncovered_days} "
                f"days of wages earned before period end. At the recent run rate that is about "
                f"{expected.format()} against {accrued.format()} accrued."
            ),
            exposure=shortfall,
            packet=builder.build(),
            confidence=Decimal("0.6"),
        )
