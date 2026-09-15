"""One call that runs a client end to end and returns everything.

This is the unit of automation. A scheduler calls it per client per day; the
CLI calls it for a demonstration. It produces the review, the health score,
the working capital, the cash forecast, the close checklist, the tax return
working paper, the compliance deadlines, the draft actions, and the proposal
figures, all from one ledger pass, and it records what it cost.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from ..actions import DraftAction, draft_for
from ..agents.gateway import ModelGateway
from ..canonical.models import Ledger
from ..cfo import (
    CashForecast,
    HealthScore,
    WorkingCapital,
    cash_forecast,
    health_score,
    working_capital,
)
from ..clients import ClientKnowledge, ClientProfile
from ..close import CloseChecklist, build_close_checklist
from ..engine.reconcile import BankStatement
from ..learning import OutcomeLog
from ..pipeline import ControllerRun, run_continuous_controller
from ..tax import (
    Deadline,
    SalesTaxReturn,
    TaxProvision,
    corporate_tax_provision,
    sales_tax_return,
    tax_calendar,
)

__all__ = ["ClientRun", "run_client"]


@dataclass
class ClientRun:
    profile: ClientProfile
    run: ControllerRun
    health: HealthScore
    working_capital: WorkingCapital
    forecast: CashForecast
    close: CloseChecklist
    sales_tax: SalesTaxReturn
    provision: TaxProvision
    deadlines: list[Deadline]
    drafts: list[tuple[str, DraftAction]] = field(default_factory=list)
    cost_micros: int = 0

    def summary(self) -> dict[str, Any]:
        r = self.run.summary()
        return {
            "client": self.profile.business_name, "period": r["period"], "passed_data_gate": r["passed_data_gate"],
            "findings": r["findings"], "by_severity": r["by_severity"], "recoverable_cash": r["recoverable_cash"],
            "health": {"score": self.health.score, "grade": self.health.grade},
            "cash": {"opening": str(self.forecast.opening_cash.to_decimal()), "goes_negative": self.forecast.goes_negative,
                     "lowest_week": self.forecast.lowest_point[0] if self.forecast.lowest_point else None},
            "close": {"percent_complete": self.close.percent_complete, "open": len(self.close.open), "can_lock": self.close.can_lock},
            "sales_tax_balance": str(self.sales_tax.line_113_balance.to_decimal()),
            "tax_provision": str(self.provision.total_tax.to_decimal()),
            "deadlines_30_days": [(d.kind, d.due_on.isoformat()) for d in self.deadlines if 0 <= d.days_until(self.run.period_end) <= 30],
            "drafts": {"total": len(self.drafts), "authorised_now": sum(1 for _, d in self.drafts if d.authorised_now)},
            "suppressed_by_learning": r.get("suppressed_by_learning", 0),
            "cost_micros": self.cost_micros,
        }


def run_client(
    profile: ClientProfile, ledger: Ledger, *, period_start: date, period_end: date,
    statements: Sequence[BankStatement] = (), gateway: ModelGateway | None = None, review: bool = False,
    data_dir: Path | None = None,
) -> ClientRun:
    knowledge = ClientKnowledge.load(profile.client_id, data_dir) if data_dir else None
    learning = OutcomeLog.load(profile.client_id, data_dir).policy() if data_dir else None
    run = run_continuous_controller(
        ledger, period_start=period_start, period_end=period_end, statements=statements, profile=profile,
        knowledge=knowledge, learning=learning, gateway=gateway, review=review,
    )
    ctx = run.ctx
    drafts = [(item.work_item_id, draft_for(item, ledger)) for item in run.work_items]
    return ClientRun(
        profile=profile, run=run,
        health=health_score(ctx, findings=run.findings),
        working_capital=working_capital(ctx),
        forecast=cash_forecast(ctx),
        close=build_close_checklist(ctx, run.findings, profile),
        sales_tax=sales_tax_return(ctx),
        provision=corporate_tax_provision(ctx),
        deadlines=tax_calendar(profile, year=period_end.year, today=period_end - timedelta(days=60)),
        drafts=drafts,
        cost_micros=gateway.total_cost_micros if gateway else 0,
    )
