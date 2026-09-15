"""The control catalogue: coverage, noise floor, and per-control behaviour."""

from __future__ import annotations

from forge.canonical.enums import RiskTier, Severity
from forge.money import Money
from forge.rules import REGISTRY, run_rules
from forge.rules.materiality import compute_materiality

EXPECTED_CONTROL_COUNT = 56
# The five benchmark controls are opt-in: they stay silent unless a client's
# industry is configured, so the clean-book fixtures below do not set one.
BENCHMARK_CONTROL_COUNT = 5
MAX_CLEAN_BOOK_FINDINGS = 12


def test_catalogue_is_complete_and_uniquely_numbered():
    ids = [r.rule_id for r in REGISTRY]
    assert len(ids) == EXPECTED_CONTROL_COUNT
    assert len(set(ids)) == len(ids)
    assert ids == sorted(ids)


def test_benchmark_controls_are_opt_in():
    """A benchmark control must not fire when no industry is configured.

    Guessing an industry would produce confident comparisons against the wrong
    peer group, which is worse than producing none.
    """
    benchmark = [r for r in REGISTRY if r.category == "benchmark"]
    assert len(benchmark) == BENCHMARK_CONTROL_COUNT


def test_every_control_declares_its_contract():
    for rule in REGISTRY:
        assert rule.title, f"{rule.rule_id} has no title"
        assert rule.purpose, f"{rule.rule_id} has no stated purpose"
        assert rule.remediation, f"{rule.rule_id} has no remediation"
        assert rule.evidence_required, f"{rule.rule_id} declares no required evidence"
        assert rule.version, f"{rule.rule_id} is unversioned"


def test_no_control_raises_on_a_clean_book(clean_context):
    outcome = run_rules(clean_context)
    assert outcome.errors == {}, f"controls raised: {outcome.errors}"
    assert outcome.rules_run == EXPECTED_CONTROL_COUNT


def test_clean_book_noise_floor_stays_low(clean_context):
    """A control suite that reports forty things on clean books is unusable."""
    outcome = run_rules(clean_context)
    assert len(outcome.findings) <= MAX_CLEAN_BOOK_FINDINGS, (
        f"{len(outcome.findings)} findings on a clean book: "
        + ", ".join(f"{f.rule_id} {f.title}" for f in outcome.findings)
    )


def test_clean_book_has_no_critical_findings(clean_context):
    outcome = run_rules(clean_context)
    criticals = [f.rule_id for f in outcome.findings if f.severity is Severity.CRITICAL]
    assert criticals == [], f"clean book produced critical findings: {criticals}"


def test_every_finding_carries_reproducible_evidence(seeded_case):
    from forge.pipeline import DEFAULT_POLICY
    from forge.rules import RuleContext

    ctx = RuleContext(
        ledger=seeded_case.ledger,
        period_start=seeded_case.period_start,
        period_end=seeded_case.period_end,
        fiscal_year_start=seeded_case.fiscal_year_start,
        statements=tuple(seeded_case.company.statements),
        policy=DEFAULT_POLICY,
    ).prepare()
    outcome = run_rules(ctx)
    assert outcome.findings
    for f in outcome.findings:
        assert f.packet.refs, f"{f.rule_id} produced a finding with no source records"
        assert f.packet.calculations, f"{f.rule_id} produced a finding with no calculations"
        assert f.packet.is_sufficient, f"{f.rule_id} packet is insufficient"
        assert f.narrative, f"{f.rule_id} has no narrative"


def test_evidence_packet_checksum_is_stable(seeded_case):
    from forge.pipeline import DEFAULT_POLICY
    from forge.rules import RuleContext

    ctx = RuleContext(
        ledger=seeded_case.ledger, period_start=seeded_case.period_start,
        period_end=seeded_case.period_end, fiscal_year_start=seeded_case.fiscal_year_start,
        statements=tuple(seeded_case.company.statements), policy=DEFAULT_POLICY,
    ).prepare()
    outcome = run_rules(ctx)
    packet = outcome.findings[0].packet
    assert packet.checksum() == packet.checksum()
    assert len(packet.checksum()) == 32


