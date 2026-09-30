"""Real-estate development and rental engagements.

A non-resident building a fourplex is a different problem from a landscaping
contractor: the money is in GST/HST self-supply and rebates, in the ownership
structure, in departure and rental filings, and in construction accounting
that a lender and the CRA will both read. Everything here is deterministic
and cites the rule it applies; the plan it renders says which numbers are
computed, which are the client's own facts, and which need a professional
to confirm before anyone acts.
"""

from .facts import (
    Complex,
    EngagementFacts,
    Facility,
    load_facts,
)
from .finance_pack import FinancePack, owner_finance_pack, render_finance_pack
from .hst import (
    HstPosition,
    RebateResult,
    federal_nrrp_rebate,
    hst_position,
    ontario_nrrp_rebate,
    pbrh_eligibility,
    rebate_for_complex,
    self_supply_tax,
)
from .ownership import (
    OwnershipScenario,
    non_resident_speculation_tax,
    ontario_land_transfer_tax,
    ownership_scenarios,
    section_216_tax,
    toronto_land_transfer_tax,
)
from .plan import render_action_plan
from .principal_residence import change_of_use, principal_residence_exemption
from .project import ProjectStatus, cash_requirement_schedule, project_status
from .rental import (
    RentalProForma,
    cca_claim_limited_by_rental_income,
    rental_pro_forma,
    section_216_withholding,
)
from .residency import departure_review, non_resident_rental_calendar

__all__ = [
    "Complex", "EngagementFacts", "Facility", "load_facts",
    "FinancePack", "owner_finance_pack", "render_finance_pack",
    "HstPosition", "RebateResult", "federal_nrrp_rebate", "hst_position", "ontario_nrrp_rebate",
    "pbrh_eligibility", "rebate_for_complex", "self_supply_tax",
    "OwnershipScenario", "non_resident_speculation_tax", "ontario_land_transfer_tax", "ownership_scenarios",
    "section_216_tax", "toronto_land_transfer_tax",
    "render_action_plan",
    "change_of_use", "principal_residence_exemption",
    "ProjectStatus", "cash_requirement_schedule", "project_status",
    "RentalProForma", "cca_claim_limited_by_rental_income", "rental_pro_forma", "section_216_withholding",
    "departure_review", "non_resident_rental_calendar",
]
