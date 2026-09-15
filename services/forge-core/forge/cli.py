"""ForgeOS command line.

Everything the system does is reachable from here without a database, an API key
or a network connection, which is what makes it possible to demonstrate, test and
regression-gate the product on any machine in seconds.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import typer

from .agents import ModelGateway
from .bench import BenchThresholds, build_seeded_case, run_bench
from .connectors.fixture_contractor import build_contractor_company
from .pipeline import run_continuous_controller
from .report import render_diagnostic, write_evidence_bundle
from .rules import REGISTRY

app = typer.Typer(
    add_completion=False,
    help="ForgeOS: evidence-first finance operating system.",
    no_args_is_help=True,
)


def _echo_money(label: str, value) -> None:
    typer.echo(f"  {label:<34s} {value.format():>16s}")


@app.command()
def controls(
    category: str | None = typer.Option(None, help="Filter by control category"),
    as_json: bool = typer.Option(False, "--json", help="Emit the catalogue as JSON"),
) -> None:
    """List the control catalogue."""
    rules = [r for r in REGISTRY if category is None or r.category == category]
    if as_json:
        typer.echo(json.dumps([r.describe() for r in rules], indent=2))
        return
    typer.echo(f"{len(rules)} control(s)\n")
    current = None
    for rule in rules:
        if rule.category != current:
            current = rule.category
            typer.echo(f"\n{current.upper()}")
        flag = " [cash]" if rule.involves_disbursed_cash else ""
        typer.echo(
            f"  {rule.rule_id} v{rule.version}  {rule.severity.value:<8s} "
            f"{rule.risk_tier_floor.value}  {rule.title}{flag}"
        )


@app.command()
def review(
    period_end: str = typer.Option("2026-06-30", help="Last day of the period, YYYY-MM-DD"),
    months: int = typer.Option(1, help="Length of the period in months"),
    seeded: bool = typer.Option(
        False, "--seeded", help="Plant the ForgeBench error set in the company first"
    ),
    with_review: bool = typer.Option(
        False, "--with-review", help="Run the agent review hierarchy on material items"
    ),
    out: Path | None = typer.Option(None, help="Write the HTML review to this path"),
    evidence: Path | None = typer.Option(None, help="Write the JSON evidence bundle here"),
) -> None:
    """Run a Continuous Controller pass over the demonstration company."""
    end = date.fromisoformat(period_end)
    start_month = end.month - months + 1
    start_year = end.year
    while start_month <= 0:
        start_month += 12
        start_year -= 1
    start = date(start_year, start_month, 1)

    if seeded:
        case = build_seeded_case(period_start=start, period_end=end)
        ledger, statements = case.ledger, case.company.statements
    else:
        company = build_contractor_company()
        ledger, statements = company.ledger, company.statements

    gateway = ModelGateway.offline() if with_review else None
    run = run_continuous_controller(
        ledger,
        period_start=start,
        period_end=end,
        statements=statements,
        gateway=gateway,
        review=with_review,
    )

    typer.echo(f"\n{run.entity_name}")
    typer.echo(f"Period {start.isoformat()} to {end.isoformat()}\n")
    typer.echo("Data gate: " + ("PASSED" if run.passed_data_gate else "FAILED"))
    for g in run.gate_failures:
        typer.echo(f"  ! {g.check}: {g.detail}")
    typer.echo(f"\nMateriality: {run.ctx.materiality.describe()}")
    typer.echo(f"Controls run: {run.outcome.rules_run}   Findings: {len(run.findings)}\n")
    _echo_money("Revenue (period)", run.ctx.pl.revenue.total)
    _echo_money("Net income (period)", run.ctx.pl.net_income)
    _echo_money("Receivables outstanding", run.ctx.ar.total)
    _echo_money("Recoverable cash identified", run.recoverable_cash)
    typer.echo("")

    for item in run.work_items:
        f = item.finding
        typer.echo(
            f"  [{f.severity.value:<8s}] {item.risk.tier.value} {f.rule_id} "
            f"{abs(f.exposure).format():>14s}  {f.title}"
        )
    if run.outcome.errors:
        typer.echo("\nCONTROL ERRORS:")
        for rid, msg in run.outcome.errors.items():
            typer.echo(f"  {rid}: {msg}")

    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_diagnostic(run), encoding="utf-8")
        typer.echo(f"\nReview written to {out}")
    if evidence:
        write_evidence_bundle(run, evidence)
        typer.echo(f"Evidence bundle written to {evidence}")


@app.command()
def bench(
    as_json: bool = typer.Option(False, "--json", help="Emit the result as JSON"),
    strict: bool = typer.Option(True, help="Exit non-zero when the release gate fails"),
) -> None:
    """Run ForgeBench and enforce the release gate."""
    case = build_seeded_case()
    result = run_bench(case)
    passed, failures = BenchThresholds().evaluate(result)

    if as_json:
        payload = result.to_dict()
        payload["gate_passed"] = passed
        payload["gate_failures"] = failures
        typer.echo(json.dumps(payload, indent=2))
    else:
        typer.echo(result.report())
        typer.echo("\nRelease gate: " + ("PASS" if passed else "FAIL"))
        for f in failures:
            typer.echo(f"  ! {f}")

    if strict and not passed:
        raise typer.Exit(code=1)


@app.command()
def tieout(
    period_end: str = typer.Option("2026-06-30", help="Last day of the period"),
) -> None:
    """Prove that the generated books reconcile, statement by statement."""
    from .engine.reconcile import reconcile

    end = date.fromisoformat(period_end)
    company = build_contractor_company()
    ledger = company.ledger
    run = run_continuous_controller(
        ledger, period_start=date(end.year, 1, 1), period_end=end,
        statements=company.statements,
    )
    tb, bs = run.ctx.tb, run.ctx.bs
    typer.echo("\nTie-out checks")
    typer.echo(f"  trial balance nets to zero      {tb.closing_imbalance.format():>14s}")
    typer.echo(f"  period debits equal credits     {tb.activity_imbalance.format():>14s}")
    typer.echo(f"  balance sheet equation          {bs.equation_difference.format():>14s}")
    typer.echo(f"  unbalanced entries              {len(tb.unbalanced_transactions):>14d}")

    ok = 0
    for st in company.statements:
        result = reconcile(ledger, st)
        if result.is_reconciled:
            ok += 1
        else:
            typer.echo(
                f"  ! {st.bank_account_id} {st.start.isoformat()} "
                f"unexplained {result.unexplained_difference.format()}"
            )
    typer.echo(f"  bank statements reconciled      {ok:>7d}/{len(company.statements)}")
    typer.echo("")
    if not run.passed_data_gate or ok != len(company.statements):
        raise typer.Exit(code=1)


@app.command()
def route(
    period_end: str = typer.Option("2026-06-30"),
    seeded: bool = typer.Option(True, "--seeded/--clean"),
) -> None:
    """Show how findings are scored and routed through the review hierarchy."""
    end = date.fromisoformat(period_end)
    start = date(end.year, end.month, 1)
    if seeded:
        case = build_seeded_case(period_start=start, period_end=end)
        ledger, statements = case.ledger, case.company.statements
    else:
        company = build_contractor_company()
        ledger, statements = company.ledger, company.statements
    run = run_continuous_controller(
        ledger, period_start=start, period_end=end, statements=statements
    )
    for item in run.work_items:
        typer.echo(
            f"\n{item.risk.tier.value}  score {item.risk.score:5.2f}  {item.finding.rule_id}  "
            f"{item.finding.title}"
        )
        typer.echo(
            f"     prepared by {item.plan.preparer.value}; reviewed by "
            + (", ".join(r.value for r in item.plan.reviewers) or "no one")
        )
        for factor in item.risk.factors:
            typer.echo(f"       - {factor.reason}")


if __name__ == "__main__":  # pragma: no cover
    app()
