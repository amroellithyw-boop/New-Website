"""Sales tax return preparation from the ledger.

Prepares the figures for a GST/HST return in the line structure the CRA uses,
entirely from postings, so every line can be traced to the transactions behind
it. Nothing is filed. The output is a working paper for the tax specialist and
the human who signs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from ..canonical.enums import AccountSubtype, AccountType, TxnType
from ..money import Money
from ..rules.base import RuleContext

__all__ = ["SalesTaxReturn", "sales_tax_return"]


@dataclass(frozen=True)
class SalesTaxReturn:
    period_start: date
    period_end: date
    currency: str
    line_101_sales: Money
    """Total sales and other revenue, net of tax."""
    line_103_tax_collected: Money
    line_104_adjustments: Money
    line_106_input_tax_credits: Money
    line_107_adjustments: Money
    line_110_instalments: Money
    ledger_liability_closing: Money
    """The tax account's closing balance, to reconcile the return against."""
    sales_transaction_count: int
    purchase_transaction_count: int
    warnings: tuple[str, ...] = ()

    @property
    def line_105_total_collected(self) -> Money:
        return self.line_103_tax_collected + self.line_104_adjustments

    @property
    def line_108_total_credits(self) -> Money:
        return self.line_106_input_tax_credits + self.line_107_adjustments

    @property
    def line_109_net_tax(self) -> Money:
        return self.line_105_total_collected - self.line_108_total_credits

    @property
    def line_113_balance(self) -> Money:
        """Positive is owed to the authority; negative is a refund claim."""
        return self.line_109_net_tax - self.line_110_instalments

    @property
    def implied_rate(self) -> Decimal | None:
        return self.line_103_tax_collected.ratio_to(self.line_101_sales)

    def to_dict(self) -> dict:
        m = lambda x: str(x.to_decimal())  # noqa: E731
        return {
            "period": f"{self.period_start.isoformat()}..{self.period_end.isoformat()}",
            "line_101_sales": m(self.line_101_sales),
            "line_103_tax_collected": m(self.line_103_tax_collected),
            "line_105_total_collected": m(self.line_105_total_collected),
            "line_106_input_tax_credits": m(self.line_106_input_tax_credits),
            "line_108_total_credits": m(self.line_108_total_credits),
            "line_109_net_tax": m(self.line_109_net_tax),
            "line_110_instalments": m(self.line_110_instalments),
            "line_113_balance": m(self.line_113_balance),
            "ledger_liability_closing": m(self.ledger_liability_closing),
            "warnings": list(self.warnings),
        }


def sales_tax_return(ctx: RuleContext, *, period_start: date | None = None, period_end: date | None = None) -> SalesTaxReturn:
    start = period_start or ctx.period_start
    end = period_end or ctx.period_end
    cur = ctx.currency
    tax_accounts = [a.account_id for a in ctx.ledger.accounts_of(subtype=AccountSubtype.SALES_TAX_PAYABLE)]
    warnings: list[str] = []
    if not tax_accounts:
        warnings.append("no sales tax payable account in the chart; return cannot be prepared")

    collected = Money.zero(cur)
    credits = Money.zero(cur)
    sales_count = purchase_count = 0
    sales_txn_types = {TxnType.INVOICE, TxnType.SALES_RECEIPT, TxnType.CREDIT_MEMO}
    for acct in tax_accounts:
        for txn, line in ctx.ledger.postings(acct):
            if not (start <= txn.txn_date <= end):
                continue
            if txn.type in sales_txn_types:
                collected = collected - line.amount  # credits are negative in debit-positive terms
                sales_count += 1
            elif txn.doc_number and txn.doc_number.upper().startswith(("HST", "GST")):
                continue  # a remittance, not collection or credit
            elif line.amount.minor_units > 0:
                credits = credits + line.amount
                purchase_count += 1
            else:
                warnings.append(f"unclassified credit to the tax account on {txn.doc_number or txn.txn_id}")

    sales = Money.zero(cur)
    for row in ctx.ledger.accounts_of(type=AccountType.REVENUE):
        for txn, line in ctx.ledger.postings(row.account_id):
            if start <= txn.txn_date <= end and txn.type in sales_txn_types:
                sales = sales - line.amount

    closing = Money.zero(cur)
    for acct in tax_accounts:
        r = ctx.tb.row(acct)
        if r:
            closing = closing + r.presentation_balance

    return SalesTaxReturn(
        period_start=start, period_end=end, currency=cur,
        line_101_sales=sales, line_103_tax_collected=collected, line_104_adjustments=Money.zero(cur),
        line_106_input_tax_credits=credits, line_107_adjustments=Money.zero(cur),
        line_110_instalments=Money.zero(cur), ledger_liability_closing=closing,
        sales_transaction_count=sales_count, purchase_transaction_count=purchase_count,
        warnings=tuple(warnings),
    )