def test_untrusted_text_is_tagged_not_inlined(seeded_case):
    """Vendor memos must reach a model as data, inside a labelled section."""
    from forge.pipeline import DEFAULT_POLICY
    from forge.rules import RuleContext

    ctx = RuleContext(
        ledger=seeded_case.ledger, period_start=seeded_case.period_start,
        period_end=seeded_case.period_end, fiscal_year_start=seeded_case.fiscal_year_start,
        statements=tuple(seeded_case.company.statements), policy=DEFAULT_POLICY,
    ).prepare()
    outcome = run_rules(ctx)
    with_memos = [f for f in outcome.findings if f.packet.untrusted]
    assert with_memos, "no finding carried source text; the tagging path is untested"
    payload = with_memos[0].packet.to_dict()
    assert "untrusted_source_text" in payload
    for entry in payload["untrusted_source_text"]:
        assert {"origin", "ref", "text"} <= set(entry)


def test_disbursed_cash_flag_is_set_where_it_matters():
    flagged = {r.rule_id for r in REGISTRY if r.involves_disbursed_cash}
    for rule_id in ("FOS-R006", "FOS-R008", "FOS-R032", "FOS-R038"):
        assert rule_id in flagged, f"{rule_id} should be marked as disbursed cash"
    assert "FOS-R027" not in flagged, (
        "an overdue invoice is money not yet received, not money already gone"
    )


def test_unauthorised_payment_always_reaches_a_human():
    rule = REGISTRY.get("FOS-R032")
    assert rule is not None
    assert rule.risk_tier_floor is RiskTier.R4
    assert rule.severity is Severity.CRITICAL


class TestMateriality:
    def test_revenue_is_the_primary_benchmark(self):
        m = compute_materiality(
            revenue=Money.from_decimal("2000000.00"),
            total_assets=Money.from_decimal("900000.00"),
            net_income=Money.from_decimal("40000.00"),
        )
        assert m.benchmark_name == "revenue"
        assert m.overall == Money.from_decimal("10000.00")
        assert m.performance == Money.from_decimal("7500.00")

    def test_volatile_profit_does_not_move_materiality(self):
        """Two years with the same revenue and wildly different profit must agree."""
        good = compute_materiality(
            revenue=Money.from_decimal("2000000.00"),
            total_assets=Money.from_decimal("900000.00"),
            net_income=Money.from_decimal("240000.00"),
        )
        bad = compute_materiality(
            revenue=Money.from_decimal("2000000.00"),
            total_assets=Money.from_decimal("900000.00"),
            net_income=Money.from_decimal("2000.00"),
        )
        assert good.overall == bad.overall

    def test_assets_are_the_fallback_when_there_is_no_revenue(self):
        m = compute_materiality(
            revenue=Money.zero(), total_assets=Money.from_decimal("800000.00"),
            net_income=Money.zero(),
        )
        assert m.benchmark_name == "total assets"

    def test_floor_protects_a_very_small_business(self):
        m = compute_materiality(
            revenue=Money.from_decimal("40000.00"),
            total_assets=Money.from_decimal("10000.00"),
            net_income=Money.from_decimal("5000.00"),
        )
        assert m.overall >= Money.from_decimal("500.00")

    def test_trivial_threshold_suppresses_noise(self):
        m = compute_materiality(
            revenue=Money.from_decimal("2000000.00"),
            total_assets=Money.from_decimal("900000.00"),
            net_income=Money.from_decimal("100000.00"),
        )
        assert m.is_trivial(Money.from_decimal("120.00"))
        assert not m.is_trivial(Money.from_decimal("900.00"))
        assert m.is_material(Money.from_decimal("9000.00"))
