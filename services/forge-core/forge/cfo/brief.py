"""The owner brief: prioritised decisions, not a dashboard.

The blueprint's tenth workflow. Every line is derived from a number the engine
proved, ordered by what changes the owner's next action: cash first, then money
to collect, then what is critical, then deadlines, then margin.
"""

from __future__ import annotations

__all__ = ["owner_brief"]


def owner_brief(client_run) -> str:
    cr = client_run
    run, fc, hs, close = cr.run, cr.forecast, cr.health, cr.close
    lines = [f"{cr.profile.business_name}: the five things that matter, {run.period_end.strftime('%d %B %Y')}", ""]
    n = 0

    def item(title: str, detail: str) -> None:
        nonlocal n
        n += 1
        lines.append(f"{n}. {title}")
        lines.append(f"   {detail}")
        lines.append("")

    if fc.goes_negative and fc.lowest_point:
        wk, low = fc.lowest_point
        item(f"Cash goes negative in week {wk} at {low.format()}.",
             f"Opening cash is {fc.opening_cash.format()}. {run.ctx.ar.past_due_total().format()} of overdue receivables is the fastest source of cover.")
    else:
        low = fc.lowest_point
        item(f"Cash stays positive for thirteen weeks; the low point is {low[1].format()} in week {low[0]}." if low else "Cash forecast unavailable.",
             f"Opening cash {fc.opening_cash.format()}.")

    overdue = run.ctx.ar.past_due(45)
    if overdue:
        top = max(overdue, key=lambda inv: inv.outstanding.minor_units)
        party = run.ctx.ledger.parties.get(top.party_id)
        item(f"Collect {run.ctx.ar.past_due_total(45).format()} that is more than 45 days late.",
             f"Largest: {party.name if party else top.party_id}, {top.outstanding.format()}, {top.days_past_due} days. A collection note is drafted for each.")

    crit = [f for f in run.findings if f.severity.value == "critical"]
    if crit:
        item(f"{len(crit)} critical item{'s' if len(crit) > 1 else ''} need{'s' if len(crit) == 1 else ''} a decision now.",
             "; ".join(f.title for f in crit[:3]))

    soon = [d for d in cr.deadlines if 0 <= d.days_until(run.period_end) <= 30]
    if soon:
        item(f"{len(soon)} filing deadline{'s' if len(soon) > 1 else ''} in the next thirty days.",
             "; ".join(f"{d.due_on.strftime('%b %d')}: {d.description}" for d in soon[:3]))

    bench = [f for f in run.findings if f.category == "benchmark"]
    if bench:
        item(bench[0].title + ".", bench[0].remediation)
    elif run.ctx.pl_ttm.gross_margin is not None:
        item(f"Gross margin is {run.ctx.pl_ttm.gross_margin * 100:.1f}% on {run.ctx.pl_ttm.revenue.total.format()} of trailing revenue.",
             "Within the expected range for the industry and size.")

    lines.append(f"Health {hs.score}/100 ({hs.grade}). Close {close.percent_complete}% complete, {len(close.open)} items open. "
                 f"Books {'tie out' if run.passed_data_gate else 'DO NOT tie out'}.")
    return "\n".join(lines)
