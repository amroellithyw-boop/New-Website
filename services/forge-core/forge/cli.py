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


# ---------------------------------------------------------------------------
# Operations: one client, the whole firm, close, tax, brief, sales, outcomes.
# ---------------------------------------------------------------------------


def _period(period_end: str, months: int = 1) -> tuple[date, date]:
    end = date.fromisoformat(period_end)
    m, y = end.month - months + 1, end.year
    while m <= 0:
        m, y = m + 12, y - 1
    return date(y, m, 1), end


def _demo_profile(name: str) -> ClientProfile:
    from .money import Money

    return ClientProfile(client_id="DEMO", business_name=name, naics_code="238210", owner_name="Demo Owner",
                         annual_revenue_estimate=Money.from_decimal("2400000.00"), employees_full_time=8,
                         uses_subcontractors=True, services=("bookkeeping", "payroll", "tax", "cfo_advisory"))


def _load_profile(profile: Path | None, company) -> ClientProfile:
    return ClientProfile.from_json(profile.read_text()) if profile else _demo_profile(company.ledger.entity.name)


def _client_run(profile: Path | None, period_end: str, months: int, data_dir: Path | None, *, seeded: bool = False,
                with_review: bool = False):
    """The ledger is the demonstration company until a live connector mapping is finished."""
    from .operations import run_client

    start, end = _period(period_end, months)
    if seeded:
        case = build_seeded_case(period_start=start, period_end=end)
        company, ledger = case.company, case.ledger
    else:
        company = build_contractor_company()
        ledger = company.ledger
    prof = _load_profile(profile, company)
    gateway = ModelGateway.from_environment() if with_review else None
    return run_client(prof, ledger, period_start=start, period_end=end, statements=company.statements,
                      gateway=gateway, review=with_review, data_dir=data_dir)


