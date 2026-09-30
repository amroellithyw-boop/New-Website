"""Ownership structures compared on the client's own numbers.

Land transfer tax, non-resident speculation tax, corporate versus personal
tax on rental income, and what each route requires. The comparison does not
decide; it puts the costs and the conditions side by side so the written
recommendation rests on arithmetic.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..money import Money
from ..payroll.rates import rates_for
from .facts import EngagementFacts

__all__ = [
    "OwnershipScenario", "ontario_land_transfer_tax", "toronto_land_transfer_tax", "non_resident_speculation_tax",
    "section_216_tax", "corporate_tax_on_rental", "ownership_scenarios",
]

# Marginal tiers shared by Ontario and Toronto. The 2.5% top tier applies only
# to land with one or two single-family residences; other property stays at 2%.
LTT_TIERS: tuple[tuple[str | None, Decimal], ...] = (
    ("55000.00", Decimal("0.005")),
    ("250000.00", Decimal("0.01")),
    ("400000.00", Decimal("0.015")),
    ("2000000.00", Decimal("0.02")),
)
LTT_TOP_RATE_ONE_OR_TWO_SFR = Decimal("0.025")
LTT_TOP_RATE_OTHER = Decimal("0.02")
NRST_ONTARIO = Decimal("0.25")
MNRST_TORONTO = Decimal("0.10")
NON_RESIDENT_SURTAX = Decimal("0.48")
ONTARIO_GENERAL_CORPORATE_RATE = Decimal("0.265")  # 15% federal + 11.5% Ontario; a non-CCPC gets no small business deduction
PART_XIII_DIVIDEND_ITALY = Decimal("0.15")  # Canada-Italy treaty rate for an individual shareholder
CORPORATE_COMPLIANCE_PER_YEAR = "3000.00"

SOURCES = (
    "Ontario Land Transfer Tax Act; Ontario Ministry of Finance, Calculating Land Transfer Tax",
    "City of Toronto Municipal Land Transfer Tax rates",
    "Ontario Non-Resident Speculation Tax (25%) and Toronto Municipal Non-Resident Speculation Tax (10%, from 1 January 2025)",
    "ITA s.216 election and the 48% non-resident surtax in lieu of provincial tax",
    "Canada-Italy income tax convention, dividend article",
    "ITA s.85 and s.116 on transfers by non-residents of taxable Canadian property",
)


def _marginal(value: Money, top_rate: Decimal) -> Money:
    cur = value.currency
    tax = Money.zero(cur)
    lower = Money.zero(cur)
    for upper_s, rate in LTT_TIERS:
        upper = Money.from_decimal(upper_s, cur)
        if value <= lower:
            break
        band = min(value, upper) - lower
        tax = tax + band.scale(rate)
        lower = upper
    if value > lower:
        tax = tax + (value - lower).scale(top_rate)
    return tax


def ontario_land_transfer_tax(value: Money, *, one_or_two_single_family: bool = False) -> Money:
    return _marginal(value, LTT_TOP_RATE_ONE_OR_TWO_SFR if one_or_two_single_family else LTT_TOP_RATE_OTHER)


def toronto_land_transfer_tax(value: Money, *, one_or_two_single_family: bool = False) -> Money:
    """Same tiers as the province. Toronto's graduated rates above $3,000,000
    apply only to one or two single-family residences and are not modelled;
    a value in that case is flagged by the caller."""
    return _marginal(value, LTT_TOP_RATE_ONE_OR_TWO_SFR if one_or_two_single_family else LTT_TOP_RATE_OTHER)


def non_resident_speculation_tax(value: Money, *, buyer_status: str, municipality: str = "Toronto") -> tuple[Money, Money, str]:
    """(Ontario NRST, Toronto MNRST, basis). Citizens and permanent residents are exempt."""
    cur = value.currency
    if buyer_status in ("canadian", "permanent_resident"):
        return Money.zero(cur), Money.zero(cur), "acquirer is a Canadian citizen or permanent resident: exempt"
    if buyer_status == "foreign_corporation" or buyer_status == "foreign_national":
        on = value.scale(NRST_ONTARIO)
        to = value.scale(MNRST_TORONTO) if municipality.lower() == "toronto" else Money.zero(cur)
        return on, to, "acquirer is a foreign national or a corporation controlled by one: NRST applies to land with one to six residential units, a fourplex included"
    on = value.scale(NRST_ONTARIO)
    to = value.scale(MNRST_TORONTO) if municipality.lower() == "toronto" else Money.zero(cur)
    return on, to, "citizenship unknown: shown at the foreign-national rate until confirmed"


def section_216_tax(net_rental_income: Money, *, year: int = 2026) -> Money:
    """Federal tax at graduated rates plus the 48% surtax, no personal credits.

    A non-resident electing under s.216 pays federal tax on net rental income
    and a surtax in lieu of provincial tax. Personal credits are not available
    on the election unless substantially all world income is Canadian.
    """
    if net_rental_income.minor_units <= 0:
        return Money.zero(net_rental_income.currency)
    rates = rates_for(year, "ON")
    cur = net_rental_income.currency
    tax = Money.zero(cur)
    lower = Money.zero(cur)
    for br in rates.federal_brackets:
        if br.upper is None:
            tax = tax + (net_rental_income - lower).scale(br.rate)
            break
        if net_rental_income <= lower:
            break
        band = min(net_rental_income, br.upper) - lower
        tax = tax + band.scale(br.rate)
        lower = br.upper
    return tax + tax.scale(NON_RESIDENT_SURTAX)


def corporate_tax_on_rental(net_rental_income: Money, *, ccpc: bool = False) -> tuple[Money, Money]:
    """(corporate tax, Part XIII on distributing the remainder to Italy)."""
    if net_rental_income.minor_units <= 0:
        z = Money.zero(net_rental_income.currency)
        return z, z
    corp = net_rental_income.scale(ONTARIO_GENERAL_CORPORATE_RATE)
    dividend = net_rental_income - corp
    return corp, dividend.scale(PART_XIII_DIVIDEND_ITALY)


@dataclass(frozen=True)
class OwnershipScenario:
    name: str
    description: str
    one_time_costs: tuple[tuple[str, Money], ...]
    annual_tax_on_net_rental: Money | None
    annual_compliance: Money
    requirements: tuple[str, ...]
    risks: tuple[str, ...]
    confirm: tuple[str, ...]
    fits: str  # "yes" | "no" | "depends"

    @property
    def one_time_total(self) -> Money:
        t = Money.zero()
        for _, m in self.one_time_costs:
            t = t + m
        return t


def ownership_scenarios(facts: EngagementFacts, *, net_rental_income: Money, transfer_value: Money) -> list[OwnershipScenario]:
    cur = facts.currency
    compliance_corp = Money.from_decimal(CORPORATE_COMPLIANCE_PER_YEAR, cur)
    zero = Money.zero(cur)
    single_family = facts.total_units <= 2
    ltt_on = ontario_land_transfer_tax(transfer_value, one_or_two_single_family=single_family)
    ltt_to = toronto_land_transfer_tax(transfer_value, one_or_two_single_family=single_family) if facts.municipality.lower() == "toronto" else zero
    nrst_on, nrst_to, nrst_basis = non_resident_speculation_tax(transfer_value, buyer_status=facts.client_citizenship, municipality=facts.municipality)

    # 1. Stay as is.
    half = net_rental_income.scale(Decimal("0.5"))
    tax_split = section_216_tax(half) + section_216_tax(half)
    tax_single = section_216_tax(net_rental_income)
    personal = OwnershipScenario(
        name="Continue personal co-ownership, elect under s.216",
        description="The owners keep the property jointly, each reports half the net rental income on a s.216 return, and an agent withholds on rent.",
        one_time_costs=(),
        annual_tax_on_net_rental=tax_split,
        annual_compliance=Money.from_decimal("1500.00", cur),
        requirements=("NR6 undertaking before the first rent so withholding is on net rather than 25% of gross",
                      "A Canadian agent or property manager to withhold and remit Part XIII tax monthly",
                      "NR4 slips and summary by 31 March; s.216 returns (T1159) by 30 June when an NR6 is in place",
                      "T1 for the change-of-use year reporting the deemed disposition"),
        risks=("Unlimited personal liability for a rental building; insurance is the mitigation",
               "s.116 clearance and 25% withholding on any future sale until a certificate issues",
               "Estate: jointly held Canadian real property passes by survivorship but the survivor faces the same non-resident rules"),
        confirm=(f"Tax if reported by one owner instead of split: {tax_single.format()} against {tax_split.format()} split",),
        fits="yes",
    )

    # 2. Transfer to a corporation.
    corp_tax, part_xiii = corporate_tax_on_rental(net_rental_income)
    one_time = [("Ontario land transfer tax on transfer at FMV", ltt_on)]
    if ltt_to.minor_units:
        one_time.append(("Toronto municipal land transfer tax", ltt_to))
    if nrst_on.minor_units:
        one_time.append(("Ontario non-resident speculation tax (25%), if the corporation is foreign-controlled", nrst_on))
    if nrst_to.minor_units:
        one_time.append(("Toronto municipal NRST (10%), same condition", nrst_to))
    one_time.append(("Legal, s.85 election, s.116 certificate, lender consent, incorporation (estimate)", Money.from_decimal("12000.00", cur)))
    corporation = OwnershipScenario(
        name="Transfer the property to a new Canadian corporation",
        description="Roll the land and work in progress into a corporation the owners control, which completes the build and holds the rentals.",
        one_time_costs=tuple(one_time),
        annual_tax_on_net_rental=corp_tax + part_xiii,
        annual_compliance=compliance_corp,
        requirements=("A corporation controlled by non-residents is not a Canadian-controlled private corporation: rental income is taxed at the general rate (about 26.5% in Ontario) with no small business deduction and no refundable tax on distribution",
                      "s.85 rollover to defer the accrued gain on the land; the transferor is a non-resident so a s.116 clearance certificate is required and the shares received are taxable Canadian property",
                      "The transfer of real property under construction is a taxable supply for GST/HST; the corporation must be registered before closing to recover the tax, and the self-supply then happens in the corporation",
                      "Lender consent and likely a new facility in the corporation's name, with personal guarantees",
                      "Dividends to Italy bear Part XIII withholding at the treaty rate (15% for an individual)"),
        risks=(nrst_basis,
               "Land transfer tax is payable on a transfer to one's own corporation; there is no affiliated-party exemption for an individual transferor",
               "If a future sale of units is intended, a corporation makes the flip a corporate sale with s.116 avoided at the share level only on a share sale, which buyers of condominium units do not do",
               "Two owners means two shareholders and a shareholders' agreement"),
        confirm=("Whether the client and co-owner are Canadian citizens or permanent residents decides NRST; unknown is shown at the foreign rate",
                 "Italian tax treatment of holding through a Canadian corporation must be reviewed by an Italian advisor before any decision"),
        fits="depends",
    )

    # 3. Co-ownership formalised as a partnership for GST/HST.
    partnership = OwnershipScenario(
        name="Keep personal ownership, register the co-ownership as a partnership for GST/HST",
        description="Income tax stays personal and flows through; one GST/HST registration claims the credits and reports the self-supply for both owners.",
        one_time_costs=(("Registration and co-ownership agreement (estimate)", Money.from_decimal("2500.00", cur)),),
        annual_tax_on_net_rental=tax_split,
        annual_compliance=Money.from_decimal("1800.00", cur),
        requirements=("A written co-ownership or partnership agreement stating the interests",
                      "Construction invoices reissued to or addressed as the partnership from the registration date",
                      "T5013 partnership information return may be required above the filing thresholds"),
        risks=("Whether a rental co-ownership is a partnership at law affects the GST/HST analysis; a partnership carries on business, a bare co-ownership may not",
               "Contributing the land to a partnership can itself be a disposition for income tax (s.97) and may attract land transfer tax on the beneficial interest"),
        confirm=("Land transfer tax and s.97 consequences of forming the partnership must be confirmed with the lawyer before registering",),
        fits="depends",
    )

    # 4. Family transfer.
    family = OwnershipScenario(
        name="Transfer to family members",
        description="Gift or sell some or all of the property to children or other relatives.",
        one_time_costs=(("Capital gain realised at fair market value on the interest transferred (computed in the principal-residence section)", zero),
                        ("Land transfer tax on any mortgage assumed or consideration paid", zero)),
        annual_tax_on_net_rental=None,
        annual_compliance=zero,
        requirements=("s.116 clearance certificate for the non-resident transferor; the transferee must withhold 25% without one",
                      "Attribution of income and gains back to the transferor for a spouse or minor child (s.74.1 to 74.4)",
                      "If the transferee is a foreign national, NRST and Toronto MNRST apply to the transfer"),
        risks=("A gift is a disposition at FMV with no cash to pay the tax",
               "The transferee's own residency and citizenship drive the future rules"),
        confirm=("Not modelled as a number: who, what share, and whether consideration is paid are open",),
        fits="depends",
    )

    # 5. Condominium conversion and sale.
    sale = OwnershipScenario(
        name="Convert to condominium and sell units",
        description="Register a condominium plan after completion and sell individual units.",
        one_time_costs=(("HST on each unit sold as a new unit by a builder: 13% of the sale price", zero),
                        ("Condominium registration, legal and survey costs", zero)),
        annual_tax_on_net_rental=None,
        annual_compliance=zero,
        requirements=("Sales within a year of first occupancy repay the rental rebate; sales of newly built units are business income, not capital gains, for a builder",
                      "s.116 on each sale by a non-resident vendor: 25% of gross proceeds withheld by each purchaser until a certificate issues",
                      "Residential property flipping rule: a unit held under 365 days is business income regardless of intention"),
        risks=("The enhanced rebate assumes long-term rental; a plan to sell soon after completion is inconsistent with it",),
        confirm=("Modelled only as conditions; a unit-by-unit sale pro forma needs prices and timing",),
        fits="depends",
    )
    return [personal, partnership, corporation, family, sale]
