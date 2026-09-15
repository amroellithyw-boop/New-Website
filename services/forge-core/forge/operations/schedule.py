"""What runs when, for each client, from the services they buy.

The job plan is the automation contract: a scheduler executes it, and every
job is a deterministic function that already exists. Cadence follows the work,
not the calendar: a client with payroll gets a payroll check on their pay
cycle; one with sales tax gets a return prepared at their filing frequency.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from ..clients.profile import ClientProfile
from ..tax.calendar import hst_filing_frequency

__all__ = ["ScheduledJob", "job_plan"]


@dataclass(frozen=True)
class ScheduledJob:
    job: str
    cadence: str  # daily | weekly | semi_monthly | monthly | quarterly | annual
    description: str
    next_due: date
    workflow: str


def job_plan(profile: ClientProfile, *, today: date) -> list[ScheduledJob]:
    services = set(profile.services)
    jobs: list[ScheduledJob] = []
    month_end = (today.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    next_monday = today + timedelta(days=(7 - today.weekday()) % 7 or 7)

    jobs.append(ScheduledJob("continuous_controller", "daily", "Run all in-scope controls on the synced ledger; route findings; draft actions", today + timedelta(days=1), "continuous_controller"))
    jobs.append(ScheduledJob("document_intake", "daily", "Process any documents uploaded since the last run", today + timedelta(days=1), "documents"))
    if services & {"cfo_advisory", "cash_forecast", "management_rep"}:
        jobs.append(ScheduledJob("cash_forecast", "weekly", "Thirteen-week cash forecast and working capital scorecard", next_monday, "cash_forecast"))
    if services & {"accounts_rec", "bookkeeping"}:
        jobs.append(ScheduledJob("collections", "weekly", "Refresh aging; draft collection notes for invoices past the threshold", next_monday, "collections"))
    if "payroll" in services:
        jobs.append(ScheduledJob("payroll_check", "semi_monthly", "Tie payroll runs to the ledger; check remittance status", today + timedelta(days=14), "payroll"))
    jobs.append(ScheduledJob("month_end_close", "monthly", "Build the close checklist, post proven adjustments as drafts, prepare the management report", month_end, "close"))
    if profile.hst_registered:
        freq = hst_filing_frequency(profile.annual_revenue_estimate)
        jobs.append(ScheduledJob("sales_tax_return", freq, f"Prepare the {profile.sales_tax.label} return working paper", month_end, "tax_readiness"))
    if "wsib" in services or profile.wsib_number:
        jobs.append(ScheduledJob("wsib_premium", "quarterly", "Compute the WSIB premium on the insurable wage base", month_end, "wsib"))
    jobs.append(ScheduledJob("year_end_package", "annual", "CCA schedule, provision, slips, working papers for the CPA", date(today.year, profile.fiscal_year_end_month, profile.fiscal_year_end_day), "year_end"))
    jobs.append(ScheduledJob("client_questions", "weekly", "Draft the email asking about unclear transactions", next_monday, "communications"))
    jobs.append(ScheduledJob("learning_review", "monthly", "Apply accepted and dismissed outcomes to suppressions and precedents", month_end, "learning"))
    return sorted(jobs, key=lambda j: j.next_due)
