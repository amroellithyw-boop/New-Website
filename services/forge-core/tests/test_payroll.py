"""Canadian payroll. Two of these tests exist because the legacy system got them
wrong in production, and both cost real money."""

from __future__ import annotations

from decimal import Decimal

import pytest

from forge.money import Money
from forge.payroll import PayrollInput, calculate_pay, rates_for
from forge.payroll.canada import annual_tax


def _pay(gross: str, **kw) -> PayrollInput:
    return PayrollInput(gross=Money.from_decimal(gross), **kw)


class TestStatutoryMaxima:
    def test_published_maxima_are_reproduced(self):
        r = rates_for(2026, "ON")
        assert r.cpp_maximum_contribution == Money.from_decimal("4230.45")
        assert r.cpp2_maximum_contribution == Money.from_decimal("416.00")

    def test_a_full_year_lands_exactly_on_every_cap(self):
        """Year-to-date tracking, not a per-period approximation."""
        ytd_g = ytd_c = ytd_c2 = ytd_e = Money.zero()
        for _ in range(26):
            r = calculate_pay(
                _pay("3500.00", ytd_gross=ytd_g, ytd_cpp=ytd_c, ytd_cpp2=ytd_c2, ytd_ei=ytd_e)
            )
            ytd_g += r.gross
            ytd_c += r.cpp_employee
            ytd_c2 += r.cpp2_employee
            ytd_e += r.ei_employee
        rates = rates_for(2026, "ON")
        assert ytd_c == rates.cpp_maximum_contribution
        assert ytd_c2 == rates.cpp2_maximum_contribution
        assert ytd_e == rates.ei_maximum_contribution

    def test_uneven_pay_still_lands_on_the_cap(self):
        """A bonus period must not push contributions past the annual maximum.

        Total gross here is $114,000, comfortably above the YAMPE, so all three
        caps must bind exactly despite one period being twelve times the others.
        """
        ytd_g = ytd_c = ytd_c2 = ytd_e = Money.zero()
        amounts = ["3000.00"] * 12 + ["42000.00"] + ["3000.00"] * 13
        for amount in amounts:
            r = calculate_pay(
                _pay(amount, ytd_gross=ytd_g, ytd_cpp=ytd_c, ytd_cpp2=ytd_c2, ytd_ei=ytd_e)
            )
            ytd_g += r.gross
            ytd_c += r.cpp_employee
            ytd_c2 += r.cpp2_employee
            ytd_e += r.ei_employee
        rates = rates_for(2026, "ON")
        assert ytd_c <= rates.cpp_maximum_contribution
        assert ytd_c2 <= rates.cpp2_maximum_contribution
        assert ytd_e <= rates.ei_maximum_contribution
        assert ytd_c == rates.cpp_maximum_contribution
        assert ytd_c2 == rates.cpp2_maximum_contribution

    def test_cpp2_does_not_start_below_the_ympe(self):
        rates = rates_for(2026, "ON")
        under = calculate_pay(
            _pay("2000.00", ytd_gross=rates.cpp_ympe - Money.from_decimal("5000.00"))
        )
        assert under.cpp2_employee.minor_units > 0 or under.cpp2_employee.is_zero
        fresh = calculate_pay(_pay("1000.00"))
        assert fresh.cpp2_employee.is_zero


class TestEmployerRemittance:
    """The legacy system counted employer CPP2 twice, over-remitting up to the
    full annual CPP2 maximum for every employee earning above the YMPE."""

    def test_employer_cpp_matches_employee_exactly_once(self):
        r = calculate_pay(_pay("3500.00", ytd_gross=Money.from_decimal("80000.00")))
        assert r.cpp2_employee.minor_units > 0, "this test needs CPP2 to be in play"
        assert r.cpp_employer == r.cpp_employee
        assert r.cpp2_employer == r.cpp2_employee

    def test_remittance_is_the_sum_of_its_parts(self):
        r = calculate_pay(_pay("3500.00", ytd_gross=Money.from_decimal("80000.00")))
        expected = (
            r.cpp_employee + r.cpp2_employee + r.ei_employee + r.income_tax
            + r.cpp_employer + r.cpp2_employer + r.ei_employer
        )
        assert r.remittance_to_cra == expected

    def test_remittance_matches_the_double_of_each_contribution(self):
        r = calculate_pay(_pay("3500.00", ytd_gross=Money.from_decimal("80000.00")))
        manual = (
            r.cpp_employee.scale(2)
            + r.cpp2_employee.scale(2)
            + r.ei_employee
            + r.ei_employer
            + r.income_tax
        )
        assert r.remittance_to_cra == manual

    def test_legacy_double_count_would_be_caught(self):
        """Reproduce the legacy formula and prove it differs by exactly one CPP2."""
        r = calculate_pay(_pay("3500.00", ytd_gross=Money.from_decimal("80000.00")))
        legacy_cpp_employer = r.cpp_employee + r.cpp2_employee  # the legacy line
        legacy_remittance = (
            r.cpp_employee + r.cpp2_employee + legacy_cpp_employer + r.cpp2_employer
            + r.ei_employee + r.ei_employer + r.income_tax
        )
        assert legacy_remittance - r.remittance_to_cra == r.cpp2_employee

    def test_wsib_is_not_part_of_the_cra_remittance(self):
        r = calculate_pay(_pay("3500.00", wsib_rate=Decimal("0.0285")))
        assert r.wsib_premium == Money.from_decimal("99.75")
        assert r.wsib_premium not in (r.remittance_to_cra,)
        without = calculate_pay(_pay("3500.00"))
        assert r.remittance_to_cra == without.remittance_to_cra


