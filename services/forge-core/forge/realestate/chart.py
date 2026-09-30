"""The construction accounting chart for a development project, and its rental successor.

Cost categories follow what the lender, the appraiser and the CRA each need
to read: land apart from building, hard costs apart from soft costs, interest
capitalised during construction, recoverable HST kept out of cost, holdbacks
kept out of payables. Each account carries the CCA class it will land in.
"""

from __future__ import annotations

from ..canonical.enums import AccountSubtype, AccountType
from ..canonical.models import Account

__all__ = ["DEVELOPMENT_COA", "build_development_chart", "DEVELOPMENT_ACCOUNTS"]

# number, name, type, subtype, control, cca_class, note
DEVELOPMENT_COA: tuple[tuple[str, str, AccountType, AccountSubtype, bool, str, str], ...] = (
    ("1000", "Project bank account", AccountType.ASSET, AccountSubtype.BANK, False, "", "One account for the project; nothing personal through it"),
    ("1100", "GST/HST recoverable (input tax credits)", AccountType.ASSET, AccountSubtype.OTHER_CURRENT_ASSET, False, "", "HST on construction inputs while registered; cleared by each return"),
    ("1200", "Rent receivable", AccountType.ASSET, AccountSubtype.ACCOUNTS_RECEIVABLE, True, "", "Phase 4"),
    ("1300", "Prepaid insurance and deposits", AccountType.ASSET, AccountSubtype.PREPAID_EXPENSE, False, "", "Builder's risk premium amortised over the build"),
    ("1500", "Land", AccountType.ASSET, AccountSubtype.FIXED_ASSET, False, "n/a", "Purchase price, acquisition costs, land transfer tax; never depreciated"),
    ("1510", "Land improvements (Class 17)", AccountType.ASSET, AccountSubtype.FIXED_ASSET, False, "17", "Paving, walkways"),
    ("1600", "Building under construction: hard costs", AccountType.ASSET, AccountSubtype.WORK_IN_PROGRESS, False, "1", "Trades, materials, site work; net of recoverable HST"),
    ("1610", "Building under construction: soft costs", AccountType.ASSET, AccountSubtype.WORK_IN_PROGRESS, False, "1", "Architect, engineering, permits, development charges, legal, appraisal, builder's risk insurance (s.18(3.1))"),
    ("1620", "Building under construction: capitalised interest", AccountType.ASSET, AccountSubtype.WORK_IN_PROGRESS, False, "1", "Construction loan interest and fees during the build (s.18(3.1))"),
    ("1630", "Building under construction: garden suite", AccountType.ASSET, AccountSubtype.WORK_IN_PROGRESS, False, "1", "Costs attributable to the detached suite, kept separate for its own rebate and FMV"),
    ("1650", "Building (Class 1), on completion", AccountType.ASSET, AccountSubtype.FIXED_ASSET, False, "1", "Transferred from 1600 to 1630 at substantial completion"),
    ("1660", "Appliances and furnishings (Class 8)", AccountType.ASSET, AccountSubtype.FIXED_ASSET, False, "8", ""),
    ("1690", "Accumulated CCA", AccountType.ASSET, AccountSubtype.ACCUMULATED_DEPRECIATION, False, "", ""),
    ("2000", "Accounts payable: trades and suppliers", AccountType.LIABILITY, AccountSubtype.ACCOUNTS_PAYABLE, True, "", ""),
    ("2200", "GST/HST payable (self-supply and rental period)", AccountType.LIABILITY, AccountSubtype.SALES_TAX_PAYABLE, False, "", "Self-assessed tax at completion, net of rebates"),
    ("2300", "Construction holdbacks payable", AccountType.LIABILITY, AccountSubtype.OTHER_CURRENT_LIABILITY, False, "", "10% statutory holdback per trade (Ontario Construction Act), released 60 days after substantial performance"),
    ("2400", "Accrued liabilities", AccountType.LIABILITY, AccountSubtype.ACCRUED_LIABILITY, False, "", ""),
    ("2500", "Tenant deposits held", AccountType.LIABILITY, AccountSubtype.OTHER_CURRENT_LIABILITY, False, "", "Phase 4: last month's rent deposits"),
    ("2600", "Non-resident withholding payable (Part XIII)", AccountType.LIABILITY, AccountSubtype.OTHER_CURRENT_LIABILITY, False, "", "Phase 4: 25% of net rent under NR6, remitted by the 15th"),
    ("2700", "Construction loan", AccountType.LIABILITY, AccountSubtype.LOAN_PAYABLE, False, "", "Draws and capitalised interest; agrees to the lender statement"),
    ("2710", "Construction facility", AccountType.LIABILITY, AccountSubtype.LOAN_PAYABLE, False, "", "Second facility, same treatment"),
    ("2750", "Permanent mortgage", AccountType.LIABILITY, AccountSubtype.LOAN_PAYABLE, False, "", "Takeout financing at completion"),
    ("3000", "Owner contributions: co-owner A", AccountType.EQUITY, AccountSubtype.OWNER_CONTRIBUTION, False, "", "Cash in and costs paid personally, by owner"),
    ("3010", "Owner contributions: co-owner B", AccountType.EQUITY, AccountSubtype.OWNER_CONTRIBUTION, False, "", ""),
    ("3100", "Opening equity", AccountType.EQUITY, AccountSubtype.RETAINED_EARNINGS, False, "", "Land at cost brought in at the start of the project"),
    ("3200", "Owner draws", AccountType.EQUITY, AccountSubtype.OWNER_DRAWS, False, "", ""),
    ("4000", "Rental income", AccountType.REVENUE, AccountSubtype.SALES_REVENUE, False, "", "Phase 4, by unit; exempt from HST"),
    ("4100", "Other income (parking, laundry)", AccountType.REVENUE, AccountSubtype.OTHER_INCOME, False, "", ""),
    ("6100", "Property tax", AccountType.EXPENSE, AccountSubtype.OPERATING_EXPENSE, False, "", "During construction: land carrying cost, restricted by s.18(2)"),
    ("6200", "Insurance", AccountType.EXPENSE, AccountSubtype.OPERATING_EXPENSE, False, "", ""),
    ("6300", "Utilities", AccountType.EXPENSE, AccountSubtype.OPERATING_EXPENSE, False, "", ""),
    ("6400", "Repairs and maintenance", AccountType.EXPENSE, AccountSubtype.OPERATING_EXPENSE, False, "", "Current repairs only; improvements go to 1650"),
    ("6500", "Property management fees", AccountType.EXPENSE, AccountSubtype.OPERATING_EXPENSE, False, "", "HST on these is a cost, not recoverable, once rental is exempt"),
    ("6600", "Professional fees", AccountType.EXPENSE, AccountSubtype.OPERATING_EXPENSE, False, "", "Accounting and tax compliance"),
    ("6700", "Advertising and leasing", AccountType.EXPENSE, AccountSubtype.OPERATING_EXPENSE, False, "", ""),
    ("6800", "Capital cost allowance", AccountType.EXPENSE, AccountSubtype.DEPRECIATION_EXPENSE, False, "", "Limited to net rental income"),
    ("7000", "Mortgage interest", AccountType.EXPENSE, AccountSubtype.INTEREST_EXPENSE, False, "", "Post-completion only; construction interest is capitalised"),
    ("7100", "Bank charges", AccountType.EXPENSE, AccountSubtype.OPERATING_EXPENSE, False, "", ""),
)

