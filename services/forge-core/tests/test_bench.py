"""ForgeBench is the release gate. If this file goes soft, nothing else matters."""

from __future__ import annotations

from forge.bench import BenchThresholds, run_bench


def test_every_planted_error_is_detected(seeded_case):
    result = run_bench(seeded_case)
    assert result.missed == (), (
        "missed: " + ", ".join(f"{e.error_id} {e.kind}" for e in result.missed)
    )
    assert result.recall == 1


def test_all_critical_errors_are_detected(seeded_case):
    result = run_bench(seeded_case)
    assert result.critical_recall == 1


def test_no_control_raises_during_a_bench_run(seeded_case):
    result = run_bench(seeded_case)
    assert result.outcome.errors == {}


def test_decoys_are_not_reported(seeded_case):
    """Legitimate-but-unusual activity must not be raised as a problem."""
    result = run_bench(seeded_case)
    assert result.decoy_false_positives == 0, (
        "reported decoys: "
        + ", ".join(
            f"{d.error.kind} via {', '.join(f.rule_id for f in d.findings)}"
            for d in result.decoy_hits
            if d.detected
        )
    )


def test_release_gate_passes(seeded_case):
    result = run_bench(seeded_case)
    passed, failures = BenchThresholds().evaluate(result)
    assert passed, f"release gate failures: {failures}"


def test_bench_is_deterministic():
    from forge.bench import build_seeded_case

    first = run_bench(build_seeded_case())
    second = run_bench(build_seeded_case())
    assert first.detected_count == second.detected_count
    assert len(first.outcome.findings) == len(second.outcome.findings)
    assert [f.finding_id for f in first.outcome.sorted_findings()] == [
        f.finding_id for f in second.outcome.sorted_findings()
    ]


def test_bench_runs_fast_enough_to_sit_in_ci(seeded_case):
    result = run_bench(seeded_case)
    assert result.duration_ms < 3000, (
        "a quality gate that is slow gets skipped; keep the suite under three seconds"
    )


def test_gate_fails_when_a_critical_error_is_missed(seeded_case):
    """The gate must actually be capable of failing."""
    result = run_bench(seeded_case)
    strict = BenchThresholds(min_recall=__import__("decimal").Decimal("1.1"))
    passed, failures = strict.evaluate(result)
    assert not passed and failures


def test_detection_is_matched_by_evidence_not_by_rule_name(seeded_case):
    """A control must point at the planted records, not merely fire."""
    result = run_bench(seeded_case)
    evidence_matched = [
        d for d in result.detections if d.detected and not d.error.match_by_rule_only
    ]
    assert evidence_matched, "no error was matched on evidence; the check is vacuous"
    for detection in evidence_matched:
        identifiers = set(detection.error.txn_ids) | set(detection.error.account_ids)
        referenced = set()
        for f in detection.findings:
            referenced |= {r.ref_id for r in f.packet.refs}
            referenced.add(f.finding_id.partition(":")[2])
        assert identifiers & referenced or any(
            i in r for i in identifiers for r in referenced
        ), f"{detection.error.error_id} was matched without pointing at its records"
