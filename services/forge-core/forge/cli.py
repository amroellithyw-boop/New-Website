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
from .agents.catalogue import CATALOGUE, PROVIDER_ENV, available_models
from .bench import BenchThresholds, build_seeded_case, run_bench
from .cfo import cash_forecast, health_score, working_capital
from .clients import ClientProfile
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
def providers() -> None:
    """Show which model providers are configured and what routing will use."""

    live = {m.provider for m in available_models()}
    typer.echo("\nProviders (set the environment variable to enable):\n")
    for provider, (var, base) in PROVIDER_ENV.items():
        if provider == "offline":
            continue
        state = "ENABLED " if provider in live else "disabled"
        typer.echo(f"  {state}  {provider:<11s} {var:<22s} {base or '(SDK)'}")
    gw = ModelGateway.from_environment()
    fams = sorted(gw.families_available())
    typer.echo(f"\nIndependent model families available: {len(fams)}  {fams}")
    if len(fams) < 2:
        typer.echo("  Material reviews will be marked not independent until a second family is configured.")
    typer.echo("\nRouting preview (cheapest capable model per tier and role):")
    from .canonical.enums import AgentRole, RiskTier
    for tier in (RiskTier.R1, RiskTier.R2, RiskTier.R3, RiskTier.R4):
        for role in (AgentRole.BOOKKEEPER, AgentRole.CONTROLLER, AgentRole.ADVERSARY):
            try:
                spec = gw.select(tier=tier, role=role)
                typer.echo(f"  {tier.value} {role.value:<18s} -> {spec.key:<40s} q{spec.quality} "
                           f"${spec.input_cost_per_mtok}/{spec.output_cost_per_mtok} per MTok")
            except Exception as exc:  # noqa: BLE001
                typer.echo(f"  {tier.value} {role.value:<18s} -> {exc}")
    typer.echo(f"\nCatalogue: {len(CATALOGUE)} models. Edit forge/agents/catalogue.py to add one.")


@app.command()
def onboard(
    intake: Path = typer.Argument(..., help="JSON file in the legacy intake shape or the profile shape"),
    out: Path | None = typer.Option(None, help="Write the normalised profile JSON here"),
    plan: bool = typer.Option(False, "--plan", help="Draft the onboarding plan with the configured models"),
) -> None:
    """Turn an intake questionnaire into a client profile, and optionally a plan."""
    raw = json.loads(intake.read_text())
    profile = ClientProfile.from_dict(raw) if "client_id" in raw else ClientProfile.from_legacy_intake(raw)
    pol = profile.to_policy()
    typer.echo(f"\n{profile.business_name}  ({profile.client_id})")
    typer.echo(f"  industry     {pol.industry_label or 'not set'}   tier {pol.size_tier}")
    typer.echo(f"  province     {profile.province}  {pol.sales_tax_label} {profile.sales_tax.percent_label}")
    typer.echo(f"  fiscal year  ends {profile.fiscal_year_end_month}/{profile.fiscal_year_end_day}")
    typer.echo(f"  services     {', '.join(profile.services)}")
    typer.echo(f"  workflows    {', '.join(pol.workflows)}")
    typer.echo(f"  controls     {', '.join(pol.control_categories)}")
    typer.echo(f"  deferred rev {'expected' if pol.deferred_revenue_expected else 'not expected'}   T5018 {'yes' if pol.files_t5018 else 'no'}")
    if out:
        out.write_text(profile.to_json())
        typer.echo(f"\nProfile written to {out}")
    if plan:
        from .agents.communications import draft_onboarding_plan

        gw = ModelGateway.from_environment()
        result = draft_onboarding_plan(profile, gateway=gw)
        typer.echo("\n" + result.model_dump_json(indent=2))
        typer.echo(f"\nCost: {gw.cost_summary()['cost']}")