DEVELOPMENT_ACCOUNTS: dict[str, str] = {
    "bank": "1000", "hst_recoverable": "1100", "land": "1500", "hard_costs": "1600", "soft_costs": "1610",
    "capitalised_interest": "1620", "garden_suite": "1630", "building": "1650", "ap": "2000", "hst_payable": "2200",
    "holdback_payable": "2300", "construction_loan": "2700", "construction_facility": "2710",
    "owner_a": "3000", "owner_b": "3010", "opening_equity": "3100", "rental_income": "4000",
    "property_tax": "6100", "insurance": "6200", "professional_fees": "6600", "interest_expense": "7000",
}


def build_development_chart(entity_id: str, currency: str = "CAD") -> dict[str, Account]:
    out: dict[str, Account] = {}
    for number, name, type_, subtype, control, _cca, _note in DEVELOPMENT_COA:
        out[number] = Account(account_id=number, entity_id=entity_id, number=number, name=name, type=type_, subtype=subtype,
                              currency=currency, is_control_account=control)
    return out


def chart_for_setup() -> list[dict[str, str]]:
    """The chart as a list ready to key into QuickBooks or to hand to a bookkeeper."""
    return [{"number": n, "name": name, "type": t.value, "detail_type": s.value, "cca_class": cca, "note": note}
            for n, name, t, s, _c, cca, note in DEVELOPMENT_COA]
