"""A realistic chart of accounts for a Canadian trades business.

Kept separate from the generator so ForgeBench cases, the QBO mapper and the
report renderer all agree on account identity and classification.
"""

from __future__ import annotations

from ..canonical.enums import AccountSubtype as S
from ..canonical.enums import AccountType as T
from ..canonical.models import Account

# (number, name, type, subtype, is_control_account)
TRADES_COA: tuple[tuple[str, str, T, S, bool], ...] = (
    ("1000", "Chequing - Operating", T.ASSET, S.BANK, True),
    ("1010", "Savings - Tax Reserve", T.ASSET, S.BANK, True),
    ("1050", "Undeposited Funds", T.ASSET, S.UNDEPOSITED_FUNDS, False),
    ("1200", "Accounts Receivable", T.ASSET, S.ACCOUNTS_RECEIVABLE, True),
    ("1300", "Prepaid Insurance", T.ASSET, S.PREPAID_EXPENSE, False),
    ("1400", "Work in Progress", T.ASSET, S.WORK_IN_PROGRESS, False),
    ("1500", "Equipment", T.ASSET, S.FIXED_ASSET, False),
    ("1510", "Vehicles", T.ASSET, S.FIXED_ASSET, False),
    ("1590", "Accumulated Depreciation", T.ASSET, S.ACCUMULATED_DEPRECIATION, False),
    ("2000", "Accounts Payable", T.LIABILITY, S.ACCOUNTS_PAYABLE, True),
    ("2100", "Visa - Operating Card", T.LIABILITY, S.CREDIT_CARD, True),
    ("2200", "GST/HST Payable", T.LIABILITY, S.SALES_TAX_PAYABLE, False),
    ("2300", "Payroll Liabilities", T.LIABILITY, S.PAYROLL_LIABILITY, False),
    ("2400", "Accrued Liabilities", T.LIABILITY, S.ACCRUED_LIABILITY, False),
    ("2500", "Customer Deposits", T.LIABILITY, S.DEFERRED_REVENUE, False),
    ("2700", "Equipment Loan", T.LIABILITY, S.LOAN_PAYABLE, False),
    ("3000", "Common Shares", T.EQUITY, S.COMMON_STOCK, False),
    ("3100", "Retained Earnings", T.EQUITY, S.RETAINED_EARNINGS, False),
    ("3200", "Owner Draws", T.EQUITY, S.OWNER_DRAWS, False),
    ("4000", "Hardscape Revenue", T.REVENUE, S.SALES_REVENUE, False),
    ("4010", "Snow Removal Revenue", T.REVENUE, S.SALES_REVENUE, False),
    ("4020", "Maintenance Revenue", T.REVENUE, S.SALES_REVENUE, False),
    ("4900", "Other Income", T.REVENUE, S.OTHER_INCOME, False),
    ("5000", "Materials", T.EXPENSE, S.MATERIALS, False),
    ("5100", "Subcontractors", T.EXPENSE, S.SUBCONTRACTOR, False),
    ("5200", "Direct Labour", T.EXPENSE, S.DIRECT_LABOUR, False),
    ("5300", "Equipment Rental", T.EXPENSE, S.EQUIPMENT_RENTAL, False),
    ("6000", "Wages and Salaries", T.EXPENSE, S.PAYROLL_EXPENSE, False),
    ("6010", "Employer Payroll Taxes", T.EXPENSE, S.PAYROLL_EXPENSE, False),
    ("6100", "Vehicle and Fuel", T.EXPENSE, S.VEHICLE_EXPENSE, False),
    ("6200", "Insurance", T.EXPENSE, S.OPERATING_EXPENSE, False),
    ("6300", "Rent - Yard and Shop", T.EXPENSE, S.OPERATING_EXPENSE, False),
    ("6400", "Office and Admin", T.EXPENSE, S.OPERATING_EXPENSE, False),
    ("6500", "Professional Fees", T.EXPENSE, S.OPERATING_EXPENSE, False),
    ("6600", "Repairs and Maintenance", T.EXPENSE, S.OPERATING_EXPENSE, False),
    ("6700", "Advertising and Marketing", T.EXPENSE, S.OPERATING_EXPENSE, False),
    ("6750", "Meals and Entertainment", T.EXPENSE, S.OPERATING_EXPENSE, False),
    ("6800", "Depreciation Expense", T.EXPENSE, S.DEPRECIATION_EXPENSE, False),
    ("7000", "Interest Expense", T.EXPENSE, S.INTEREST_EXPENSE, False),
    ("7100", "Bank Charges", T.EXPENSE, S.OPERATING_EXPENSE, False),
)


def build_chart(entity_id: str, currency: str = "CAD") -> dict[str, Account]:
    """Return the chart keyed by account number, which doubles as the id."""
    out: dict[str, Account] = {}
    for number, name, type_, subtype, control in TRADES_COA:
        out[number] = Account(
            account_id=number,
            entity_id=entity_id,
            number=number,
            name=name,
            type=type_,
            subtype=subtype,
            currency=currency,
            is_control_account=control,
        )
    return out


# Convenient aliases used throughout the generator and the controls.
BANK = "1000"
TAX_SAVINGS = "1010"
UNDEPOSITED = "1050"
AR = "1200"
PREPAID_INSURANCE = "1300"
WIP = "1400"
EQUIPMENT = "1500"
VEHICLES = "1510"
ACCUM_DEP = "1590"
AP = "2000"
VISA = "2100"
HST = "2200"
PAYROLL_LIAB = "2300"
ACCRUED = "2400"
CUSTOMER_DEPOSITS = "2500"
LOAN = "2700"
COMMON_SHARES = "3000"
RETAINED_EARNINGS = "3100"
OWNER_DRAWS = "3200"
REV_HARDSCAPE = "4000"
REV_SNOW = "4010"
REV_MAINTENANCE = "4020"
OTHER_INCOME = "4900"
MATERIALS = "5000"
SUBCONTRACTORS = "5100"
DIRECT_LABOUR = "5200"
EQUIPMENT_RENTAL = "5300"
WAGES = "6000"
PAYROLL_TAXES = "6010"
VEHICLE = "6100"
INSURANCE = "6200"
RENT = "6300"
OFFICE = "6400"
PROFESSIONAL = "6500"
REPAIRS = "6600"
ADVERTISING = "6700"
MEALS = "6750"
DEPRECIATION = "6800"
INTEREST = "7000"
BANK_CHARGES = "7100"
