"""The firm's view: every client, one queue, one brief.

This is the operations machine. It takes each client's run and produces the
ordered list of what the practice should do today: human-approval items first,
then blocked closes, then deadlines inside thirty days, then material findings
by value, then sales follow-ups. It is deterministic and it is the same list
every time for the same inputs.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

from ..growth.pipeline import SalesPipeline
from ..money import Money, msum
from .runner import ClientRun

__all__ = ["QueueItem", "FirmQueue", "build_firm_queue", "daily_brief"]


@dataclass(frozen=True)
class QueueItem:
    priority: int
    client: str
    kind: str
    title: str
    detail: str
    value: Money | None = None
    due_on: date | None = None


@dataclass
class FirmQueue:
    as_of: date
    items: list[QueueItem] = field(default_factory=list)
    clients: int = 0
    total_recoverable: Money = field(default_factory=lambda: Money.zero())
    total_cost_micros: int = 0

    def top(self, n: int = 20) -> list[QueueItem]:
        return self.items[:n]


def build_firm_queue(runs: Sequence[ClientRun], *, as_of: date, pipeline: SalesPipeline | None = None) -> FirmQueue:
    items: list[QueueItem] = []
    for cr in runs:
        name = cr.profile.business_name
        for item in cr.run.items_needing_human:
            items.append(QueueItem(1, name, "approval", item.objective, f"{item.risk.tier.value}: {item.finding.remediation[:120]}", abs(item.finding.exposure)))
        if not cr.run.passed_data_gate:
            items.append(QueueItem(1, name, "data_gate", "Books do not tie out", "; ".join(g.check for g in cr.run.gate_failures)))
        for task in cr.close.tasks:
            if task.status == "blocked":
                items.append(QueueItem(2, name, "close_blocked", task.title, task.evidence))
        for d in cr.deadlines:
            days = d.days_until(as_of)
            if -7 <= days <= 30:
                items.append(QueueItem(2 if days <= 7 else 3, name, "deadline", d.description, f"{d.authority}, due {d.due_on.isoformat()}" + (" (confirm frequency)" if d.confirm else ""), due_on=d.due_on))
        if cr.forecast.goes_negative and cr.forecast.lowest_point:
            wk, cash = cr.forecast.lowest_point
            items.append(QueueItem(2, name, "cash", f"Cash forecast goes negative in week {wk}", f"Low point {cash.format()}; work collections and stage payables", abs(cash)))
        for f in cr.run.findings:
            if f.severity.value in ("critical", "high") and not any(i.title == f.title for i in items):
                items.append(QueueItem(3 if f.severity.value == "high" else 2, name, "finding", f.title, f.remediation[:120], abs(f.exposure)))
    if pipeline:
        for p in pipeline.due_today(as_of):
            items.append(QueueItem(4, p.business_name, "sales", p.next_action, f"Stage {p.stage}, {p.stage_age_days} days"))
    items.sort(key=lambda i: (i.priority, -(i.value.minor_units if i.value else 0), i.due_on or date.max))
    return FirmQueue(as_of=as_of, items=items, clients=len(runs),
                     total_recoverable=msum((cr.run.recoverable_cash for cr in runs), "CAD"),
                     total_cost_micros=sum(cr.cost_micros for cr in runs))


def daily_brief(queue: FirmQueue, runs: Sequence[ClientRun]) -> str:
    lines = [f"Daily brief, {queue.as_of.isoformat()}: {queue.clients} clients, "
             f"{len(queue.items)} items, {queue.total_recoverable.format()} recoverable across the book."]
    approvals = [i for i in queue.items if i.kind == "approval"]
    if approvals:
        lines.append(f"\nNeeds your authorisation ({len(approvals)}):")
        lines += [f"  {i.client}: {i.title} ({i.value.format() if i.value else ''})" for i in approvals[:8]]
    blocked = [i for i in queue.items if i.kind in ("close_blocked", "data_gate")]
    if blocked:
        lines.append(f"\nCloses blocked ({len(blocked)}):")
        lines += [f"  {i.client}: {i.title}; {i.detail}" for i in blocked[:8]]
    due = [i for i in queue.items if i.kind == "deadline"]
    if due:
        lines.append(f"\nDeadlines in thirty days ({len(due)}):")
        lines += [f"  {i.due_on}: {i.client}, {i.title}" for i in due[:10]]
    cash = [i for i in queue.items if i.kind == "cash"]
    if cash:
        lines.append("\nCash warnings:")
        lines += [f"  {i.client}: {i.title}; {i.detail}" for i in cash]
    sales = [i for i in queue.items if i.kind == "sales"]
    if sales:
        lines.append(f"\nSales follow-ups ({len(sales)}):")
        lines += [f"  {i.client}: {i.title} ({i.detail})" for i in sales[:8]]
    lines.append("\nClient health:")
    for cr in sorted(runs, key=lambda c: c.health.score):
        lines.append(f"  {cr.health.score:>3} {cr.health.grade}  {cr.profile.business_name}: {cr.run.summary()['findings']} findings, close {cr.close.percent_complete}%")
    return "\n".join(lines)
