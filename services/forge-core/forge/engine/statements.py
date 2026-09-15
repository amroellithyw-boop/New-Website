"""Income statement and balance sheet reconstruction from the canonical ledger.

The gate that matters (blueprint section 23, "data gate") is that these
statements reproduce the source ledger *exactly*. So this module derives every
figure from the trial balance rather than from a parallel query, and exposes the
balance-sheet equation and the retained-earnings roll-forward as first-class,
checkable facts instead of hoping they tie.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from ..canonical.enums import AccountSubtype, AccountType
from ..canonical.models import Ledger
from ..money import Money, msum
from .trial_balance import AccountBalance, TrialBalance, build_trial_balance

__all__ = [
    "StatementSection",
    "IncomeStatement",
    "BalanceSheet",
    "build_income_statement",
    "build_balance_sheet",
]

COGS_SUBTYPES = (
    AccountSubtype.COST_OF_GOODS_SOLD,
    AccountSubtype.DIRECT_LABOUR,
    AccountSubtype.SUBCONTRACTOR,
    AccountSubtype.MATERIALS,
    AccountSubtype.EQUIPMENT_RENTAL,
)

CURRENT_ASSET_SUBTYPES = (
    AccountSubtype.BANK,
    AccountSubtype.ACCOUNTS_RECEIVABLE,
    AccountSubtype.UNDEPOSITED_FUNDS,
    AccountSubtype.INVENTORY,
    AccountSubtype.PREPAID_EXPENSE,
    AccountSubtype.WORK_IN_PROGRESS,
    AccountSubtype.OTHER_CURRENT_ASSET,
)

CURRENT_LIABILITY_SUBTYPES = (
    AccountSubtype.ACCOUNTS_PAYABLE,
    AccountSubtype.CREDIT_CARD,
    AccountSubtype.SALES_TAX_PAYABLE,
    AccountSubtype.PAYROLL_LIABILITY,
    AccountSubtype.ACCRUED_LIABILITY,
    AccountSubtype.DEFERRED_REVENUE,
    AccountSubtype.OTHER_CURRENT_LIABILITY,
)


@dataclass(frozen=True)
class StatementSection:
    """A labelled group of trial-balance rows plus its subtotal."""

    label: str
    rows: tuple[AccountBalance, ...]
    total: Money
    is_movement: bool = False

    def value_of(self, row: AccountBalance) -> Money:
        """The amount this section presents for ``row``."""
        return row.presentation_movement if self.is_movement else row.presentation_balance

    def nonzero(self) -> tuple[AccountBalance, ...]:
        return tuple(r for r in self.rows if not self.value_of(r).is_zero)

    def sorted_rows(self) -> tuple[AccountBalance, ...]:
        return tuple(sorted(self.nonzero(), key=lambda r: -abs(self.value_of(r).minor_units)))


def _section(
    label: str, rows: list[AccountBalance], currency: str, *, movement: bool = False
) -> StatementSection:
    """Build a section. ``movement=True`` for P&L, closing balances for the BS."""
    values = (
        (r.presentation_movement if movement else r.presentation_balance) for r in rows
    )
    return StatementSection(
        label=label, rows=tuple(rows), total=msum(values, currency), is_movement=movement
    )


@dataclass(frozen=True)
class IncomeStatement:
    entity_id: str
    currency: str
    start: date
    end: date
    revenue: StatementSection
    cost_of_sales: StatementSection
    operating_expenses: StatementSection
    other_income: StatementSection
    other_expenses: StatementSection

    @property
    def gross_profit(self) -> Money:
        return self.revenue.total - self.cost_of_sales.total

    @property
    def operating_income(self) -> Money:
        return self.gross_profit - self.operating_expenses.total

    @property
    def net_income(self) -> Money:
        return self.operating_income + self.other_income.total - self.other_expenses.total

    @property
    def gross_margin(self) -> Decimal | None:
        return self.gross_profit.ratio_to(self.revenue.total)

    @property
    def net_margin(self) -> Decimal | None:
        return self.net_income.ratio_to(self.revenue.total)

    @property
    def sections(self) -> tuple[StatementSection, ...]:
        return (
            self.revenue,
            self.cost_of_sales,
            self.operating_expenses,
            self.other_income,
            self.other_expenses,
        )


@dataclass(frozen=True)
class BalanceSheet:
    entity_id: str
    currency: str
    as_of: date
    period_start: date
    current_assets: StatementSection
    fixed_assets: StatementSection
    other_assets: StatementSection
    current_liabilities: StatementSection
    long_term_liabilities: StatementSection
    equity: StatementSection
    net_income_for_period: Money
    prior_period_earnings: Money

    @property
    def total_assets(self) -> Money:
        return self.current_assets.total + self.fixed_assets.total + self.other_assets.total

    @property
    def total_liabilities(self) -> Money:
        return self.current_liabilities.total + self.long_term_liabilities.total

    @property
    def total_equity(self) -> Money:
        """Equity accounts plus every dollar of earnings not yet closed out.

        Two separate amounts have to be added back or the sheet will not
        balance. Prior fiscal years' profit is closed to retained earnings only
        as a *presentation* step in most SMB ledgers, never as a posted entry,
        so it must be derived. Current-year profit is genuinely open. Deriving
        both here, once, is what makes the equation check below a real control
        rather than a decoration.
        """
        return self.equity.total + self.prior_period_earnings + self.net_income_for_period

    @property
    def equation_difference(self) -> Money:
        """Assets - (Liabilities + Equity). Must be exactly zero."""
        return self.total_assets - (self.total_liabilities + self.total_equity)

    @property
    def balances(self) -> bool:
        return self.equation_difference.is_zero

    @property
    def working_capital(self) -> Money:
        return self.current_assets.total - self.current_liabilities.total

    @property
    def current_ratio(self) -> Decimal | None:
        return self.current_assets.total.ratio_to(self.current_liabilities.total)


def build_income_statement(tb: TrialBalance) -> IncomeStatement:
    """Derive the P&L from a period trial balance."""
    cur = tb.currency
    revenue = [r for r in tb.rows if r.account.subtype is AccountSubtype.SALES_REVENUE]
    other_inc = [r for r in tb.rows if r.account.subtype is AccountSubtype.OTHER_INCOME]
    cogs = [r for r in tb.rows if r.account.subtype in COGS_SUBTYPES]
    other_exp_subs = (
        AccountSubtype.INTEREST_EXPENSE,
        AccountSubtype.INCOME_TAX_EXPENSE,
        AccountSubtype.OTHER_EXPENSE,
    )
    other_exp = [r for r in tb.rows if r.account.subtype in other_exp_subs]
    opex = [
        r
        for r in tb.rows
        if r.account.type is AccountType.EXPENSE
        and r.account.subtype not in COGS_SUBTYPES
        and r.account.subtype not in other_exp_subs
    ]
    return IncomeStatement(
        entity_id=tb.entity_id,
        currency=cur,
        start=tb.start,
        end=tb.end,
        revenue=_section("Revenue", revenue, cur, movement=True),
        cost_of_sales=_section("Cost of sales", cogs, cur, movement=True),
        operating_expenses=_section("Operating expenses", opex, cur, movement=True),
        other_income=_section("Other income", other_inc, cur, movement=True),
        other_expenses=_section("Other expenses", other_exp, cur, movement=True),
    )


def build_balance_sheet(
    ledger: Ledger,
    as_of: date,
    *,
    fiscal_year_start: date,
) -> BalanceSheet:
    """Build a balance sheet as at ``as_of``.

    ``fiscal_year_start`` determines which earnings are still un-closed and must
    therefore be presented as current-period net income inside equity.
    """
    # Inception-to-date trial balance gives cumulative balance-sheet positions.
    inception = date(1900, 1, 1)
    cumulative = build_trial_balance(ledger, inception, as_of)
    ytd = build_trial_balance(ledger, fiscal_year_start, as_of)
    ytd_pl = build_income_statement(ytd)
    prior_tb = build_trial_balance(
        ledger, inception, fiscal_year_start - timedelta(days=1)
    )
    prior_pl = build_income_statement(prior_tb)
    cur = ledger.currency

    def bs_rows(subtypes) -> list[AccountBalance]:
        return [r for r in cumulative.rows if r.account.subtype in subtypes]

    current_assets = bs_rows(CURRENT_ASSET_SUBTYPES)
    fixed_assets = bs_rows(
        (AccountSubtype.FIXED_ASSET, AccountSubtype.ACCUMULATED_DEPRECIATION)
    )
    other_assets = bs_rows((AccountSubtype.OTHER_ASSET,))
    current_liabs = bs_rows(CURRENT_LIABILITY_SUBTYPES)
    lt_liabs = bs_rows((AccountSubtype.LOAN_PAYABLE, AccountSubtype.OTHER_LIABILITY))
    equity = [r for r in cumulative.rows if r.account.type is AccountType.EQUITY]

    return BalanceSheet(
        entity_id=ledger.entity.entity_id,
        currency=cur,
        as_of=as_of,
        period_start=fiscal_year_start,
        current_assets=_section("Current assets", current_assets, cur),
        fixed_assets=_section("Property and equipment", fixed_assets, cur),
        other_assets=_section("Other assets", other_assets, cur),
        current_liabilities=_section("Current liabilities", current_liabs, cur),
        long_term_liabilities=_section("Long-term liabilities", lt_liabs, cur),
        equity=_section("Equity", equity, cur),
        net_income_for_period=ytd_pl.net_income,
        prior_period_earnings=prior_pl.net_income,
    )
