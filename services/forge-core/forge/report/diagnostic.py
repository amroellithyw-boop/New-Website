"""The client-facing control review.

This is the artefact a client actually pays for, so it follows two rules that
most generated finance output breaks:

* **Every number is traceable.** Each finding carries its calculations and its
  source records, so the reader can check the work rather than trust it.
* **Nothing is padded.** Items below the trivial threshold never appear. A short
  report that names six real problems is worth more than a long one that buries
  them in observations.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..canonical.enums import Severity
from ..money import msum
from ..pipeline import ControllerRun
from ..rules import REGISTRY
from ..workitems.risk import RiskScore

__all__ = ["render_diagnostic", "write_diagnostic", "summary_sentence"]

TEMPLATE_DIR = Path(__file__).parent / "templates"
CONTROL_VERSION = "1.0"


def summary_sentence(run: ControllerRun) -> str:
    """The one paragraph an owner will actually read.

    Leads with whether the books can be trusted, then with money, because those
    are the two things that change what the reader does next.
    """
    findings = run.findings
    criticals = [f for f in findings if f.severity is Severity.CRITICAL]
    cash = run.recoverable_cash

    if not run.passed_data_gate:
        opening = (
            "The accounting records do not currently balance, so the reported figures "
            "cannot be relied on until that is corrected. "
        )
    elif not findings:
        return (
            "The books tie out and no exceptions above the reporting threshold were found "
            f"for this period across {run.outcome.rules_run} controls."
        )
    else:
        opening = "The books tie out. "

    parts = [opening]
    if criticals:
        parts.append(
            f"{len(criticals)} critical issue{'' if len(criticals) == 1 else 's'} "
            f"{'needs' if len(criticals) == 1 else 'need'} attention now, "
        )
        parts.append(
            f"and {len(findings) - len(criticals)} further finding"
            f"{'' if len(findings) - len(criticals) == 1 else 's'} "
            f"{'was' if len(findings) - len(criticals) == 1 else 'were'} raised. "
        )
    else:
        parts.append(
            f"{len(findings)} finding{'' if len(findings) == 1 else 's'} "
            f"{'was' if len(findings) == 1 else 'were'} raised, none of them critical. "
        )
    if cash.minor_units > 0:
        parts.append(
            f"{cash.format()} is money that has been overpaid, left unbilled, or is overdue "
            "from customers, and is recoverable with the specific steps set out below."
        )
    return "".join(parts)


def _category_stats(run: ControllerRun) -> list[tuple[str, dict[str, int]]]:
    by_cat = REGISTRY.by_category()
    finding_counts: dict[str, int] = {}
    for f in run.findings:
        finding_counts[f.category] = finding_counts.get(f.category, 0) + 1
    rows = []
    for cat in sorted(by_cat):
        rows.append(
            (
                cat,
                {"controls": len(by_cat[cat]), "findings": finding_counts.get(cat, 0)},
            )
        )
    return rows


def render_diagnostic(run: ControllerRun, *, generated_on: date | None = None) -> str:
    """Render the review to a standalone HTML document."""
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template("diagnostic.html.j2")

    ctx = run.ctx
    by_severity: dict[str, int] = {}
    for f in run.findings:
        by_severity[f.severity.value] = by_severity.get(f.severity.value, 0) + 1

    tiers: dict[str, RiskScore] = {i.finding.finding_id: i.risk for i in run.work_items}
    cash = msum(
        (
            r.presentation_balance
            for r in ctx.tb.rows
            if r.account.subtype.value == "bank"
        ),
        run.currency,
    )

    return template.render(
        run=run,
        findings=run.findings,
        by_severity=by_severity,
        tiers=tiers,
        pl=ctx.pl,
        ttm=ctx.pl_ttm,
        bs=ctx.bs,
        cash=cash,
        categories=_category_stats(run),
        summary_sentence=summary_sentence(run),
        generated_on=generated_on or date.today(),
        control_version=CONTROL_VERSION,
        reviewed_count=len([i for i in run.work_items if i.reviews]),
    )


def write_diagnostic(
    run: ControllerRun, path: str | Path, *, generated_on: date | None = None
) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_diagnostic(run, generated_on=generated_on), encoding="utf-8")
    return out


def write_evidence_bundle(run: ControllerRun, path: str | Path) -> Path:
    """Write the machine-readable evidence bundle beside the report.

    A client can open the HTML; a reviewer, a regulator or the next system needs
    the structured version. Both come from the same run so they cannot disagree.
    """
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "summary": run.summary(),
        "generated_at": datetime.utcnow().isoformat(),
        "control_version": CONTROL_VERSION,
        "work_items": [i.to_dict() for i in run.work_items],
    }
    out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return out
