"""Profiles drive policy; policy drives every control. Prove the bridge."""

from __future__ import annotations

from datetime import date

from forge.clients import ClientProfile, sales_tax_for
from forge.money import Money
from forge.pipeline import run_continuous_controller
from forge.rules import REGISTRY


def _profile(**kw) -> ClientProfile:
    base = {"client_id": "C1", "business_name": "Test Co", "province": "ON", "naics_code": "561730",
                "annual_revenue_estimate": Money.from_decimal("1800000.00"), "services": ("bookkeeping", "hst")}
    base.update(kw)
    return ClientProfile(**base)


class TestProfile:
    def test_province_sets_the_tax_rate_for_every_control(self):
        on = _profile(province="ON").to_policy()
        ns = _profile(province="NS").to_policy()
        ab = _profile(province="AB").to_policy()
        assert (on.sales_tax_rate, ns.sales_tax_rate, ab.sales_tax_rate) == (
            sales_tax_for("ON").rate, sales_tax_for("NS").rate, sales_tax_for("AB").rate)

    def test_revenue_sets_the_benchmark_tier(self):
        assert _profile(annual_revenue_estimate=Money.from_decimal("300000.00")).size_tier_label == "micro"
        assert _profile(annual_revenue_estimate=Money.from_decimal("6000000.00")).size_tier_label == "mid-size"

    def test_services_bring_control_categories_into_scope(self):
        p = _profile(services=("bookkeeping",))
        assert "payroll" not in p.control_categories_in_scope
        q = _profile(services=("bookkeeping", "payroll", "cfo_advisory"))
        assert {"payroll", "benchmark", "treasury"} <= set(q.control_categories_in_scope)

    def test_integrity_is_never_out_of_scope(self):
        p = _profile(services=("cfo_advisory",))
        assert "integrity" in p.control_categories_in_scope
        assert "reconciliation" in p.control_categories_in_scope

    def test_industry_pack_implies_deferred_revenue(self):
        """Landscaping defers seasonal contracts and legal defers retainers;
        an electrical contractor with no seasonality defers nothing."""
        assert _profile().needs_deferred_revenue_control
        assert _profile(naics_code="541110").needs_deferred_revenue_control
        assert not _profile(naics_code="238210", seasonal=False).needs_deferred_revenue_control

    def test_construction_with_subs_files_t5018(self):
        assert _profile(naics_code="236110", uses_subcontractors=True).files_t5018
        assert not _profile(naics_code="561730", uses_subcontractors=True).files_t5018

    def test_non_calendar_fiscal_year_start(self):
        p = _profile(fiscal_year_end_month=9, fiscal_year_end_day=30)
        assert p.fiscal_year_start(date(2026, 6, 30)) == date(2025, 10, 1)
        assert p.fiscal_year_start(date(2026, 11, 1)) == date(2026, 10, 1)
        assert _profile().fiscal_year_start(date(2026, 6, 30)) == date(2026, 1, 1)

    def test_json_round_trip_preserves_money_and_tuples(self):
        p = _profile(monthly_fee=Money.from_decimal("1500.00"))
        q = ClientProfile.from_json(p.to_json())
        assert q.monthly_fee == p.monthly_fee and q.services == p.services

    def test_legacy_intake_shape_maps_cleanly(self):
        p = ClientProfile.from_legacy_intake({
            "id": "C9", "business_name": "Eternal Property Services", "province": "Ontario",
            "naics_code": "561730", "annual_revenue_estimate": "$1,800,000", "employees_ft": "6",
            "subcontractors": "yes", "fiscal_year_end": "September 30", "services": ["bookkeeping", "payroll"],
            "hst_registered": "yes", "owner_draws_salary": "dividends",
        })
        assert p.province == "ON" and p.fiscal_year_end_month == 9
        assert p.annual_revenue_estimate == Money.from_decimal("1800000.00")
        assert p.uses_subcontractors and p.owner_compensation_method == "dividends"

    def test_policy_scopes_controls(self):
        pol = _profile(services=("bookkeeping",)).to_policy()
        ids = pol.controls_in_scope(REGISTRY)
        assert "FOS-R001" in ids
        assert "FOS-R038" not in ids  # payroll not engaged


class TestProfileDrivesTheRun:
    def test_run_with_profile_uses_its_policy(self, clean_company):
        p = _profile(services=("bookkeeping", "hst", "cfo_advisory"))
        run = run_continuous_controller(
            clean_company.ledger, period_start=date(2026, 6, 1), period_end=date(2026, 6, 30),
            statements=clean_company.statements, profile=p,
        )
        assert run.ctx.policy["naics_code"] == "561730"
        assert any(f.category == "benchmark" for f in run.findings)

    def test_profile_scope_limits_controls_run(self, clean_company):
        p = _profile(services=("bookkeeping",))
        run = run_continuous_controller(
            clean_company.ledger, period_start=date(2026, 6, 1), period_end=date(2026, 6, 30),
            statements=clean_company.statements, profile=p, scope_to_profile=True,
        )
        assert run.outcome.rules_run < len(REGISTRY)
        assert not any(f.category == "benchmark" for f in run.findings)