@app.command()
def run(
    profile: Path | None = typer.Option(None, help="Client profile JSON; the demo company is used without one"),
    period_end: str = typer.Option("2026-06-30"),
    months: int = typer.Option(1),
    data_dir: Path = typer.Option(Path("data"), help="Where outcomes and knowledge for each client are kept"),
    seeded: bool = typer.Option(False, "--seeded", help="Plant the ForgeBench error set first"),
    with_review: bool = typer.Option(False, "--with-review", help="Run the model review hierarchy on material items"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Everything the finance team does for one client in one period: controls,
    health, cash, close, sales tax, provision, deadlines and drafted actions."""
    cr = _client_run(profile, period_end, months, data_dir, seeded=seeded, with_review=with_review)
    if as_json:
        typer.echo(json.dumps(cr.summary(), indent=2, default=str))
        return
    s = cr.summary()
    typer.echo(f"\n{s['client']}  {s['period']}   data gate {'PASSED' if s['passed_data_gate'] else 'FAILED'}")
    typer.echo(f"  health {s['health']['score']}/100 ({s['health']['grade']})   findings {s['findings']} {s['by_severity']}"
               f"   suppressed by learning {s['suppressed_by_learning']}")
    typer.echo(f"  recoverable cash {s['recoverable_cash']}   cash low point week {s['cash']['lowest_week']}"
               f"{'  GOES NEGATIVE' if s['cash']['goes_negative'] else ''}")
    typer.echo(f"  close {s['close']['percent_complete']}% ({s['close']['open']} open)   can lock: {s['close']['can_lock']}")
    typer.echo(f"  sales tax balance {s['sales_tax_balance']}   tax provision YTD {s['tax_provision']}")
    typer.echo(f"  drafts {s['drafts']['total']} ({s['drafts']['authorised_now']} authorised now)   model cost {s['cost_micros']} micro-dollars")
    if s["deadlines_30_days"]:
        typer.echo("  deadlines in thirty days: " + "; ".join(f"{k} {d}" for k, d in s["deadlines_30_days"]))
    typer.echo("")
    for item in cr.run.work_items:
        f = item.finding
        typer.echo(f"  [{f.severity.value:<8s}] {item.risk.tier.value} {f.rule_id} {abs(f.exposure).format():>14s}  {f.title}")
    typer.echo("\nRecord decisions with `forge outcome <finding-id> accepted|dismissed|corrected` so the system learns.")


@app.command("run-all")
def run_all(
    clients: Path = typer.Argument(..., help="Directory of client profile JSON files"),
    period_end: str = typer.Option("2026-06-30"),
    data_dir: Path = typer.Option(Path("data")),
    pipeline: Path | None = typer.Option(None, help="Sales pipeline JSON to fold into the queue"),
) -> None:
    """Run every client, then print the firm's prioritised queue and the daily brief."""
    from .growth import SalesPipeline
    from .operations import build_firm_queue, daily_brief

    runs = [_client_run(p, period_end, 1, data_dir) for p in sorted(clients.glob("*.json"))]
    if not runs:
        typer.echo("No profiles found; `forge onboard <intake.json> --out clients/<id>.json` creates one.")
        raise typer.Exit(1)
    sp = SalesPipeline.load(pipeline) if pipeline else None
    queue = build_firm_queue(runs, as_of=date.fromisoformat(period_end), pipeline=sp)
    typer.echo(daily_brief(queue, runs))
    typer.echo("\nQueue")
    for i in queue.top(30):
        typer.echo(f"  P{i.priority} {i.client:<22s} {i.kind:<10s} {i.title}" + (f"  due {i.due_on}" if i.due_on else ""))


@app.command()
def close(
    profile: Path | None = typer.Option(None),
    period_end: str = typer.Option("2026-06-30"),
    data_dir: Path = typer.Option(Path("data")),
    seeded: bool = typer.Option(False, "--seeded"),
) -> None:
    """The month-end close checklist, each task proven done, open or blocked."""
    cr = _client_run(profile, period_end, 1, data_dir, seeded=seeded)
    cl = cr.close
    typer.echo(f"\nClose {cl.period_start} to {cl.period_end}: {cl.percent_complete}% complete, "
               f"{'can lock' if cl.can_lock else 'cannot lock yet'}\n")
    for t in cl.tasks:
        mark = {"done": "x", "open": " ", "blocked": "!", "not_applicable": "-"}[t.status]
        typer.echo(f"  [{mark}] {t.title:<44s} {t.owner.value:<22s} {t.evidence}")
    typer.echo("\nDrafted adjustments")
    for wid, d in cr.drafts:
        if d.kind == "draft_journal_entry":
            typer.echo(f"  {wid}  {d.title}" + ("  (estimate)" if d.is_estimate else ""))
            for ln in d.lines:
                typer.echo(f"      {ln['side']:<6s} {ln['account_number']:<8s} {ln['account_name']:<32s} {ln['amount']:>12s}")


@app.command()
def tax(
    what: str = typer.Argument("return", help="return | calendar | provision | slips"),
    profile: Path | None = typer.Option(None),
    period_end: str = typer.Option("2026-06-30"),
    months: int = typer.Option(3, help="Return period length; three for a quarterly filer"),
    data_dir: Path = typer.Option(Path("data")),
) -> None:
    """Sales tax return working paper, filing calendar, corporate provision, or slip obligations."""
    from .tax import slip_obligations

    cr = _client_run(profile, period_end, months, data_dir)
    end = date.fromisoformat(period_end)
    if what == "return":
        r = cr.sales_tax
        typer.echo(f"\n{cr.profile.business_name}: sales tax return {r.period_start} to {r.period_end}\n")
        for k, v in r.to_dict().items():
            typer.echo(f"  {k:<32s} {v}")
    elif what == "calendar":
        typer.echo(f"\nFiling calendar for {cr.profile.business_name}, from {end}\n")
        for d in cr.deadlines:
            flag = "  (confirm frequency on the CRA account)" if d.confirm else ""
            typer.echo(f"  {d.due_on}  {d.kind:<20s} {d.description}{flag}")
    elif what == "provision":
        typer.echo(f"\nCorporate tax provision, fiscal year to {end}\n")
        for k, v in cr.provision.to_dict().items():
            typer.echo(f"  {k:<28s} {v}")
    elif what == "slips":
        slips = slip_obligations(cr.run.ctx, year=end.year, construction=cr.profile.files_t5018)
        typer.echo(f"\n{len(slips)} slip(s) to issue for {end.year}\n")
        for s in slips:
            typer.echo(f"  {s.slip}  {s.party_name:<32s} {s.amount_paid.format():>14s}")
    else:
        raise typer.BadParameter("what must be return, calendar, provision or slips")


@app.command()
def brief(
    profile: Path | None = typer.Option(None),
    period_end: str = typer.Option("2026-06-30"),
    data_dir: Path = typer.Option(Path("data")),
    proposal: bool = typer.Option(False, "--proposal", help="Render the prospect proposal instead of the owner brief"),
    firm: str = typer.Option("Profit Forge"),
    sender: str = typer.Option("Amro"),
) -> None:
    """The five things that matter this month for the owner, or a proposal for a prospect."""
    from .cfo import owner_brief
    from .growth import build_proposal, render_proposal

    cr = _client_run(profile, period_end, 1, data_dir)
    if proposal:
        typer.echo(render_proposal(build_proposal(cr.profile, cr.run, today=date.fromisoformat(period_end)), firm=firm, sender=sender))
    else:
        typer.echo(owner_brief(cr))


@app.command()
def sales(
    action: str = typer.Argument("list", help="list | add | move | due | outreach"),
    pipeline: Path = typer.Option(Path("data/pipeline.json")),
    prospect_id: str | None = typer.Option(None),
    name: str | None = typer.Option(None),
    naics: str | None = typer.Option(None),
    stage: str | None = typer.Option(None),
    note: str = typer.Option(""),
    firm: str = typer.Option("Profit Forge"),
    sender: str = typer.Option("Amro"),
) -> None:
    """The selling machine: pipeline stages, follow-ups due, and drafted outreach."""
    from .growth import STAGES, Prospect, SalesPipeline, diagnostic_fee, draft_outreach

    sp = SalesPipeline.load(pipeline)
    if action == "list":
        typer.echo(f"Funnel: {sp.funnel()}   conversion: {sp.conversion()}\n")
        for p in sp.prospects.values():
            typer.echo(f"  {p.prospect_id:<10s} {p.business_name:<28s} {p.stage:<20s} next: {p.next_action or ''} {p.next_action_on or ''}")
    elif action == "add":
        if not (prospect_id and name):
            raise typer.BadParameter("--prospect-id and --name are required")
        sp.add(Prospect(prospect_id=prospect_id, business_name=name, naics_code=naics))
        sp.save()
        typer.echo(f"Added {name} at stage lead")
    elif action == "move":
        if not (prospect_id and stage):
            raise typer.BadParameter(f"--prospect-id and --stage ({', '.join(STAGES)}) are required")
        sp.prospects[prospect_id].move(stage, note=note)
        sp.save()
        typer.echo(f"{prospect_id} -> {stage}; next: {sp.prospects[prospect_id].next_action}")
    elif action == "due":
        for p in sp.due_today():
            typer.echo(f"  {p.prospect_id:<10s} {p.business_name:<28s} {p.stage:<20s} {p.next_action}")
    elif action == "outreach":
        if not prospect_id:
            raise typer.BadParameter("--prospect-id is required")
        p = sp.prospects[prospect_id]
        prof = ClientProfile(client_id=p.prospect_id, business_name=p.business_name, naics_code=p.naics_code, province=p.province)
        gw = ModelGateway.from_environment()
        email = draft_outreach(prof, gateway=gw, sender=sender, firm=firm, diagnostic_fee=diagnostic_fee(prof).format())
        typer.echo(f"Subject: {email.subject}\n\n{email.body}\n\nCost: {gw.cost_summary()['cost']}")
    else:
        raise typer.BadParameter("action must be list, add, move, due or outreach")


@app.command()
def outcome(
    finding_id: str = typer.Argument(..., help="The finding id shown by `forge run`"),
    decision: str = typer.Argument(..., help="accepted | dismissed | corrected | deferred"),
    profile: Path | None = typer.Option(None),
    period_end: str = typer.Option("2026-06-30"),
    data_dir: Path = typer.Option(Path("data")),
    reason: str = typer.Option(""),
    by: str = typer.Option("operator"),
    seeded: bool = typer.Option(False, "--seeded"),
) -> None:
    """Record what happened to a finding. Three dismissals of one pattern suppress it;
    an acceptance makes it a known pattern and lowers its novelty next time."""
    from .learning import OutcomeLog

    if decision not in ("accepted", "dismissed", "corrected", "deferred"):
        raise typer.BadParameter("decision must be accepted, dismissed, corrected or deferred")
    cr = _client_run(profile, period_end, 1, None, seeded=seeded)
    finding = next((f for f in list(cr.run.findings) + list(cr.run.suppressed) if f.finding_id == finding_id), None)
    if finding is None:
        raise typer.BadParameter(f"{finding_id} is not a finding in this run")
    log = OutcomeLog.load(cr.profile.client_id, data_dir)
    o = log.record(finding, decision, by=by, reason=reason)  # type: ignore[arg-type]
    log.save()
    pol = log.policy()
    typer.echo(f"Recorded {decision} for {finding_id} (pattern {o.pattern}); "
               f"dismissals so far {pol.dismissal_counts.get(o.pattern, 0)}; "
               f"{'now suppressed' if pol.is_suppressed(finding) else 'now a known pattern' if pol.is_known(finding) else 'still raised'}.")
