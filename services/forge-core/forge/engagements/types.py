"""Engagement types: what kind of work a letter describes, and what each kind needs.

A type carries the services it implies (which set the controls, workflows
and job plan), the documents to request from the client before work starts,
the questions to raise, and the facts schema that gives the type its own
computed plan. Detection is by keyword score over the letter; a letter can
carry more than one type, and the first is primary.
"""

from __future__ import annotations

from dataclasses import dataclass

from .letter import EngagementLetter

__all__ = ["DocumentRequest", "EngagementType", "ENGAGEMENT_TYPES", "detect_types"]


@dataclass(frozen=True)
class DocumentRequest:
    kind: str
    description: str
    why: str


@dataclass(frozen=True)
class EngagementType:
    key: str
    label: str
    keywords: tuple[str, ...]
    services: tuple[str, ...]
    facts_schema: str | None
    document_requests: tuple[DocumentRequest, ...]
    questions: tuple[str, ...]
    workflows: tuple[str, ...]


ENGAGEMENT_TYPES: dict[str, EngagementType] = {
    t.key: t for t in (
        EngagementType(
            "development_rental", "Development to rental (construction, self-supply, rebates, rental operations)",
            ("self-supply", "rental rebate", "nrrp", "pbrh", "construction accounting", "fourplex", "triplex", "duplex", "garden suite",
             "construction loan", "purpose-built", "development", "builder"),
            ("project_accounting", "rental_property", "hst", "cfo_advisory"), "realestate",
            (
                DocumentRequest("engagement_letter", "Signed engagement letter", "Scope and authorised phases"),
                DocumentRequest("purchase_agreement", "Purchase agreement and closing statement for the land", "Adjusted cost base"),
                DocumentRequest("tax_return", "Most recent Canadian personal returns and notices of assessment", "Filing history and residency position"),
                DocumentRequest("identification", "Proof of Canadian citizenship or permanent residence for each owner", "Speculation taxes and purchase rules"),
                DocumentRequest("gst_account", "GST/HST account number, filing frequency and CRA access (authorise the firm)", "Credits, outstanding returns, self-supply reporting"),
                DocumentRequest("bank_statement", "Project bank account statements from the first construction payment", "Books and reconciliation"),
                DocumentRequest("loan_commitment", "Construction loan and facility commitment letters with rate, limit and draw terms", "Financing schedules, capitalised interest"),
                DocumentRequest("appraisal", "Appraisal(s): completed value and, separately, land value at the change of use", "Self-supply FMV and the deemed disposition"),
                DocumentRequest("permit", "Building permit, site plan, unit count and floor areas per unit", "Rebate per unit, start of construction"),
                DocumentRequest("budget", "Construction budget by trade and the contracts signed to date", "Cost to complete and draws"),
                DocumentRequest("invoice", "All construction invoices and proof of payment to date", "Input tax credits, holdbacks"),
                DocumentRequest("insurance", "Builder's risk insurance policy", "Soft cost capitalisation"),
                DocumentRequest("property_tax", "Property tax bills since construction began", "Land carrying costs"),
                DocumentRequest("residency", "Foreign residence evidence: address, tax residency certificate, date of departure", "Departure filing and treaty position"),
            ),
            ("Are both owners Canadian citizens or permanent residents?",
             "Which owner(s) will hold the GST/HST registration for the build: one, both, or the co-ownership as a partnership?",
             "Is any unit intended for family occupancy or for sale within the first year after completion?",
             "Who will act as the Canadian agent for rent and withholding once the property is tenanted?",
             "What was the land worth on the date construction for rental began (appraisal)?",
             "Has an advisor in the country of residence reviewed the Canadian rental structure?"),
            ("development", "tax_readiness", "cash_forecast", "rental"),
        ),
        EngagementType(
            "rental_property", "Rental property accounting and non-resident administration",
            ("rental income", "rental accounting", "property manager", "nr4", "section 216", "nr6", "tenant"),
            ("rental_property", "hst"), "realestate",
            (
                DocumentRequest("engagement_letter", "Signed engagement letter", "Scope"),
                DocumentRequest("lease", "Current leases and the rent roll", "Income and deposits"),
                DocumentRequest("bank_statement", "Rental bank account statements", "Books and reconciliation"),
                DocumentRequest("loan_statement", "Mortgage statements", "Interest and principal"),
                DocumentRequest("property_tax", "Property tax and insurance bills", "Operating expenses"),
                DocumentRequest("tax_return", "Prior-year rental schedules and returns", "Opening UCC and carry-forwards"),
            ),
            ("Is the owner a Canadian resident for tax purposes?", "Who collects rent and remits withholding?"),
            ("rental", "tax_readiness"),
        ),
        EngagementType(
            "monthly_bookkeeping", "Monthly bookkeeping",
            ("bookkeeping", "monthly books", "reconcile", "transaction coding", "quickbooks"),
            ("bookkeeping", "hst"), None,
            (
                DocumentRequest("engagement_letter", "Signed engagement letter", "Scope"),
                DocumentRequest("accounting_access", "QuickBooks Online accountant access", "The ledger"),
                DocumentRequest("bank_statement", "Bank and credit card statements for every business account, twelve months", "Reconciliation and opening balances"),
                DocumentRequest("tax_return", "Last filed corporate return and financial statements", "Opening balances and tax accounts"),
                DocumentRequest("gst_account", "GST/HST and payroll account numbers and filing frequencies", "Compliance calendar"),
                DocumentRequest("loan_statement", "Loan, lease and line of credit statements", "Debt schedules"),
            ),
            ("Which accounts are business and which are mixed with personal use?", "Who approves bills and who has bank signing authority?"),
            ("continuous_controller", "documents", "close"),
        ),
        EngagementType(
            "cleanup", "Books cleanup and catch-up",
            ("cleanup", "catch-up", "catch up", "behind", "reconstruct", "historical"),
            ("cleanup", "bookkeeping"), None,
            (
                DocumentRequest("bank_statement", "Every bank and credit card statement for the catch-up period", "Reconstruction"),
                DocumentRequest("tax_return", "Last filed return and financial statements", "Opening balances"),
                DocumentRequest("accounting_access", "Access to the existing file, however bad", "Starting point"),
            ),
            ("What was the last period that was reconciled and filed?",),
            ("cleanup", "continuous_controller"),
        ),
        EngagementType(
            "sales_tax", "GST/HST filing",
            ("gst/hst", "hst return", "gst return", "sales tax", "input tax credit"),
            ("hst",), None,
            (DocumentRequest("gst_account", "GST/HST account number, filing frequency, last return filed", "Compliance"),),
            ("Is the business on the quick method or the regular method?",),
            ("tax_readiness",),
        ),
        EngagementType(
            "payroll", "Payroll processing",
            ("payroll", "cpp", "ei ", "t4", "source deduction", "remittance"),
            ("payroll",), None,
            (DocumentRequest("payroll_records", "Employee list with TD1s, pay frequency, prior remittances year to date", "Payroll setup"),),
            ("What is the CRA remitter type and frequency?",),
            ("payroll",),
        ),
        EngagementType(
            "year_end_corporate", "Year-end and corporate tax",
            ("t2", "year-end", "year end", "corporate tax", "financial statements", "notice to reader", "compilation"),
            ("year_end", "corporate_tax"), None,
            (
                DocumentRequest("tax_return", "Prior-year T2, financial statements and notices of assessment", "Carry-forwards and opening balances"),
                DocumentRequest("bank_statement", "Year-end bank, loan and credit card statements", "Cut-off"),
                DocumentRequest("fixed_asset", "Asset purchases and disposals in the year", "CCA"),
            ),
            ("Were there any related-party transactions or shareholder loans in the year?",),
            ("year_end", "close"),
        ),
        EngagementType(
            "cfo_advisory", "Virtual CFO and planning",
            ("cfo", "advisory", "forecast", "budget", "kpi", "strategy", "cash flow"),
            ("cfo_advisory", "management_rep", "cash_forecast"), None,
            (DocumentRequest("accounting_access", "Accounting file access and the last twelve months of statements", "Baseline"),),
            ("What are the three decisions the owner needs the numbers for this quarter?",),
            ("cfo_brief", "cash_forecast"),
        ),
        EngagementType(
            "personal_tax", "Personal tax and residency",
            ("t1", "personal tax", "departure", "residency", "non-resident", "emigra"),
            ("corporate_tax",), None,
            (
                DocumentRequest("tax_return", "Prior personal returns and notices of assessment", "History"),
                DocumentRequest("residency", "Dates of departure or arrival, foreign address and tax residency evidence", "Residency"),
            ),
            ("Was a departure date reported in the year of emigration?",),
            ("tax_readiness",),
        ),
    )
}


def detect_types(letter: EngagementLetter) -> list[tuple[str, int]]:
    """Types ranked by keyword hits over the letter's own words. Primary first."""
    text = letter.all_text.lower()
    scored = []
    for key, t in ENGAGEMENT_TYPES.items():
        score = sum(text.count(k) for k in t.keywords)
        if score:
            scored.append((key, score))
    scored.sort(key=lambda s: -s[1])
    if not scored:
        return []
    top = scored[0][1]
    # A stray word is not a second engagement: keep a type only when it is a real share of the letter.
    return [(k, sc) for k, sc in scored if sc >= 2 and sc * 4 >= top]
