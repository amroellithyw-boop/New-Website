"""Controls 45-49: debt servicing, covenants and cash runway.

Control 49 is the one an owner reads first. Runway is computed from actual
operating cash movement rather than accrual profit, because a profitable
contractor with 80-day receivables still runs out of money.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import timedelta
from decimal import Decimal

from ...canonical.enums import AccountSubtype, RiskTier, Severity
from ...engine.rollforward import build_rollforward, debt_rollforward
from ...money import Money, msum
from ..base import Finding, Rule, RuleContext, register


@register
class LoanPaymentSplit(Rule):
    rule_id = "FOS-R045"
    version = "1"
    title = "Loan payments not split between principal and interest"
    purpose = (
        "A whole loan payment coded to principal understates expense and overstates "
        "profit. Coded entirely to interest, it does the opposite and never clears "
        "the liability."
    )
    category = "debt"
    severity = Severity.HIGH
    risk_tier_floor = RiskTier.R3
    remediation = (
        "Obtain the lender amortisation schedule and correct the split for every "
        "payment in the period."
    )
    evidence_required = ("loan payments", "lender amortisation schedule")
    involves_disbursed_cash = True

    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        for debt_id, debt in ctx.ledger.debts.items():
            rf = debt_rollforward(ctx.ledger, debt_id, ctx.period_start, ctx.period_end)
            if rf.payments_total.is_zero:
                continue
            diff = rf.implied_split_difference
            # Coding the whole payment to principal makes the arithmetic tie
            # while eliminating interest expense entirely, so the split test has
            # to check for a missing interest component as well as a difference.
            no_interest = (
                rf.interest_expensed.is_zero
                and debt.annual_rate is not None
                and rf.opening_principal.minor_units > 0
            )
            if no_interest:
                expected_interest = rf.opening_principal.scale(
                    debt.annual_rate
                    * Decimal((ctx.period_end - ctx.period_start).days + 1)
                    / Decimal(365)
                )
                nb = ctx.packet(
                    f"{self.rule_id}-{debt_id}-nointerest",
                    f"Show that no interest was recorded on {debt.name}",
                )
                for txn, _line in ctx.drivers(debt.liability_account_id):
                    nb.transaction(txn, label=f"Payment posted to {debt.name}")
                packet = (
                    nb
                    .calc("cash paid on the loan", "sum(bank credits on payment entries)",
                          rf.payments_total)
                    .calc("principal reduction recorded", "debits to the loan account",
                          rf.principal_repaid)
                    .calc("interest expensed", "movement in interest expense",
                          rf.interest_expensed)
                    .calc("stated annual rate", "from the loan agreement", debt.annual_rate)
                    .calc("interest that should have been charged",
                          "opening principal x rate x days / 365", expected_interest)
                    .build()
                )
                yield self.finding(
                    ctx,
                    suffix=f"{debt_id}-no-interest",
                    title=f"{debt.name} payments recorded with no interest at all",
                    narrative=(
                        f"{rf.payments_total.format()} of payments were made on {debt.name} and "
                        f"the entire amount was applied to principal. At the stated rate of "
                        f"{debt.annual_rate:.2%}, about {expected_interest.format()} of interest "
                        "expense is missing and the loan balance is understated by the same amount."
                    ),
                    exposure=expected_interest,
                    packet=packet,
                )
                continue
            if abs(diff) < ctx.materiality.performance:
                continue
            sb = ctx.packet(f"{self.rule_id}-{debt_id}", f"Split test for {debt.name}")
            for txn, _line in ctx.drivers(debt.liability_account_id):
                sb.transaction(txn, label=f"Payment posted to {debt.name}")
            packet = (
                sb
                .calc("cash paid on the loan", "sum(bank credits on payment entries)", rf.payments_total)
                .calc("principal reduction recorded", "debits to the loan account", rf.principal_repaid)
                .calc("interest expensed", "movement in interest expense", rf.interest_expensed)
                .calc("unexplained", "cash paid - (principal + interest)", diff)
                .build()
            )
            yield self.finding(
                ctx,
                suffix=debt_id,
                title=f"{debt.name} payments are out by {abs(diff).format()}",
                narrative=(
                    f"{rf.payments_total.format()} of cash left the bank for {debt.name}, against "
                    f"{rf.principal_repaid.format()} of principal and "
                    f"{rf.interest_expensed.format()} of interest recorded."
                ),
                exposure=abs(diff),
                packet=packet,
            )


@register
class DebtRollForwardTie(Rule):
    rule_id = "FOS-R046"
    version = "1"
    title = "Debt roll-forward does not tie"
    purpose = "Prove opening principal plus borrowings less repayments equals closing."
    category = "debt"
    severity = Severity.HIGH
    risk_tier_floor = RiskTier.R2
    remediation = "Reconcile the loan account to the lender statement for the period."
    evidence_required = ("loan account movement", "lender statement")

    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        for debt_id, debt in ctx.ledger.debts.items():
            rf = debt_rollforward(ctx.ledger, debt_id, ctx.period_start, ctx.period_end)
            if rf.ties:
                continue
            rb = ctx.packet(f"{self.rule_id}-{debt_id}", f"Roll forward {debt.name}")
            for txn, _line in ctx.drivers(debt.liability_account_id):
                rb.transaction(txn, label=f"Movement in {debt.name}")
            packet = (
                rb
                .calc("opening principal", "balance at period start", rf.opening_principal)
                .calc("new borrowings", "credits to the loan account", rf.new_borrowings)
                .calc("principal repaid", "debits to the loan account", rf.principal_repaid)
                .calc("closing principal", "balance at period end", rf.closing_principal)
                .calc("difference", "opening + borrowings - repaid - closing", rf.difference)
                .build()
            )
            yield self.finding(
                ctx,
                suffix=debt_id,
                title=f"{debt.name} roll-forward is out by {abs(rf.difference).format()}",
                narrative=(
                    f"The movement in {debt.name} does not reconcile; "
                    f"{abs(rf.difference).format()} is unexplained."
                ),
                exposure=abs(rf.difference),
                packet=packet,
            )


@register
class InterestExpenseReasonableness(Rule):
    rule_id = "FOS-R047"
    version = "1"
    title = "Interest expense is not reasonable against the debt balance"
    purpose = "Recompute expected interest from average principal and the stated rate."
    category = "debt"
    severity = Severity.MEDIUM
    risk_tier_floor = RiskTier.R2
    remediation = "Reconcile to the lender interest statement and correct the charge."
    evidence_required = ("debt roll-forward", "stated rate", "interest expense")

    TOLERANCE = Decimal("0.25")

    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        for debt_id, debt in ctx.ledger.debts.items():
            if debt.annual_rate is None:
                continue
            rf = debt_rollforward(ctx.ledger, debt_id, ctx.period_start, ctx.period_end)
            average = (rf.opening_principal + rf.closing_principal).scale(Decimal("0.5"))
            if average.minor_units <= 0:
                continue
            days = max((ctx.period_end - ctx.period_start).days + 1, 1)
            expected = average.scale(debt.annual_rate * Decimal(days) / Decimal(365))
            if expected.minor_units == 0:
                continue
            variance = rf.interest_expensed - expected
            ratio = abs(variance).ratio_to(expected)
            if ratio is None or ratio <= self.TOLERANCE:
                continue
            if ctx.materiality.is_trivial(variance):
                continue
            ib = ctx.packet(
                f"{self.rule_id}-{debt_id}", f"Recompute interest on {debt.name}"
            )
            for txn, _line in ctx.drivers(debt.liability_account_id):
                ib.transaction(txn, label=f"Payment posted to {debt.name}")
            if debt.interest_account_id:
                for txn, _line in ctx.drivers(debt.interest_account_id):
                    ib.transaction(txn, label="Interest expense posting")
            packet = (
                ib
                .calc("opening principal", "balance at period start", rf.opening_principal)
                .calc("closing principal", "balance at period end", rf.closing_principal)
                .calc("average principal", "(opening + closing) / 2", average)
                .calc("stated annual rate", "from the loan agreement", debt.annual_rate)
                .calc("expected interest", "average x rate x days / 365", expected)
                .calc("interest recorded", "movement in interest expense", rf.interest_expensed)
                .calc("variance", "recorded - expected", variance)
                .build()
            )
            yield self.finding(
                ctx,
                suffix=debt_id,
                title=f"Interest on {debt.name} is {abs(variance).format()} from expectation",
                narrative=(
                    f"Average principal of {average.format()} at {debt.annual_rate:.2%} implies "
                    f"{expected.format()} for the period, against {rf.interest_expensed.format()} "
                    "recorded."
                ),
                exposure=abs(variance),
                packet=packet,
                confidence=Decimal("0.7"),
            )


@register
class CovenantHeadroom(Rule):
    rule_id = "FOS-R048"
    version = "1"
    title = "Loan covenant headroom"
    purpose = (
        "Compute the covenant ratios the lender tests and report headroom before a "
        "breach forces a repayment demand."
    )
    category = "debt"
    severity = Severity.CRITICAL
    risk_tier_floor = RiskTier.R4
    remediation = (
        "Model the ratio forward for the next two test dates and agree an action "
        "with the lender before the test, not after."
    )
    evidence_required = ("covenant definition", "ratio calculation", "forecast")
    involves_disbursed_cash = True

    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        covenants = ctx.policy.get("covenants") or {}
        if not covenants:
            return
        ebitda = (
            ctx.pl_ytd.operating_income
            + msum(
                (
                    r.presentation_movement
                    for r in ctx.tb_ytd.rows_of(subtype=AccountSubtype.DEPRECIATION_EXPENSE)
                ),
                ctx.currency,
            )
        )
        total_debt = msum(
            (
                r.presentation_balance
                for r in ctx.tb.rows_of(
                    subtypes=(AccountSubtype.LOAN_PAYABLE, AccountSubtype.OTHER_LIABILITY)
                )
            ),
            ctx.currency,
        )
        if "max_debt_to_ebitda" in covenants and ebitda.minor_units > 0:
            limit = Decimal(str(covenants["max_debt_to_ebitda"]))
            actual = total_debt.ratio_to(ebitda)
            if actual is not None and actual > limit * Decimal("0.85"):
                breached = actual > limit
                packet = (
                    ctx.packet(f"{self.rule_id}-leverage", "Compute the leverage covenant")
                    .calc("total debt", "sum(loan and other long-term liabilities)", total_debt)
                    .calc(
                        "earnings before interest, tax, depreciation and amortisation",
                        "operating income + depreciation, year to date",
                        ebitda,
                    )
                    .calc("ratio", "total debt / earnings measure", actual.quantize(Decimal("0.01")))
                    .calc("covenant limit", "per the loan agreement", limit)
                    .calc(
                        "headroom",
                        "limit - actual",
                        (limit - actual).quantize(Decimal("0.01")),
                    )
                    .build()
                )
                yield self.finding(
                    ctx,
                    suffix="leverage",
                    title=(
                        f"Leverage covenant {'breached' if breached else 'within 15% of its limit'} "
                        f"at {actual:.2f}x"
                    ),
                    narrative=(
                        f"Debt of {total_debt.format()} against an earnings measure of "
                        f"{ebitda.format()} gives {actual:.2f}x against a limit of {limit}x."
                    ),
                    exposure=total_debt,
                    packet=packet,
                    severity=Severity.CRITICAL if breached else Severity.HIGH,
                )


@register
class CashRunway(Rule):
    rule_id = "FOS-R049"
    version = "1"
    title = "Cash runway below the safety threshold"
    purpose = (
        "Measure how many weeks of operating cash remain at the current burn, using "
        "actual bank movement rather than accrual profit."
    )
    category = "treasury"
    severity = Severity.CRITICAL
    risk_tier_floor = RiskTier.R3
    remediation = (
        "Accelerate collections on the largest overdue balances, stage discretionary "
        "spend, and confirm available credit facility headroom this week."
    )
    evidence_required = ("bank balances", "operating cash movement", "receivables aging")
    involves_disbursed_cash = True

    MIN_WEEKS = Decimal("8")
    LOOKBACK_DAYS = 90

    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        cash = msum(
            (r.presentation_balance for r in ctx.tb.rows_of(subtype=AccountSubtype.BANK)),
            ctx.currency,
        )
        start = ctx.period_end - timedelta(days=self.LOOKBACK_DAYS)
        net_movement = Money.zero(ctx.currency)
        for acct in ctx.ledger.accounts_of(subtype=AccountSubtype.BANK):
            rf = build_rollforward(ctx.ledger, acct.account_id, start, ctx.period_end)
            net_movement = net_movement + rf.total_movement
        if net_movement.minor_units >= 0:
            return  # cash grew; runway is not the binding constraint
        weekly_burn = abs(net_movement).scale(Decimal(7) / Decimal(self.LOOKBACK_DAYS))
        if weekly_burn.minor_units <= 0:
            return
        weeks = cash.ratio_to(weekly_burn)
        if weeks is None or weeks > self.MIN_WEEKS:
            return
        collectible = ctx.ar.past_due_total(1)
        cb = ctx.packet(
            f"{self.rule_id}-runway", "Compute weeks of cash at the current burn"
        )
        for acct in ctx.ledger.accounts_of(subtype=AccountSubtype.BANK):
            for txn, _line in ctx.drivers(acct.account_id, start=start)[:6]:
                cb.transaction(txn, label="Largest cash movement in the window")
        packet = (
            cb
            .calc("cash on hand", "sum(bank account balances)", cash)
            .calc(
                f"net cash movement over {self.LOOKBACK_DAYS} days",
                "sum(bank postings in window)",
                net_movement,
            )
            .calc("weekly burn", "absolute movement x 7 / days in window", weekly_burn)
            .calc("weeks of runway", "cash / weekly burn", weeks.quantize(Decimal("0.1")))
            .calc("overdue receivables available to collect", "aging past due total", collectible)
            .build()
        )
        yield self.finding(
            ctx,
            suffix="runway",
            title=f"Cash runway is {weeks:.1f} weeks",
            narrative=(
                f"{cash.format()} of cash against a weekly burn of {weekly_burn.format()} leaves "
                f"about {weeks:.1f} weeks. {collectible.format()} of overdue receivables is the "
                "fastest available source of cash."
            ),
            exposure=cash,
            packet=packet,
        )