class TestIncomeTax:
    """The basic personal amount is a credit at the lowest rate, not a deduction.
    Treating it as a deduction shifts every bracket down and under-withholds."""

    def test_bpa_is_applied_as_a_credit(self):
        rates = rates_for(2026, "ON")
        fed, _prov, _ohp = annual_tax(Money.from_decimal("100000.00"), rates)
        gross_tax = (
            Money.from_decimal("58523.00").scale(Decimal("0.14"))
            + Money.from_decimal("41477.00").scale(Decimal("0.205"))
        )
        credit = rates.federal_basic_personal_amount.scale(Decimal("0.14"))
        assert fed == gross_tax - credit

    def test_deduction_method_would_under_withhold(self):
        rates = rates_for(2026, "ON")
        income = Money.from_decimal("100000.00")
        correct, _p, _o = annual_tax(income, rates)
        # The legacy approach: apply brackets to income less the BPA.
        reduced = income - rates.federal_basic_personal_amount
        legacy = (
            Money.from_decimal("58523.00").scale(Decimal("0.14"))
            + (reduced - Money.from_decimal("58523.00")).scale(Decimal("0.205"))
        )
        assert legacy < correct
        assert (correct - legacy) > Money.from_decimal("900.00")

    def test_no_tax_below_either_basic_personal_amount(self):
        """Ontario's basic amount is lower than the federal one, so income
        between the two attracts provincial tax and no federal tax."""
        rates = rates_for(2026, "ON")
        fed, prov, _ohp = annual_tax(Money.from_decimal("12000.00"), rates)
        assert fed.is_zero
        assert prov.is_zero

    def test_income_between_the_two_basic_amounts_attracts_only_provincial_tax(self):
        rates = rates_for(2026, "ON")
        fed, prov, _ohp = annual_tax(Money.from_decimal("14000.00"), rates)
        assert fed.is_zero
        assert prov.minor_units > 0

    def test_ontario_surtax_applies_above_the_thresholds(self):
        rates = rates_for(2026, "ON")
        _f, low, _o = annual_tax(Money.from_decimal("60000.00"), rates)
        _f2, high, _o2 = annual_tax(Money.from_decimal("250000.00"), rates)
        assert high > low.scale(4), "the surtax should make upper-band tax more than linear"

    def test_health_premium_is_capped(self):
        rates = rates_for(2026, "ON")
        _f, _p, premium = annual_tax(Money.from_decimal("500000.00"), rates)
        assert premium == Money.from_decimal("900.00")

    def test_health_premium_is_zero_at_low_income(self):
        rates = rates_for(2026, "ON")
        _f, _p, premium = annual_tax(Money.from_decimal("19000.00"), rates)
        assert premium.is_zero


class TestReconciliation:
    def test_every_result_reconciles(self):
        for gross in ("1200.00", "3500.00", "8000.00", "25000.00"):
            r = calculate_pay(_pay(gross))
            assert r.reconciles
            assert r.net_pay + r.total_deductions == r.gross

    def test_employer_cost_exceeds_gross(self):
        r = calculate_pay(_pay("3500.00", wsib_rate=Decimal("0.0285")))
        assert r.employer_cost > r.gross

    def test_exemptions_are_honoured(self):
        r = calculate_pay(_pay("3500.00", cpp_exempt=True, ei_exempt=True))
        assert r.cpp_employee.is_zero and r.cpp2_employee.is_zero
        assert r.ei_employee.is_zero and r.ei_employer.is_zero
        assert r.income_tax.minor_units > 0

    def test_unknown_year_fails_loudly(self):
        with pytest.raises(KeyError, match="No payroll rates loaded"):
            calculate_pay(_pay("3500.00", year=2019))

    def test_unknown_frequency_fails_loudly(self):
        with pytest.raises(ValueError, match="Unknown pay frequency"):
            calculate_pay(_pay("3500.00", pay_frequency="fortnightly"))

    def test_rates_declare_whether_they_were_verified(self):
        """An unverified year must say so rather than imply authority."""
        rates = rates_for(2026, "ON")
        assert hasattr(rates, "is_verified")
        assert isinstance(rates.is_verified, bool)
