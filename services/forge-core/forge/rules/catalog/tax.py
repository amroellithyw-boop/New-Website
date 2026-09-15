"""Controls 35-36: sales tax coding consistency and remittance reasonableness.

Canadian GST/HST is the reference implementation because the target vertical is
Ontario trades, but the control reads its rate and account from policy so the
same logic serves any jurisdiction.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal

from ...canonical.enums import AccountSubtype, AccountType, RiskTier, Severity, TxnType
from ...money import Money, msum
from ..base import Finding, Rule, RuleContext, register


@register
class SalesTaxCodeInconsistency(Rule):
    rule_id = "FOS-R035"
    version = "1"
    title = "Sales tax coding inconsistent with the account's own history"
    purpose = (
        "Revenue posted without a tax code, or at a rate inconsistent with the rest "
        "of the account, understates the remittance and creates an assessment risk."
    )
    category = "tax"
    severity = Severity.HIGH
    risk_tier_floor = RiskTier.R3
    remediation = (
        "Confirm whether the supply was genuinely exempt or zero-rated. If not, "
        "correct the tax code and amend the affected return."
    )
    evidence_required = ("revenue lines", "tax codes", "customer exemption certificate")

    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        expected_rate = ctx.policy.get("sales_tax_rate", Decimal("0.13"))
        for acct in ctx.ledger.accounts_of(type=AccountType.REVENUE):
            lines = [
                (t, line)
                for (t, line) in ctx.ledger.postings(acct.account_id)
                if ctx.period_start <= t.txn_date <= ctx.period_end
                and t.type in (TxnType.INVOICE, TxnType.SALES_RECEIPT)
            ]
            if len(lines) < 4:
                continue
            untaxed = [
                (t, line)
                for (t, line) in lines
                if line.tax_code is None or line.tax_amount is None or line.tax_amount.is_zero
            ]
            if not untaxed or len(untaxed) == len(lines):
                continue  # all-or-nothing is a deliberate policy, not an anomaly
            exposure = msum((abs(line.amount) for _t, line in untaxed), ctx.currency)
            tax_at_risk = exposure.scale(expected_rate)
            if ctx.materiality.is_trivial(tax_at_risk):
                continue
            builder = ctx.packet(
                f"{self.rule_id}-{acct.account_id}",
                f"Compare taxed and untaxed revenue in {acct.name}",
            )
            for txn, _l in untaxed[:15]:
                builder.transaction(txn)
            builder.calc("revenue lines in account", "count", len(lines))
            builder.calc("lines with no tax applied", "count", len(untaxed))
            builder.calc("untaxed revenue", "sum(line amounts with no tax)", exposure)
            builder.calc(
                "tax potentially underremitted",
                f"untaxed revenue x {expected_rate}",
                tax_at_risk,
            )
            yield self.finding(
                ctx,
                suffix=acct.account_id,
                title=(
                    f"{len(untaxed)} of {len(lines)} revenue lines in {acct.name} carry no sales tax"
                ),
                narrative=(
                    f"{exposure.format()} of revenue in {acct.name} was invoiced without sales tax "
                    f"while the rest of the account was taxed, putting roughly "
                    f"{tax_at_risk.format()} of tax at risk."
                ),
                exposure=tax_at_risk,
                packet=builder.build(),
                confidence=Decimal("0.7"),
            )


@register
class SalesTaxPayableReasonableness(Rule):
    rule_id = "FOS-R036"
    version = "1"
    title = "Sales tax payable reasonableness"
    purpose = (
        "Recompute the expected net tax position from taxable sales and input tax "
        "credits, and compare it with the recorded liability."
    )
    category = "tax"
    severity = Severity.HIGH
    risk_tier_floor = RiskTier.R3
    remediation = (
        "Reconcile the tax account to the filed returns. An unexplained difference "
        "means either a return was wrong or a remittance was misposted."
    )
    evidence_required = ("tax account roll-forward", "filed returns", "taxable sales")
    involves_disbursed_cash = True

    def evaluate(self, ctx: RuleContext) -> Iterable[Finding]:
        tax_accounts = ctx.ledger.accounts_of(subtype=AccountSubtype.SALES_TAX_PAYABLE)
        if not tax_accounts:
            return
        rate = ctx.policy.get("sales_tax_rate", Decimal("0.13"))
        for acct in tax_accounts:
            row = ctx.tb.row(acct.account_id)
            if row is None:
                continue
            collected = Money.zero(ctx.currency)
            credited = Money.zero(ctx.currency)
            remitted = Money.zero(ctx.currency)
            for txn, line in ctx.ledger.postings(acct.account_id):
                if not (ctx.period_start <= txn.txn_date <= ctx.period_end):
                    continue
                if txn.type in (TxnType.INVOICE, TxnType.SALES_RECEIPT):
                    collected = collected - line.amount
                elif txn.type in (TxnType.BILL, TxnType.EXPENSE) and line.amount.minor_units > 0:
                    if txn.doc_number and txn.doc_number.upper().startswith("HST"):
                        remitted = remitted + line.amount
                    else:
                        credited = credited + line.amount
                else:
                    remitted = remitted + line.amount
            expected_collected = ctx.pl.revenue.total.scale(rate)
            variance = collected - expected_collected
            tolerance = max(
                ctx.materiality.performance.minor_units,
                expected_collected.scale(Decimal("0.05")).minor_units,
            )
            if abs(variance).minor_units <= tolerance:
                continue
            builder = ctx.packet(
                f"{self.rule_id}-{acct.account_id}", "Recompute expected sales tax collected"
            )
            builder.calc("revenue in period", "income statement revenue", ctx.pl.revenue.total)
            builder.calc("statutory rate", "jurisdiction rate from policy", rate)
            builder.calc("expected tax collected", f"revenue x {rate}", expected_collected)
            builder.calc("tax actually recorded", "sum(tax postings on sales)", collected)
            builder.calc("variance", "recorded - expected", variance)
            builder.calc("input tax credits claimed", "sum(tax on purchases)", credited)
            builder.calc("remittances in period", "sum(payments to authority)", remitted)
            builder.calc("closing liability", "trial balance closing", row.presentation_balance)
            yield self.finding(
                ctx,
                suffix=acct.account_id,
                title=f"Sales tax collected is {abs(variance).format()} away from expected",
                narrative=(
                    f"Revenue of {ctx.pl.revenue.total.format()} at {rate:.0%} implies "
                    f"{expected_collected.format()} of tax, but {collected.format()} was "
                    "recorded. The difference needs to reconcile to exempt or zero-rated supplies."
                ),
                exposure=abs(variance),
                packet=builder.build(),
                confidence=Decimal("0.6"),
            )