@app.command()
def forecast(
    period_end: str = typer.Option("2026-06-30"),
    profile: Path | None = typer.Option(None, help="Client profile JSON; the demo company is used without one"),
) -> None:
    """Working capital, health score and a thirteen-week cash forecast."""
    end = date.fromisoformat(period_end)
    company = build_contractor_company()
    prof = ClientProfile.from_json(profile.read_text()) if profile else ClientProfile(
        client_id="DEMO", business_name=company.ledger.entity.name, naics_code="561730",
        annual_revenue_estimate=None, services=("bookkeeping", "cfo_advisory"))
    run = run_continuous_controller(company.ledger, period_start=date(end.year, end.month, 1), period_end=end,
                                    statements=company.statements, profile=prof)
    wc = working_capital(run.ctx)
    hs = health_score(run.ctx, findings=run.findings)
    fc = cash_forecast(run.ctx)
    typer.echo(f"\n{run.entity_name}  as at {end.isoformat()}\n")
    typer.echo(f"Health score {hs.score}/100  grade {hs.grade}")
    for f in hs.factors:
        typer.echo(f"  {f.points:>3}/{f.maximum:<3} {f.label:<20s} {f.detail}")
    typer.echo("\nWorking capital")
    for k, v in wc.to_dict().items():
        if k != "as_of":
            typer.echo(f"  {k:<28s} {v}")
    typer.echo("\nThirteen-week cash")
    typer.echo(f"  {'wk':>2} {'start':<11} {'receipts':>12} {'payroll':>12} {'payables':>12} {'recurring':>11} {'debt':>10} {'closing':>13}")
    for w in fc.weeks:
        typer.echo(f"  {w.week:>2} {w.start.isoformat():<11} {w.receipts.format():>12} {w.payroll.format():>12} "
                   f"{w.payables.format():>12} {w.recurring.format():>11} {w.debt_service.format():>10} {w.closing.format():>13}")
    low = fc.lowest_point
    if low:
        typer.echo(f"\nLowest point: week {low[0]} at {low[1].format()}" + ("  NEGATIVE" if fc.goes_negative else ""))


@app.command()
def document(
    path: Path = typer.Argument(..., help="PDF or image to process"),
    profile: Path | None = typer.Option(None, help="Client profile JSON"),
    out: Path | None = typer.Option(None, help="Write the full result JSON here"),
) -> None:
    """Run the three-pass document pipeline and show the proposed entries."""
    import mimetypes

    from .documents import VendorCache, process_document

    media = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    prof = ClientProfile.from_json(profile.read_text()) if profile else ClientProfile(
        client_id="DEMO", business_name="Demo Client", naics_code="561730")
    gw = ModelGateway.from_environment()
    if not gw.families_available():
        typer.echo("No model provider configured; running with the offline stub, which extracts nothing.")
    cache = VendorCache.load(prof.client_id, Path(".forge") / "vendors")
    result = process_document(path.read_bytes(), media, profile=prof, gateway=gw, vendor_cache=cache)
    cache.save()
    ex = result.extracted
    if ex:
        typer.echo(f"\n{ex.document_type} from {ex.vendor_or_issuer or 'unknown'}  "
                   f"{len(ex.transactions)} transactions  confidence {ex.confidence}")
    typer.echo(f"proposed {len(result.prepared_entries)} entries, rejected {len(result.rejected_entries)}, "
               f"warnings {len(result.warnings)}, cost {gw.cost_summary()['cost']}")
    for e in result.prepared_entries:
        typer.echo(f"\n  {e['date']}  {e['description']}  ({e['tax_treatment']}, confidence {e['confidence']})")
        for ln in e["lines"]:
            typer.echo(f"     {ln['side']:<6} {ln['account_number']:<6} {ln['account_name']:<30s} {ln['amount']:>12}")
    for entry, reason in result.rejected_entries:
        typer.echo(f"\n  REJECTED {entry.description}: {reason}")
    for w in result.warnings:
        typer.echo(f"  warning: {w}")
    if out:
        out.write_text(json.dumps(result.to_dict(), indent=2, default=str))
        typer.echo(f"\nResult written to {out}")


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
