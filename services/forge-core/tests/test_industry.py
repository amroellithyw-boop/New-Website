"""Industry packs and the benchmark controls built on them."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from forge.industry import load_packs, pack_for, packs_with_benchmarks
from forge.industry.ranges import parse_money_range, parse_percent_range
from forge.money import Money
from forge.pipeline import DEFAULT_POLICY, run_continuous_controller

LANDSCAPING = "561730"


class TestRangeParsing:
    @pytest.mark.parametrize(
        "text,low,high",
        [
            ("35–45%", "0.35", "0.45"),
            ("6–10%", "0.06", "0.10"),
            ("8%", "0.08", "0.08"),
        ],
    )
    def test_percent_ranges(self, text, low, high):
        r = parse_percent_range(text)
        assert r is not None
        assert r.low == Decimal(low) and r.high == Decimal(high)

    def test_negative_lower_bound_is_a_sign_not_a_separator(self):
        """'-10-15%' means minus ten to plus fifteen."""
        r = parse_percent_range("-10–15%")
        assert r is not None
        assert r.low == Decimal("-0.10")
        assert r.high == Decimal("0.15")

    @pytest.mark.parametrize(
        "text,low,high",
        [
            ("$100K–$500K", "100000.00", "500000.00"),
            ("$2M–$10M", "2000000.00", "10000000.00"),
        ],
    )
    def test_money_ranges(self, text, low, high):
        r = parse_money_range(text)
        assert r is not None
        assert r.low == Money.from_decimal(low)
        assert r.high == Money.from_decimal(high)

    def test_open_ended_ranges(self):
        under = parse_money_range("<$100K")
        over = parse_money_range("$10M+")
        assert under is not None and under.low is None
        assert over is not None and over.high is None
        assert under.contains(Money.from_decimal("50000.00"))
        assert over.contains(Money.from_decimal("40000000.00"))

    def test_unparseable_returns_none_rather_than_guessing(self):
        assert parse_percent_range("varies") is None
        assert parse_money_range("") is None
        assert parse_percent_range(None) is None

    def test_position_reports_which_side(self):
        r = parse_percent_range("35–45%")
        assert r.position(Decimal("0.20")) == "below"
        assert r.position(Decimal("0.40")) == "within"
        assert r.position(Decimal("0.60")) == "above"
        assert r.shortfall(Decimal("0.30")) == Decimal("0.05")
        assert r.excess(Decimal("0.50")) == Decimal("0.05")


class TestIndustryPacks:
    def test_packs_load_and_join(self):
        packs = load_packs()
        assert len(packs) >= 20
        assert len(packs_with_benchmarks()) >= 19

    def test_every_pack_has_a_label(self):
        for code, pack in load_packs().items():
            assert pack.label, f"{code} has no label"
            assert pack.naics_code == code

    def test_landscaping_pack_is_complete(self):
        p = pack_for(LANDSCAPING)
        assert p is not None
        assert p.wsib_rate == Decimal("0.0285")
        assert p.deferred_revenue is True
        assert p.deferred_note
        assert p.common_errors
        assert len(p.size_tiers) == 5

    def test_size_tier_selection_tracks_revenue(self):
        p = pack_for(LANDSCAPING)
        tiers = [
            p.tier_for(Money.from_decimal(v)).name
            for v in ("80000.00", "350000.00", "1800000.00", "5000000.00", "20000000.00")
        ]
        assert tiers == ["startup", "micro", "small", "mid", "upper_mid"]

    def test_margins_tighten_as_a_business_grows(self):
        """A larger contractor should carry a lower expected gross margin."""
        p = pack_for(LANDSCAPING)
        small = p.effective_gross_margin(Money.from_decimal("1000000.00"))
        large = p.effective_gross_margin(Money.from_decimal("20000000.00"))
        assert small is not None and large is not None
        assert large.high < small.high

    def test_cost_lines_map_to_account_ranges(self):
        p = pack_for(LANDSCAPING)
        labour = p.cost_line_for("6100")
        assert labour is not None and labour.behaviour == "variable"
        assert p.cost_line_for("9999") is None

    def test_unknown_industry_returns_nothing(self):
        assert pack_for("000000") is None
        assert pack_for(None) is None


class TestBenchmarkControls:
    def _run(self, company, naics: str | None):
        policy = dict(DEFAULT_POLICY)
        if naics:
            policy["naics_code"] = naics
        return run_continuous_controller(
            company.ledger,
            period_start=date(2026, 6, 1),
            period_end=date(2026, 6, 30),
            statements=company.statements,
            policy=policy,
        )

    def test_silent_without_an_industry(self, clean_company):
        run = self._run(clean_company, None)
        assert [f for f in run.findings if f.category == "benchmark"] == []

    def test_gross_margin_below_the_band_is_reported_with_its_value(self, clean_company):
        run = self._run(clean_company, LANDSCAPING)
        margin = [f for f in run.findings if f.rule_id == "FOS-R051"]
        assert margin, "a 32.8% margin against a 35-45% band should be reported"
        finding = margin[0]
        assert finding.exposure.minor_units > 0
        assert "benchmark" in finding.narrative.lower()

    def test_missing_seasonal_deferral_is_reported(self, clean_company):
        run = self._run(clean_company, LANDSCAPING)
        assert any(f.rule_id == "FOS-R053" for f in run.findings)

    def test_benchmarks_never_claim_certainty(self, clean_company):
        """A benchmark is a prompt for a conversation, not a proven defect."""
        run = self._run(clean_company, LANDSCAPING)
        for f in run.findings:
            if f.category == "benchmark":
                assert f.confidence < Decimal("1.0"), f"{f.rule_id} claims certainty"

    def test_benchmark_findings_carry_evidence(self, clean_company):
        run = self._run(clean_company, LANDSCAPING)
        for f in run.findings:
            if f.category == "benchmark":
                assert f.packet.refs, f"{f.rule_id} cites no accounts"
                assert f.packet.calculations

    def test_benchmark_findings_name_their_source(self, clean_company):
        """The reader must be told these are practitioner benchmarks."""
        run = self._run(clean_company, LANDSCAPING)
        benchmark = [f for f in run.findings if f.category == "benchmark"]
        assert benchmark
        for f in benchmark:
            source = f.packet.context.get("benchmark_source", "")
            assert "not a published statistic" in source

    def test_controls_within_the_band_stay_silent(self, clean_company):
        """Labour at 27.5% against a 32-42% band must not be reported."""
        run = self._run(clean_company, LANDSCAPING)
        assert not any(f.rule_id == "FOS-R052" for f in run.findings)
        assert not any(f.rule_id == "FOS-R055" for f in run.findings)

    def test_wsib_control_needs_an_account_to_check(self, clean_company):
        run = self._run(clean_company, LANDSCAPING)
        assert not any(f.rule_id == "FOS-R054" for f in run.findings)
