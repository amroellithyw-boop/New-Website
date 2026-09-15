"""The client profile: where hyper-personalisation actually lives.

Every legacy intake field is preserved here, and the thing that makes it more
than a record is :meth:`ClientProfile.to_policy`. One profile produces the
policy dictionary that every control, benchmark, tax calculation, review chain
and report reads. Change the province and the tax controls re-anchor. Change the
revenue and the benchmark tier shifts. Turn on a service and its workflows and
controls come into scope. Nothing is personalised by hand, so nothing is
personalised inconsistently.

The profile is deliberately a plain dataclass with a JSON round-trip, so it can
be stored in any database, edited by hand in an emergency, and diffed in a
review.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from datetime import date
from decimal import Decimal
from typing import Any

from ..industry import IndustryPack, pack_for
from ..money import Money
from .tax_regions import SalesTaxRegime, sales_tax_for

__all__ = ["ServicePackage", "SERVICES", "ClientProfile", "ClientPolicy"]


@dataclass(frozen=True)
class ServicePackage:
    key: str
    label: str
    description: str
    workflows: tuple[str, ...]
    control_categories: tuple[str, ...]


# Carried over from the legacy PF_SERVICES list, with each service mapped to the
# ForgeOS workflows and control categories it brings into scope.
SERVICES: dict[str, ServicePackage] = {
    s.key: s
    for s in (
        ServicePackage("bookkeeping", "Monthly Bookkeeping",
                       "Full-cycle bookkeeping, transaction coding, bank reconciliation",
                       ("continuous_controller", "documents"),
                       ("integrity", "classification", "reconciliation", "period control", "variance")),
        ServicePackage("hst", "HST/GST Filing", "Sales tax preparation and CRA filing",
                       ("tax_readiness",), ("tax",)),
        ServicePackage("payroll", "Payroll Processing",
                       "Payroll, CPP/EI remittances, T4 slips", ("payroll",), ("payroll",)),
        ServicePackage("corporate_tax", "Corporate Tax Return (T2)",
                       "Annual T2 preparation and filing", ("year_end",), ("tax", "fixed assets")),
        ServicePackage("management_rep", "Management Reporting",
                       "Monthly statements, cash flow, KPI dashboard", ("cfo_brief",), ("benchmark",)),
        ServicePackage("cfo_advisory", "Virtual CFO Advisory",
                       "Monthly CFO meeting, planning, cash forecasting",
                       ("cfo_brief", "cash_forecast"), ("benchmark", "treasury", "planning", "risk")),
        ServicePackage("job_costing", "Job Costing & WIP",
                       "Per-job profitability, WIP, holdbacks", ("job_costing",), ("benchmark",)),
        ServicePackage("accounts_rec", "Accounts Receivable",
                       "Invoice tracking, collections, aging", ("collections",), ("accounts receivable",)),
        ServicePackage("accounts_pay", "Accounts Payable",
                       "Bill management, vendor payments, aging", ("payables",), ("accounts payable",)),
        ServicePackage("wsib", "WSIB Filings", "Premium calculation and filings", ("wsib",), ("payroll", "benchmark")),
        ServicePackage("year_end", "Year-End Working Papers",
                       "Complete year-end file for the external CPA", ("year_end",), ("close", "fixed assets", "debt")),
        ServicePackage("cash_forecast", "Cash Flow Forecasting",
                       "13-week rolling cash forecast", ("cash_forecast",), ("treasury",)),
        ServicePackage("cleanup", "Books Cleanup / Catch-Up",
                       "Historical cleanup for prior periods", ("cleanup",), ("integrity", "period control")),
    )
}


@dataclass
class ClientProfile:
    """Everything known about a client that should shape how ForgeOS treats them."""

    client_id: str
    business_name: str
    owner_name: str = ""
    legal_structure: str = "CCPC"
    province: str = "ON"
    naics_code: str | None = None
    industry_label: str = ""
    fiscal_year_end_month: int = 12
    fiscal_year_end_day: int = 31
    accounting_standard: str = "ASPE"
    currency: str = "CAD"

    # Registrations
    business_number: str | None = None
    hst_registered: bool = True
    hst_number: str | None = None
    wsib_number: str | None = None
    incorporation_date: date | None = None

    # Size and operations
    annual_revenue_estimate: Money | None = None
    monthly_transactions_estimate: int | None = None
    employees_full_time: int = 0
    employees_part_time: int = 0
    employees_seasonal: int = 0
    uses_subcontractors: bool = False
    seasonal: bool = False
    seasonal_details: str = ""
    operates_from_home: bool = False
    locations: int = 1

    # Financial state at intake
    current_accounting_software: str = "QuickBooks Online"
    books_current: bool = True
    months_behind: int = 0
    bank_name: str = ""
    bank_accounts: int = 1
    credit_cards: int = 0
    outstanding_loans: bool = False
    loan_details: str = ""
    owner_compensation_method: str = "salary"  # salary | dividends | draws | mixed
    owner_compensation_amount: Money | None = None
    has_external_cpa: bool = False
    external_cpa_name: str = ""
    last_filed_t2: str | None = None
    last_filed_hst: str | None = None

    # Engagement
    services: tuple[str, ...] = ("bookkeeping",)
    tier: str = "growth"
    monthly_fee: Money | None = None

    # Business intelligence from the owner
    main_customers: str = ""
    main_vendors: str = ""
    main_expenses: str = ""
    pain_points: str = ""
    goals_12_months: str = ""
    goals_3_years: str = ""
    revenue_target: Money | None = None
    pricing_model: str = ""
    biggest_financial_risk: str = ""
    growth_plans: str = ""

    # Operator-set policy
    authorised_posters: tuple[str, ...] = ("sync", "payroll_sync", "close_process", "migration")
    capitalisation_threshold: Money | None = None
    wsib_account_id: str | None = None
    seasonal_accounts: tuple[str, ...] = ()
    covenants: dict[str, str] = field(default_factory=dict)
    ai_context_notes: str = ""
    """Free-text facts the operator wants every analysis to know. Reaches a
    model as data inside the evidence packet, never as instructions."""

    # ---- derived -----------------------------------------------------------

    @property
    def industry(self) -> IndustryPack | None:
        return pack_for(self.naics_code)

    @property
    def sales_tax(self) -> SalesTaxRegime:
        return sales_tax_for(self.province)

    @property
    def headcount(self) -> int:
        return self.employees_full_time + self.employees_part_time + self.employees_seasonal

    @property
    def size_tier_label(self) -> str:
        pack = self.industry
        if pack and self.annual_revenue_estimate:
            tier = pack.tier_for(self.annual_revenue_estimate)
            if tier:
                return tier.label
        rev = self.annual_revenue_estimate
        if rev is None:
            return "unknown"
        units = rev.minor_units
        if units < 10_000_000:
            return "start-up"
        if units < 50_000_000:
            return "micro"
        if units < 200_000_000:
            return "small"
        if units < 1_000_000_000:
            return "mid-size"
        return "upper mid-market"

    @property
    def service_packages(self) -> list[ServicePackage]:
        return [SERVICES[s] for s in self.services if s in SERVICES]

    @property
    def workflows_in_scope(self) -> tuple[str, ...]:
        out: list[str] = []
        for pkg in self.service_packages:
            for w in pkg.workflows:
                if w not in out:
                    out.append(w)
        return tuple(out)

    @property
    def control_categories_in_scope(self) -> tuple[str, ...]:
        out: list[str] = []
        for pkg in self.service_packages:
            for c in pkg.control_categories:
                if c not in out:
                    out.append(c)
        # Integrity is never out of scope: nothing else means anything without it.
        for always in ("integrity", "reconciliation"):
            if always not in out:
                out.append(always)
        return tuple(out)

    @property
    def needs_deferred_revenue_control(self) -> bool:
        pack = self.industry
        return bool(pack and pack.deferred_revenue) or self.seasonal

    @property
    def files_t5018(self) -> bool:
        """Construction subcontractor reporting applies to construction NAICS codes."""
        return bool(self.naics_code and self.naics_code.startswith("23")) and self.uses_subcontractors

    def fiscal_year_start(self, for_date: date) -> date:
        """First day of the fiscal year containing ``for_date``."""
        fye = date(for_date.year, self.fiscal_year_end_month, self.fiscal_year_end_day)
        if for_date <= fye:
            start_year = for_date.year - 1 if not (self.fiscal_year_end_month == 12 and self.fiscal_year_end_day == 31) else for_date.year
        else:
            start_year = for_date.year
        if self.fiscal_year_end_month == 12 and self.fiscal_year_end_day == 31:
            return date(for_date.year, 1, 1)
        start = fye.replace(year=start_year)
        m = start.month % 12 + 1
        y = start.year + (1 if start.month == 12 else 0)
        from datetime import timedelta
        return date(y, m, 1) if start.day >= 28 else start + timedelta(days=1)

    # ---- the bridge to the engine ------------------------------------------

    def to_policy(self) -> ClientPolicy:
        """Turn the profile into the policy every control reads.

        This is the single point of personalisation. A control never looks at
        the profile; it looks at the policy, and the policy was built from the
        profile. That keeps fifty-five controls consistent with each other.
        """
        pack = self.industry
        threshold = self.capitalisation_threshold or Money.from_decimal("2500.00", self.currency)
        seasonal_accounts = tuple(self.seasonal_accounts)
        return ClientPolicy(
            client_id=self.client_id,
            naics_code=self.naics_code,
            province=self.province,
            sales_tax_rate=self.sales_tax.rate,
            sales_tax_label=self.sales_tax.label,
            hst_registered=self.hst_registered,
            authorised_posters=frozenset(self.authorised_posters),
            capitalisation_threshold=threshold,
            depreciation_rate=Decimal("0.15"),
            wsib_account_id=self.wsib_account_id,
            wsib_rate=pack.wsib_rate if pack else None,
            seasonal_accounts=seasonal_accounts,
            deferred_revenue_expected=self.needs_deferred_revenue_control,
            covenants=dict(self.covenants),
            control_categories=self.control_categories_in_scope,
            workflows=self.workflows_in_scope,
            size_tier=self.size_tier_label,
            industry_label=pack.label if pack else self.industry_label,
            revenue_target=self.revenue_target,
            files_t5018=self.files_t5018,
            owner_compensation_method=self.owner_compensation_method,
            context_notes=self.ai_context_notes,
        )

    # ---- persistence -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if isinstance(value, Money):
                out[f.name] = str(value.to_decimal())
            elif isinstance(value, date):
                out[f.name] = value.isoformat()
            elif isinstance(value, tuple):
                out[f.name] = list(value)
            else:
                out[f.name] = value
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ClientProfile:
        kwargs: dict[str, Any] = {}
        money_fields = {"annual_revenue_estimate", "owner_compensation_amount", "monthly_fee",
                        "revenue_target", "capitalisation_threshold"}
        tuple_fields = {"services", "authorised_posters", "seasonal_accounts"}
        for f in fields(cls):
            if f.name not in data:
                continue
            value = data[f.name]
            if f.name in money_fields and value not in (None, ""):
                value = Money.from_decimal(str(value), data.get("currency", "CAD"))
            elif f.name == "incorporation_date" and value:
                value = date.fromisoformat(str(value))
            elif f.name in tuple_fields and value is not None:
                value = tuple(value)
            kwargs[f.name] = value
        return cls(**kwargs)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> ClientProfile:
        return cls.from_dict(json.loads(text))

    @classmethod
    def from_legacy_intake(cls, intake: Mapping[str, Any]) -> ClientProfile:
        """Build a profile from the legacy ProfitForge intake payload shape."""
        def money(key: str) -> Money | None:
            raw = intake.get(key)
            if raw in (None, "", "unknown"):
                return None
            cleaned = str(raw).replace("$", "").replace(",", "").strip()
            try:
                return Money.from_decimal(cleaned, "CAD")
            except Exception:  # noqa: BLE001 - free text from a form
                return None

        def yes(key: str) -> bool:
            return str(intake.get(key, "")).strip().lower() in ("yes", "true", "1", "y")

        fye = str(intake.get("fiscal_year_end", "December 31"))
        month_names = ["january", "february", "march", "april", "may", "june", "july",
                       "august", "september", "october", "november", "december"]
        fye_month, fye_day = 12, 31
        parts = fye.replace(",", "").split()
        if parts and parts[0].lower() in month_names:
            fye_month = month_names.index(parts[0].lower()) + 1
            fye_day = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 28

        province_raw = str(intake.get("province", "ON")).strip()
        province = province_raw.upper() if len(province_raw) == 2 else {
            "ontario": "ON", "quebec": "QC", "british columbia": "BC", "alberta": "AB",
            "manitoba": "MB", "saskatchewan": "SK", "nova scotia": "NS",
            "new brunswick": "NB", "newfoundland": "NL", "pei": "PE",
            "prince edward island": "PE",
        }.get(province_raw.lower(), "ON")

        return cls(
            client_id=str(intake.get("id") or intake.get("client_id") or "C-NEW"),
            business_name=str(intake.get("business_name") or intake.get("name") or "New Client"),
            owner_name=str(intake.get("owner_name", "")),
            legal_structure=str(intake.get("legal_structure", "CCPC")),
            province=province,
            naics_code=str(intake["naics_code"]) if intake.get("naics_code") else None,
            industry_label=str(intake.get("industry", "")),
            fiscal_year_end_month=fye_month,
            fiscal_year_end_day=fye_day,
            accounting_standard=str(intake.get("standard", "ASPE")),
            business_number=intake.get("business_number") or None,
            hst_registered=yes("hst_registered") if "hst_registered" in intake else True,
            hst_number=intake.get("hst_number") or None,
            wsib_number=intake.get("wsib_number") or None,
            annual_revenue_estimate=money("annual_revenue_estimate"),
            monthly_transactions_estimate=int(intake["monthly_transactions_estimate"])
            if str(intake.get("monthly_transactions_estimate", "")).isdigit() else None,
            employees_full_time=int(intake.get("employees_ft") or 0),
            employees_part_time=int(intake.get("employees_pt") or 0),
            employees_seasonal=int(intake.get("employees_seasonal") or 0),
            uses_subcontractors=yes("subcontractors"),
            seasonal=bool(intake.get("seasonal_details")) or yes("seasonal"),
            seasonal_details=str(intake.get("seasonal_details", "")),
            operates_from_home=yes("operates_from_home"),
            current_accounting_software=str(intake.get("current_accounting_software", "QuickBooks Online")),
            books_current=str(intake.get("books_current", "current")).lower() in ("current", "yes", "true"),
            months_behind=int(intake.get("months_behind") or 0),
            bank_name=str(intake.get("bank_name", "")),
            bank_accounts=int(intake.get("bank_accounts") or 1),
            credit_cards=int(intake.get("credit_cards") or 0),
            outstanding_loans=str(intake.get("outstanding_loans", "none")).lower() not in ("none", "", "no"),
            loan_details=str(intake.get("loan_details", "")),
            owner_compensation_method=str(intake.get("owner_draws_salary", "salary")),
            owner_compensation_amount=money("owner_compensation_amount"),
            has_external_cpa=yes("has_cpa"),
            external_cpa_name=str(intake.get("cpa_name", "")),
            last_filed_t2=intake.get("last_filed_t2") or None,
            last_filed_hst=intake.get("last_filed_hst") or None,
            services=tuple(intake.get("services") or ("bookkeeping",)),
            tier=str(intake.get("tier", "growth")),
            monthly_fee=money("monthly_fee"),
            main_customers=str(intake.get("main_customers", "")),
            main_vendors=str(intake.get("main_vendors", "")),
            main_expenses=str(intake.get("main_expenses", "")),
            pain_points=str(intake.get("pain_points", "")),
            goals_12_months=str(intake.get("goals_12mo", "")),
            goals_3_years=str(intake.get("goals_3yr", "")),
            revenue_target=money("revenue_target"),
            pricing_model=str(intake.get("pricing_model", "")),
            biggest_financial_risk=str(intake.get("biggest_financial_risk", "")),
            growth_plans=str(intake.get("growth_plans", "")),
            ai_context_notes=str(intake.get("ai_context_notes", "")),
        )


@dataclass(frozen=True)
class ClientPolicy:
    """What the engine actually consumes. Built only by :meth:`ClientProfile.to_policy`."""

    client_id: str
    naics_code: str | None
    province: str
    sales_tax_rate: Decimal
    sales_tax_label: str
    hst_registered: bool
    authorised_posters: frozenset[str]
    capitalisation_threshold: Money
    depreciation_rate: Decimal
    wsib_account_id: str | None
    wsib_rate: Decimal | None
    seasonal_accounts: tuple[str, ...]
    deferred_revenue_expected: bool
    covenants: dict[str, str]
    control_categories: tuple[str, ...]
    workflows: tuple[str, ...]
    size_tier: str
    industry_label: str
    revenue_target: Money | None
    files_t5018: bool
    owner_compensation_method: str
    context_notes: str

    def as_rule_policy(self) -> dict[str, Any]:
        """The dictionary shape ``RuleContext.policy`` expects."""
        return {
            "client_id": self.client_id,
            "naics_code": self.naics_code,
            "province": self.province,
            "sales_tax_rate": self.sales_tax_rate,
            "sales_tax_label": self.sales_tax_label,
            "authorised_posters": set(self.authorised_posters),
            "capitalisation_threshold": self.capitalisation_threshold,
            "depreciation_rate": self.depreciation_rate,
            "wsib_account_id": self.wsib_account_id,
            "seasonal_accounts": self.seasonal_accounts,
            "covenants": dict(self.covenants),
            "control_categories": set(self.control_categories),
            "size_tier": self.size_tier,
            "industry_label": self.industry_label,
            "context_notes": self.context_notes,
        }

    def controls_in_scope(self, registry) -> list[str]:
        """Rule ids whose category the client's services bring into scope."""
        wanted = set(self.control_categories)
        return [r.rule_id for r in registry if r.category in wanted or r.category in ("integrity", "reconciliation")]
