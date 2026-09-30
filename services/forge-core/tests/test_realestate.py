"""The real-estate engagement: every number the plan prints, proven."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from forge.canonical.enums import AgentRole, TxnType
from forge.connectors.fixture_development import build_development_project
from forge.money import Money
from forge.pipeline import DEFAULT_POLICY
from forge.realestate import (
    cash_requirement_schedule,
    cca_claim_limited_by_rental_income,
    change_of_use,
    departure_review,
    federal_nrrp_rebate,
    hst_position,
    non_resident_rental_calendar,
    non_resident_speculation_tax,
    ontario_land_transfer_tax,
    ontario_nrrp_rebate,
    owner_finance_pack,
    ownership_scenarios,
    pbrh_eligibility,
    principal_residence_exemption,
    project_status,
    rebate_for_complex,
    render_action_plan,
    render_finance_pack,
    rental_pro_forma,
    section_216_tax,
    section_216_withholding,
    toronto_land_transfer_tax,
)
from forge.realestate.facts import EXAMPLE_FACTS, Complex, EngagementFacts
from forge.realestate.ownership import corporate_tax_on_rental
from forge.realestate.rental import level_payment
from forge.realestate.residency import t1161_penalty
from forge.rules import REGISTRY, RuleContext, run_rules
from forge.workitems.risk import PREPARERS, SPECIALIST_FOR

TODAY = date(2026, 9, 30)


def _m(v: str) -> Money:
    return Money.from_decimal(v)


@pytest.fixture(scope="module")
def facts() -> EngagementFacts:
    return EngagementFacts.from_dict(EXAMPLE_FACTS)


class TestRebateArithmetic:
    def test_federal_rebate_full_phase_out_and_nil(self):
        assert federal_nrrp_rebate(_m("300000.00")) == _m("5400.00")
        assert federal_nrrp_rebate(_m("350000.00")) == _m("6300.00")
        assert federal_nrrp_rebate(_m("400000.00")) == _m("3150.00")
        assert federal_nrrp_rebate(_m("450000.00")).is_zero
        assert federal_nrrp_rebate(_m("457000.00")).is_zero

    def test_ontario_rebate_caps_at_24000(self):
        assert ontario_nrrp_rebate(_m("300000.00")) == _m("18000.00")
        assert ontario_nrrp_rebate(_m("400000.00")) == _m("24000.00")
        assert ontario_nrrp_rebate(_m("2300000.00")) == _m("24000.00")

    def test_pbrh_needs_four_units_long_term_rental_in_the_window(self):
        four = Complex("Fourplex", 4, "multiple_unit")
        assert pbrh_eligibility(four, construction_start=date(2026, 6, 1), completion=date(2027, 5, 1)).eligible
        assert not pbrh_eligibility(Complex("Suite", 1, "single_unit", detached=True), construction_start=date(2026, 6, 1), completion=date(2027, 5, 1)).eligible
        assert not pbrh_eligibility(four, construction_start=date(2023, 9, 1), completion=date(2025, 1, 1)).eligible
        assert not pbrh_eligibility(Complex("Fourplex", 4, "multiple_unit", intended_use="sale"), construction_start=date(2026, 6, 1), completion=date(2027, 5, 1)).eligible

    def test_example_engagement_position(self, facts):
        h = hst_position(facts, today=TODAY)
        by = {r.complex_name: r for r in h.results}
        four, suite = by["Fourplex"], by["Garden suite"]
        assert four.total_tax == _m("299000.00") and four.net_payable.is_zero and four.pbrh.eligible
        assert suite.total_tax == _m("59410.00") and suite.federal_rebate.is_zero and suite.ontario_rebate == _m("24000.00")
        assert suite.net_payable == _m("35410.00")
        assert h.net_payable_at_completion == _m("35410.00")
        assert h.itc_estimate == _m("1550000.00").scale(Decimal("13") / Decimal("113"))
        assert any("not been claimed" in w for w in h.warnings)
        assert any("co-owned" in w for w in h.warnings)
        kinds = {d.kind for d in h.deadlines}
        assert {"hst_self_supply", "nrrp_rebate_application", "gst_return_outstanding", "appraisal"} <= kinds
        rebate_due = next(d for d in h.deadlines if d.kind == "nrrp_rebate_application")
        assert rebate_due.due_on == date(2029, 5, 31)

    def test_per_unit_rebates_use_floor_area_shares(self):
        cx = Complex("Duplex", 2, "multiple_unit", floor_area_share=(Decimal("0.6"), Decimal("0.4")), expected_fmv=_m("800000.00"))
        r = rebate_for_complex(cx, construction_start=date(2026, 6, 1), completion=date(2027, 5, 1))
        assert [u.fmv for u in r.per_unit] == [_m("480000.00"), _m("320000.00")]
        assert r.per_unit[0].federal.is_zero and r.per_unit[1].federal == _m("5760.00")
        assert r.federal_rebate == _m("5760.00")


class TestPrincipalResidence:
    def test_example_change_of_use(self, facts):
        pre = change_of_use(facts)
        assert pre.years_owned == 9 and pre.years_designated == 3 and pre.plus_one
        assert pre.exempt_fraction == Decimal(4) / Decimal(9)
        assert pre.gain == _m("442000.00")
        assert pre.taxable_capital_gain == (pre.gain - pre.exempt_gain).scale(Decimal("0.5"))

    def test_non_resident_in_acquisition_year_loses_the_plus_one(self):
        r = principal_residence_exemption(proceeds=_m("1000000.00"), adjusted_cost_base=_m("500000.00"), acquisition_year=2018,
                                          disposition_year=2026, designated_years=(2018, 2019, 2020), resident_in_acquisition_year=False)
        assert r.exempt_fraction == Decimal(3) / Decimal(9)

    def test_a_loss_is_nil(self):
        r = principal_residence_exemption(proceeds=_m("400000.00"), adjusted_cost_base=_m("500000.00"), acquisition_year=2018,
                                          disposition_year=2026, designated_years=(2018,), resident_in_acquisition_year=True)
        assert r.taxable_capital_gain.is_zero and "nil" in r.notes[-1]


class TestOwnership:
    def test_land_transfer_tax_tiers(self):
        v = _m("1150000.00")
        assert ontario_land_transfer_tax(v) == _m("19475.00")
        assert toronto_land_transfer_tax(v) == _m("19475.00")
        big = _m("3000000.00")
        assert ontario_land_transfer_tax(big, one_or_two_single_family=True) - ontario_land_transfer_tax(big) == _m("5000.00")

    def test_speculation_taxes_follow_citizenship(self):
        v = _m("1000000.00")
        on, to, _ = non_resident_speculation_tax(v, buyer_status="canadian")
        assert on.is_zero and to.is_zero
        on, to, _ = non_resident_speculation_tax(v, buyer_status="foreign_national")
        assert on == _m("250000.00") and to == _m("100000.00")
        on, to, basis = non_resident_speculation_tax(v, buyer_status="unknown", municipality="Ottawa")
        assert on == _m("250000.00") and to.is_zero and "unknown" in basis

    def test_section_216_applies_the_48_percent_surtax_without_credits(self):
        assert section_216_tax(_m("50000.00")) == _m("10360.00")
        assert section_216_tax(_m("0.00")).is_zero

    def test_corporate_route_taxes_at_general_rate_then_treaty_withholding(self):
        corp, wht = corporate_tax_on_rental(_m("100000.00"))
        assert corp == _m("26500.00") and wht == _m("11025.00")

    def test_example_scenarios_put_the_transfer_cost_on_the_table(self, facts):
        s = ownership_scenarios(facts, net_rental_income=_m("50000.00"), transfer_value=_m("1150000.00"))
        assert s[0].name.startswith("Continue personal")
        corp = next(x for x in s if "corporation" in x.name.lower())
        assert corp.one_time_total == _m("453450.00")
        assert s[0].annual_tax_on_net_rental < corp.annual_tax_on_net_rental
        assert all(x.fits in ("yes", "no", "depends") for x in s)


class TestResidency:
    def test_t1161_penalty_caps(self):
        assert t1161_penalty(date(2021, 4, 30), date(2021, 5, 2)) == _m("100.00")
        assert t1161_penalty(date(2021, 4, 30), date(2021, 6, 30)) == _m("1525.00")
        assert t1161_penalty(date(2021, 4, 30), TODAY) == _m("2500.00")

    def test_example_review_lists_the_departure_and_change_of_use(self, facts):
        items = departure_review(facts, today=TODAY)
        kinds = [i.kind for i in items]
        assert kinds.count("departure") >= 2 and "gst" in kinds and "rental" in kinds and "ownership" in kinds
        t1161 = next(i for i in items if "T1161" in i.title)
        assert t1161.exposure == _m("2500.00")
        cou = next(i for i in items if "change of use" in i.title)
        assert cou.deadline == date(2027, 4, 30)

    def test_rental_calendar_dates(self):
        cal = non_resident_rental_calendar(date(2027, 5, 1), year=2027)
        by = {d.kind: d for d in cal}
        assert by["nr4"].due_on == date(2028, 3, 31)
        assert by["s216_return"].due_on == date(2028, 6, 30)
        assert min(d.due_on for d in cal if d.kind == "part_xiii_remittance") == date(2027, 6, 15)


class TestProjectAndRental:
    def test_status_and_schedule_tie(self, facts):
        s = project_status(facts, today=TODAY)
        assert s.cost_to_complete == _m("1300000.00") and s.months_remaining == 8
        assert s.owner_equity_required == _m("50000.00")
        sched = cash_requirement_schedule(facts, today=TODAY)
        total = Money.zero()
        for m in sched:
            total = total + m.spend
        assert total == s.cost_to_complete
        assert sched[-1].facility_balance <= facts.total_facility_limit
        assert s.loan_to_value is not None and Decimal("0.5") < s.loan_to_value < Decimal("0.6")

    def test_level_payment_matches_the_textbook(self):
        pay = level_payment(_m("100000.00"), Decimal("0.05"), 25)
        assert abs(pay.to_decimal() - Decimal("7095.25")) < Decimal("0.05")

    def test_cca_cannot_create_a_rental_loss(self):
        assert cca_claim_limited_by_rental_income(_m("-1000.00"), _m("50000.00")).is_zero
        assert cca_claim_limited_by_rental_income(_m("20000.00"), _m("50000.00")) == _m("20000.00")

    def test_pro_forma_supportable_debt_and_withholding(self, facts):
        p = rental_pro_forma(facts)
        assert p.gross_potential_rent == _m("174000.00")
        implied_service = level_payment(p.max_supportable_debt, facts.permanent_mortgage_rate, 25)
        assert abs((implied_service.scale(p.coverage_target) - p.net_operating_income).to_decimal()) < Decimal("1.00")
        assert p.debt_over_supportable == p.debt - p.max_supportable_debt
        assert p.cca_claim <= p.cca_available_year_one and p.cca_claim <= max(p.net_rental_before_cca, Money.zero())
        assert section_216_withholding(_m("100000.00"), _m("20000.00"), nr6_approved=False) == _m("25000.00")
        assert section_216_withholding(_m("100000.00"), _m("20000.00"), nr6_approved=True) == _m("5000.00")


def _ctx(fx, policy_extra=None):
    policy = dict(DEFAULT_POLICY)
    policy.update({"authorised_posters": {"sync", "migration"}, "gst_registered": True, "construction_start_date": "2026-06-01",
                   "facility_limits": {"2700": "500000.00", "2710": "1000000.00"}, "holdback_percent": 10})
    policy.update(policy_extra or {})
    return RuleContext(ledger=fx.ledger, period_start=date(2026, 9, 1), period_end=date(2026, 9, 30), fiscal_year_start=date(2026, 1, 1),
                       statements=tuple(fx.statements), policy=policy).prepare()


class TestDevelopmentControls:
    def test_clean_project_ledger_ties_reconciles_and_is_quiet(self, facts):
        fx = build_development_project(facts)
        ctx = _ctx(fx)
        assert ctx.tb.closing_imbalance.is_zero and ctx.bs.equation_difference.is_zero
        assert all(r.is_reconciled for r in ctx.reconciliations.values())
        out = run_rules(ctx, registry=REGISTRY)
        assert out.errors == {}
        assert [f.rule_id for f in out.findings] == []

    def test_each_development_control_fires_on_its_defect(self, facts):
        from forge.connectors.fixture_contractor import LedgerBuilder

        def mutated(*posts):
            fx = build_development_project(facts)
            b = LedgerBuilder("ENT-DEV")
            for kwargs in posts:
                lines = [b.line(a, _m(v), party_id=kwargs.get("party_id")) for a, v in kwargs.pop("lines")]
                fx.ledger.transactions.append(b.post(lines=lines, created_by="sync", **kwargs))
            fx.ledger.build_indexes()
            return fx

        cases = {
            "FOS-R057": mutated({"type": TxnType.EXPENSE, "txn_date": date(2026, 9, 28), "party_id": "VEND-BANK", "memo": "Loan interest",
                                 "lines": [("7000", "3000.00"), ("1000", "-3000.00")]}),
            "FOS-R058": mutated({"type": TxnType.BILL, "txn_date": date(2026, 9, 20), "party_id": "VEND-FRAME", "memo": "Framing",
                                 "lines": [("1600", "22600.00"), ("2000", "-22600.00")]}),
            "FOS-R059": mutated({"type": TxnType.BILL, "txn_date": date(2026, 9, 12), "party_id": "VEND-ELEC", "memo": "Electrical rough-in",
                                 "lines": [("1600", "30000.00"), ("1100", "3900.00"), ("2000", "-33900.00")]},
                                {"type": TxnType.BILL_PAYMENT, "txn_date": date(2026, 9, 25), "party_id": "VEND-ELEC", "memo": "Paid in full",
                                 "lines": [("2000", "33900.00"), ("1000", "-33900.00")]}),
            "FOS-R060": mutated({"type": TxnType.DEPOSIT, "txn_date": date(2026, 9, 15), "party_id": "VEND-BANK", "memo": "Draw",
                                 "lines": [("1000", "400000.00"), ("2700", "-400000.00")]}),
            "FOS-R061": mutated({"type": TxnType.BILL, "txn_date": date(2026, 9, 10), "party_id": "VEND-EXC", "memo": "Site grading",
                                 "lines": [("1500", "8000.00"), ("1100", "1040.00"), ("2000", "-9040.00")]}),
            "FOS-R062": mutated({"type": TxnType.BILL, "txn_date": date(2026, 9, 9), "party_id": "VEND-ARCH", "memo": "Design development",
                                 "lines": [("6600", "12000.00"), ("1100", "1560.00"), ("2000", "-13560.00")]}),
        }
        for rule_id, fx in cases.items():
            out = run_rules(_ctx(fx), registry=REGISTRY, only=[rule_id])
            assert out.errors == {}, out.errors
            assert [f.rule_id for f in out.findings] == [rule_id], (rule_id, [f.rule_id for f in out.findings])
            f = out.findings[0]
            assert f.packet.refs and f.packet.calculations and f.exposure.minor_units > 0

    def test_controls_are_out_of_scope_without_the_development_chart(self, clean_context):
        out = run_rules(clean_context, registry=REGISTRY, only=["FOS-R057", "FOS-R058", "FOS-R059", "FOS-R060", "FOS-R061", "FOS-R062"])
        assert out.findings == [] and out.errors == {}

    def test_development_has_a_preparer_and_a_specialist(self):
        assert PREPARERS["development"] is AgentRole.CLOSE_ACCOUNTANT
        assert SPECIALIST_FOR["development"] is AgentRole.REAL_ESTATE_STRATEGIST


class TestFinancePackAndPlan:
    def test_finance_pack_reads_the_ledger(self, facts):
        fx = build_development_project(facts)
        ctx = _ctx(fx)
        pack = owner_finance_pack(ctx, facts, [], budget_by_category={"hard_costs": _m("1200000.00")})
        by = {c.label: c for c in pack.categories}
        assert by["Land"].to_date == _m("708000.00")
        assert by["Hard costs"].to_date.minor_units > 0 and by["Hard costs"].percent_of_budget is not None
        assert pack.cost_to_date == by["Hard costs"].to_date + by["Soft costs"].to_date + by["Capitalised interest"].to_date + by["Garden suite"].to_date
        assert pack.holdbacks_payable == by["Hard costs"].to_date.scale(Decimal("0.10"))
        assert pack.hst_recoverable_balance.minor_units > 0
        assert pack.facilities[0][2] == _m("500000.00") and pack.facilities[1][2] == _m("1000000.00")
        assert pack.bank_reconciled is True and pack.missing_support == []
        text = render_finance_pack(pack)
        assert "Owner Finance Pack" in text and "Holdbacks payable" in text

    def test_plan_carries_every_section_and_the_key_numbers(self, facts):
        text = render_action_plan(facts, today=TODAY, firm="Test Firm", preparer="T. Preparer")
        for heading in ("## 1. The five decisions", "## 2. Residency", "## 3. GST/HST", "## 4. Ownership", "## 5. The build",
                        "## 6. Rental operations", "## 7. Construction accounting structure", "## 8. Action list",
                        "## 9. Facts relied on", "## 10. Questions", "## 11. Sources"):
            assert heading in text, heading
        assert "$299,000.00" in text and "$35,410.00" in text and "$453,450.00" in text
        assert "*confirm*" in text and "T2091" in text and "NR6" in text
        assert text.rstrip().endswith("Prepared by T. Preparer, Test Firm.")

    def test_facts_round_trip_through_json(self, tmp_path):
        import json

        from forge.realestate import load_facts

        p = tmp_path / "f.json"
        p.write_text(json.dumps(EXAMPLE_FACTS))
        again = load_facts(p)
        assert again.total_units == 5 and again.budget_mid == _m("1550000.00")
        assert again.complexes[1].detached and again.facilities[1].limit == _m("1000000.00")
        assert again.phases[0].authorised and not again.phases[1].authorised
