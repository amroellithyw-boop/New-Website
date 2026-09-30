"""The written Canadian Tax, Ownership and Project Action Plan.

Every number in the plan is computed from the facts file by the modules in
this package. Every statement of law carries its source. Anything the plan
could not settle from the facts is marked "confirm" and repeated in the
questions list at the end, so the implementation meeting has its agenda.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from ..money import Money
from .chart import chart_for_setup
from .facts import EngagementFacts
from .hst import SOURCES as HST_SOURCES
from .hst import hst_position
from .ownership import SOURCES as OWN_SOURCES
from .ownership import ownership_scenarios
from .principal_residence import SOURCES as PRE_SOURCES
from .principal_residence import change_of_use
from .project import project_status
from .rental import PBRH_ACCELERATED_RATE, rental_pro_forma
from .rental import SOURCES as RENT_SOURCES
from .residency import SOURCES as RES_SOURCES
from .residency import departure_review, non_resident_rental_calendar

__all__ = ["render_action_plan"]


def _pct(x: Decimal | None) -> str:
    return f"{x:.1%}" if x is not None else "n/a"


def render_action_plan(facts: EngagementFacts, *, today: date, firm: str = "Profit Forge", preparer: str = "Amro Ellithy") -> str:
    cur = facts.currency
    hst = hst_position(facts, today=today)
    review = departure_review(facts, today=today)
    pre = change_of_use(facts)
    status = project_status(facts, today=today)
    pro = rental_pro_forma(facts)
    pro_fast = rental_pro_forma(facts, cca_rate=PBRH_ACCELERATED_RATE)
    transfer_value = facts.land_fmv_at_change_of_use or facts.purchase_price or Money.zero(cur)
    comparison_income = pro.net_rental_before_cca if pro.net_rental_before_cca.minor_units > 0 else pro.net_operating_income
    comparison_basis = ("net rental income after mortgage interest" if pro.net_rental_before_cca.minor_units > 0
                        else "net operating income before interest, as an upper bound, because interest exceeds income in year one")
    scenarios = ownership_scenarios(facts, net_rental_income=comparison_income, transfer_value=transfer_value)
    scenarios_at_completion = ownership_scenarios(facts, net_rental_income=comparison_income,
                                                  transfer_value=facts.appraised_completed_value or transfer_value)
    confirms: list[str] = []
    L: list[str] = []
    L.append(f"# Canadian Tax, Ownership and Project Action Plan: {facts.property_label}")
    L.append(f"Prepared by {firm} for {facts.client_name}" + (f" and {facts.co_owner_name}" if facts.co_owner_name else "") + f", {today.strftime('%d %B %Y')}. Draft for the implementation meeting.")
    L.append("")
    L.append("Every figure below is computed from the facts you gave us; the facts are listed in section 9. Statements of law cite their source. Items marked *confirm* need a document, an appraisal or a professional's sign-off before anyone acts on them.")

    # 1. Summary
    L += ["", "## 1. The five decisions", ""]
    fourplex = next((r for r in hst.results if r.pbrh.eligible), None)
    others = [r for r in hst.results if not r.pbrh.eligible]
    if fourplex:
        L.append(f"1. **Keep the {fourplex.complex_name.lower()} as long-term rental and claim the enhanced rebate.** Self-supply tax at completion is {fourplex.total_tax.format()} on {fourplex.fmv.format()}; the enhanced purpose-built rental rebate returns {fourplex.total_rebate.format()} of it, leaving {fourplex.net_payable.format()}. Selling units within a year of first occupancy forfeits this.")
    for r in others:
        L.append(f"2. **Treat the {r.complex_name.lower()} separately.** With {r.units} unit(s) it does not qualify for the enhanced rebate. Self-supply tax {r.total_tax.format()} on {r.fmv.format()}; standard rebates {r.total_rebate.format()} (federal {r.federal_rebate.format()}, Ontario {r.ontario_rebate.format()}); net {r.net_payable.format()}. *confirm*: whether a detached suite on the same title is its own residential complex.")
    L.append(f"3. **Claim the construction HST now.** About {hst.itc_estimate.format()} of HST sits in the {facts.budget_mid.format() if facts.budget_mid else 'budget'} budget ({hst.itc_basis}). Registered builders recover it on each return as costs are paid, which funds roughly {_pct(Decimal(hst.itc_estimate.minor_units) / Decimal(facts.budget_mid.minor_units)) if facts.budget_mid else ''} of the build. Nothing has been claimed to date.")
    L.append("4. **Fix the departure file before anything else.** " + next((i.action for i in review if i.kind == "departure"), "Departure appears reported."))
    personal = scenarios[0]
    corp = next(s for s in scenarios if "corporation" in s.name.lower())
    L.append(f"5. **Do not move the property into a corporation now.** A transfer at today's land value ({transfer_value.format()}) costs {corp.one_time_total.format()} before any capital gains tax, and at completion value ({(facts.appraised_completed_value or transfer_value).format()}) it costs {next(s for s in scenarios_at_completion if 'corporation' in s.name.lower()).one_time_total.format()}. Personal co-ownership with a s.216 election pays {personal.annual_tax_on_net_rental.format()} a year on the projected net rental income against {corp.annual_tax_on_net_rental.format()} through a non-resident-controlled corporation. The corporation's case rests on liability and a future unit sale, not on tax.")

    # 2. Residency and filing
    L += ["", "## 2. Residency and filing history", ""]
    for i in review:
        flag = " *confirm*" if i.confirm else ""
        exp = f" Exposure {i.exposure.format()}." if i.exposure else ""
        due = f" Due {i.deadline}." if i.deadline else ""
        L.append(f"- **{i.title}.**{flag} {i.detail} Action: {i.action}{exp}{due}")
        if i.confirm:
            confirms.append(i.title)
    if pre:
        L += ["", "### The change of use", ""]
        L.append(f"Turning the former home into a rental development on {facts.change_of_use_date} is a deemed disposition at fair market value.")
        L.append(f"- Proceeds (land FMV at change of use): {pre.proceeds.format()}" + (" *confirm: appraisal required*" if facts.land_fmv_at_change_of_use is None else " *confirm with an appraisal as at that date*"))
        L.append(f"- Adjusted cost base: {pre.adjusted_cost_base.format()}; gain {pre.gain.format()}")
        L.append(f"- Principal residence years {', '.join(str(y) for y in facts.principal_residence_years)} plus one: exempt fraction {pre.exempt_fraction:.4f} of {pre.years_owned} years owned; exempt {pre.exempt_gain.format()}")
        L.append(f"- Capital gain {pre.capital_gain.format()}; taxable capital gain at 50% {pre.taxable_capital_gain.format()}, split between the co-owners, reported on {facts.change_of_use_date.year} non-resident T1s with T2091(IND)")
        for n in pre.notes:
            L.append(f"- {n}")
        confirms.append("Land appraisal as at the change-of-use date")

    # 3. GST/HST
    L += ["", "## 3. GST/HST: self-supply, credits and rebates", ""]
    L.append("When a builder rents out a newly built residential complex, the builder is deemed to sell and repurchase it at fair market value at the later of substantial completion and first occupancy, and owes HST on that value (ETA s.191). The rebates then return part or all of it.")
    L.append("")
    L.append("| Building | Units | FMV | HST on self-supply | Federal rebate | Ontario rebate | Net HST | Basis |")
    L.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |")
    for r in hst.results:
        L.append(f"| {r.complex_name} | {r.units} | {r.fmv.format()} | {r.total_tax.format()} | {r.federal_rebate.format()} | {r.ontario_rebate.format()} | {r.net_payable.format()} | {r.basis} |")
    L.append(f"| **Total** | {facts.total_units} | | {hst.total_self_supply_tax.format()} | | {hst.total_rebates.format()} | **{hst.net_payable_at_completion.format()}** | |")
    L.append("")
    for r in hst.results:
        L.append(f"**{r.complex_name}, enhanced rebate test:** " + ("qualifies. " if r.pbrh.eligible else "does not qualify. ") + "; ".join(r.pbrh.reasons) + ".")
        for c in r.confirm:
            L.append(f"- *confirm*: {c}")
            confirms.append(f"{r.complex_name}: {c}")
        if not r.pbrh.eligible and r.units:
            L.append("- Per unit (equal floor areas assumed): " + "; ".join(f"unit {u.unit} FMV {u.fmv.format()} federal {u.federal.format()} Ontario {u.ontario.format()}" for u in r.per_unit))
    L += ["", f"**Input tax credits.** {hst.itc_basis}: about {hst.itc_estimate.format()} over the build. Net cash effect of HST over the project, credits recovered less net tax at completion: {hst.net_cash_effect_over_project.format()}.", ""]
    for w in hst.warnings:
        L.append(f"- {w}")
        if "co-own" in w or "invoice" in w.lower():
            confirms.append(w.split(".")[0])
    L += ["", "**Dates**", ""]
    for d in hst.deadlines:
        L.append(f"- {d.due_on}: {d.description}" + (" *confirm*" if d.confirm else ""))

    # 4. Ownership
    L += ["", "## 4. Ownership: the options compared", ""]
    L.append(f"Income used for the annual tax comparison: {comparison_income.format()}, {comparison_basis} (section 6). Transfer value used for one-time costs: {transfer_value.format()} today; {(facts.appraised_completed_value or transfer_value).format()} at completion.")
    L.append("")
    L.append("| Option | One-time cost today | One-time cost at completion | Annual tax on net rent | Annual compliance | Fit |")
    L.append("| --- | ---: | ---: | ---: | ---: | --- |")
    for s_now, s_later in zip(scenarios, scenarios_at_completion, strict=False):
        tax = s_now.annual_tax_on_net_rental.format() if s_now.annual_tax_on_net_rental is not None else "not modelled"
        L.append(f"| {s_now.name} | {s_now.one_time_total.format()} | {s_later.one_time_total.format()} | {tax} | {s_now.annual_compliance.format()} | {s_now.fits} |")
    L.append("")
    for s in scenarios:
        L.append(f"### {s.name}")
        L.append(s.description)
        if s.one_time_costs:
            L.append("- One-time: " + "; ".join(f"{k} {v.format()}" if v.minor_units else k for k, v in s.one_time_costs))
        L.append("- Requires: " + " ".join(f"({i + 1}) {r}." for i, r in enumerate(s.requirements)))
        L.append("- Risks: " + " ".join(f"{r}." for r in s.risks))
        for c in s.confirm:
            L.append(f"- *confirm*: {c}")
            confirms.append(c)
        L.append("")
    L.append("**Recommendation on the facts stated.** Keep personal co-ownership through construction and the first rental year. Register the co-ownership correctly for GST/HST now (section 3). Revisit incorporation only if a unit sale or a liability event makes it worth the transfer taxes, and only after the Italian advisor has reviewed the consequences. The recommendation changes if either owner is a foreign national, because the speculation taxes then apply to every transfer option.")

    # 5. Project
    L += ["", "## 5. The build: cost, financing and cash", ""]
    L.append(f"- Budget {status.budget.format()} (midpoint); spent {status.spent.format()} ({status.percent_spent:.0%}) as of {facts.spent_as_of or today}; cost to complete {status.cost_to_complete.format()} over {status.months_remaining} months, about {status.monthly_burn_required.format()} a month")
    L.append(f"- Facilities {status.facility_limit.format()}, drawn {status.drawn.format()}, undrawn {status.undrawn.format()}; owner equity still required {status.owner_equity_required.format()}")
    L.append(f"- Interest during construction, capitalised: about {status.capitalised_interest_estimate.format()}")
    L.append(f"- Total project cost including land: {status.total_project_cost.format()}; loan to cost {_pct(status.loan_to_cost)}; loan to value on the {status.appraised_value.format() if status.appraised_value else 'appraisal'} appraisal {_pct(status.loan_to_value)}; equity created at completion {status.equity_created.format() if status.equity_created else 'n/a'}")
    for n in status.notes:
        L.append(f"- {n}")

    # 6. Rental
    L += ["", "## 6. Rental operations: what the owner keeps", ""]
    L.append(f"- Gross potential rent {pro.gross_potential_rent.format()} from {pro.units} units; vacancy {pro.vacancy.format()}; effective gross {pro.effective_gross_income.format()}")
    L.append(f"- Operating expenses {pro.operating_expenses.format()}; management {pro.management_fee.format()}; net operating income {pro.net_operating_income.format()}; cap rate on appraisal {_pct(pro.cap_rate)}")
    L.append(f"- Debt service on {pro.debt.format()} {pro.annual_debt_service.format()} a year; coverage {pro.dscr:.2f}x" if pro.dscr is not None else "- Debt service: n/a")
    L.append(f"- Cash flow before tax {pro.cash_flow_before_tax.format()}")
    L.append(f"- **Takeout financing.** At {pro.coverage_target}x coverage the income supports {pro.max_supportable_debt.format()} of mortgage; the facilities total {pro.debt.format()}, so {pro.debt_over_supportable.format()} would have to be repaid from equity or the rebate refund at takeout, or the lender's covenant relaxed. This is the number to raise with the lender before completion.")
    L.append(f"- Tax: net rental before CCA {pro.net_rental_before_cca.format()}; CCA available {pro.cca_available_year_one.format()} at 4% (half-year), claim limited to {pro.cca_claim.format()}; taxable {pro.taxable_net_rental.format()}; s.216 tax split between two owners {pro.section_216_tax_split.format()}")
    L.append(f"- If the 10% accelerated rate for purpose-built rental applies: CCA available {pro_fast.cca_available_year_one.format()}, claim {pro_fast.cca_claim.format()}, taxable {pro_fast.taxable_net_rental.format()}, tax {pro_fast.section_216_tax_split.format()} *confirm the measure is enacted and the building qualifies*")
    L.append(f"- Withholding: without an NR6, {pro.withholding_without_nr6.format()} a year is withheld on gross rent; with one, {pro.withholding_with_nr6.format()} on net")
    for n in pro.notes:
        L.append(f"- {n}")
    confirms.append("Accelerated 10% CCA for purpose-built rental: enacted and applicable")

    # 7. Accounting setup
    L += ["", "## 7. Construction accounting structure", ""]
    L.append("One QuickBooks company for the project, one project bank account, and this chart. Every trade bill is entered net of HST with the HST to the recoverable account, 10% held back on payment, and the document attached.")
    L.append("")
    L.append("| Number | Account | CCA class | Note |")
    L.append("| --- | --- | --- | --- |")
    for a in chart_for_setup():
        L.append(f"| {a['number']} | {a['name']} | {a['cca_class'] or ''} | {a['note']} |")
    L += ["", "**Document workflow.** Trades invoice the registered owner(s) exactly as registered. Every bill: PDF to the project folder, entered within the week, paid from the project account only, holdback recorded. Lender draw requests are built from the ledger's hard-cost total and the paid-invoice list. Monthly: bank and loan statements reconciled, HST return prepared from the recoverable account, the Owner Finance Pack issued.",
          "", "**GST/HST support schedule.** One line per bill: vendor, invoice number, date, net, HST, account, paid date, holdback; totals agree to the recoverable account and to each return filed. This is the file the CRA asks for on a rebate claim."]

    # 8. Actions
    L += ["", "## 8. Action list", ""]
    L.append("| # | Action | Owner | By |")
    L.append("| --- | --- | --- | --- |")
    n = 0
    for i in review:
        if i.deadline:
            n += 1
            L.append(f"| {n} | {i.action} | {firm} | {i.deadline} |")
    for d in hst.deadlines:
        n += 1
        L.append(f"| {n} | {d.description} | {firm if d.authority == 'CRA' else d.authority} | {d.due_on} |")
    n += 1
    L.append(f"| {n} | Confirm citizenship or permanent residence of both owners with documents | Client | {today} |")
    n += 1
    L.append(f"| {n} | Italian advisor to review the Canadian rental income and any structure change | Client | before any transfer |")
    n += 1
    L.append(f"| {n} | Set up the project company, chart and document workflow; enter costs to date with HST split | {firm} | {today} |")
    if facts.expected_completion:
        for d in non_resident_rental_calendar(facts.expected_completion, year=facts.expected_completion.year)[:3]:
            n += 1
            L.append(f"| {n} | {d.description} | {firm} | {d.due_on} |")

    # 9. Facts and questions
    L += ["", "## 9. Facts relied on", ""]
    L.append(f"- Owners: {facts.client_name}" + (f" and {facts.co_owner_name}" if facts.co_owner_name else "") + f", resident in {facts.resident_country} since {facts.departure_date}; citizenship {facts.client_citizenship}")
    L.append(f"- Property purchased {facts.purchase_date} for {facts.purchase_price.format() if facts.purchase_price else 'n/a'}; former principal residence {facts.principal_residence_years}; house demolished: {facts.building_demolished}")
    L.append(f"- Construction started {facts.construction_start}; completion expected {facts.expected_completion}; budget {facts.budget_low.format() if facts.budget_low else ''} to {facts.budget_high.format() if facts.budget_high else ''} ({'HST-inclusive' if facts.budget_includes_hst else 'before HST'}); spent {facts.spent_to_date.format() if facts.spent_to_date else 'n/a'}")
    L.append("- Buildings: " + "; ".join(f"{c.name}, {c.units} unit(s), {'detached' if c.detached else 'attached'}, expected FMV {c.expected_fmv.format() if c.expected_fmv else 'n/a'}" for c in facts.complexes))
    L.append("- Financing: " + "; ".join(f"{f.name} {f.limit.format()} at {f.annual_rate:.2%}, drawn {f.drawn.format()}" for f in facts.facilities))
    L.append(f"- Appraised value at completion {facts.appraised_completed_value.format() if facts.appraised_completed_value else 'n/a'}; GST/HST registered: {facts.gst_registered} ({facts.gst_account_holder}); returns outstanding {list(facts.gst_returns_outstanding)}; construction credits claimed: {facts.construction_itcs_claimed}")
    L += ["", "## 10. Questions to settle at the meeting", ""]
    seen: set[str] = set()
    for c in confirms:
        if c not in seen:
            seen.add(c)
            L.append(f"- {c}")
    L += ["", "## 11. Sources", ""]
    for src in dict.fromkeys(HST_SOURCES + OWN_SOURCES + PRE_SOURCES + RES_SOURCES + RENT_SOURCES):
        L.append(f"- {src}")
    L += ["", f"This plan is accounting and tax-compliance support on the facts provided. It is not a legal opinion, a residency determination, a valuation or Italian tax advice. Prepared by {preparer}, {firm}."]
    return "\n".join(L)
