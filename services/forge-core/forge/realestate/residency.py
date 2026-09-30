"""Departure, filing history and the non-resident rental calendar.

The review lists what appears required from the facts as stated, what each
item costs if late, and what has to be confirmed. It is a filing review on
the facts provided, not a residency determination.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from ..money import Money
from ..tax.calendar import Deadline
from .facts import EngagementFacts

__all__ = ["ReviewItem", "t1161_penalty", "departure_review", "non_resident_rental_calendar"]

T1161_THRESHOLD = "25000.00"
T1161_PER_DAY = "25.00"
T1161_MIN = "100.00"
T1161_MAX = "2500.00"
SOURCES = (
    "ITA s.128.1(4) deemed disposition on emigration; s.128.1(4)(b)(i) excludes Canadian real property",
    "Form T1161 list of properties by an emigrant; penalty s.162(7): $25 a day, $100 minimum, $2,500 maximum",
    "ITA s.216 election; Form NR6; NR4 information return; guide T4144",
    "Part XIII tax at 25% on gross rents paid to a non-resident; remittance by the 15th of the following month",
    "ITA s.116 dispositions of taxable Canadian property by non-residents",
    "Canada-Italy income tax convention, residence tie-breaker",
)


@dataclass(frozen=True)
class ReviewItem:
    kind: str
    title: str
    detail: str
    action: str
    deadline: date | None = None
    exposure: Money | None = None
    confirm: bool = False


def t1161_penalty(due: date, today: date) -> Money:
    days = max((today - due).days, 0)
    if days == 0:
        return Money.zero()
    raw = Money.from_decimal(T1161_PER_DAY).scale(days)
    return max(min(raw, Money.from_decimal(T1161_MAX)), Money.from_decimal(T1161_MIN))


def departure_review(facts: EngagementFacts, *, today: date) -> list[ReviewItem]:
    items: list[ReviewItem] = []
    cur = facts.currency
    if facts.departure_date and facts.last_t1_year and not facts.departure_reported_on_t1:
        due = date(facts.departure_date.year + 1, 4, 30)
        value = facts.property_value_at_departure
        needs_t1161 = value is None or value > Money.from_decimal(T1161_THRESHOLD, cur)
        items.append(ReviewItem(
            "departure", f"Departure on {facts.departure_date} was not reported on the {facts.last_t1_year} return",
            "A T1 for the year of emigration shows the departure date; without it the CRA treats the taxpayer as resident and expects world income for every later year.",
            f"File a T1 adjustment for {facts.last_t1_year} adding the departure date and residency status; attach the departure documents.",
            deadline=today, confirm=False))
        if needs_t1161:
            items.append(ReviewItem(
                "departure", "Form T1161 (list of properties on emigration) appears required and late",
                f"Property held at departure exceeded {Money.from_decimal(T1161_THRESHOLD, cur).format()}. The late-filing penalty is $25 a day to a $2,500 maximum, and the maximum is reached after 100 days.",
                f"File T1161 with the adjustment; expect the maximum penalty of {t1161_penalty(due, today).format()} and request relief on the grounds of unreported departure if the facts support it.",
                deadline=today, exposure=t1161_penalty(due, today), confirm=True))
        items.append(ReviewItem(
            "departure", "Deemed disposition on departure does not apply to Canadian real property",
            "Real property in Canada is excluded from the emigration deemed disposition; other property such as non-registered investments would be included and reported on T1243.",
            "Confirm what else was owned at departure; if only the home and registered plans, no departure tax arises.",
            confirm=True))
    if facts.departure_date and not facts.later_t1_returns_filed:
        items.append(ReviewItem(
            "filing", f"No Canadian returns filed for {facts.departure_date.year + 1} onward",
            "A non-resident files only for Canadian-source employment or business income, or a disposition of taxable Canadian property. A vacant former home produces neither.",
            "Confirm no other Canadian income in those years; if none, no returns are outstanding for them.",
            confirm=True))
    if not facts.residency_determination_done:
        items.append(ReviewItem(
            "residency", "Residency status rests on facts, not on a determination",
            f"Living in {facts.resident_country} since {facts.departure_date} with no dwelling in Canada (the house is demolished) points to non-residence, and the treaty tie-breaker would favour {facts.resident_country}. The unreported departure is what creates the uncertainty.",
            "Document the ties: home, family, memberships, bank accounts, driver's licence, health card. Request a determination (NR73) only if the file stays uncertain after the adjustment, since a request invites a review.",
            confirm=True))
    if facts.change_of_use_date:
        yr = facts.change_of_use_date.year
        items.append(ReviewItem(
            "filing", f"A non-resident T1 is required for {yr}: the change of use is a deemed disposition of taxable Canadian property",
            "Turning the former home into a rental development deems a sale at fair market value on the change-of-use date. The principal residence years reduce the gain; the balance is a capital gain in that year.",
            f"Obtain a land appraisal as at {facts.change_of_use_date}; file the {yr} T1 with T2091(IND) by 30 April {yr + 1}; each co-owner reports half.",
            deadline=date(yr + 1, 4, 30)))
    if facts.gst_returns_outstanding:
        items.append(ReviewItem(
            "gst", f"GST/HST return(s) outstanding: {', '.join(facts.gst_returns_outstanding)}",
            "An open account with an unfiled period blocks a clean claim for construction credits and can trigger arbitrary assessments.",
            "File the outstanding return (nil if the records support it) before the first credit claim.",
            deadline=today))
    if facts.expected_completion:
        first_rent = facts.expected_completion
        items.append(ReviewItem(
            "rental", "Before the first rent: NR6 and a withholding agent",
            "Without an NR6 the agent must withhold 25% of gross rent. With an approved NR6, withholding is 25% of net rent and a s.216 return is due by 30 June of the following year.",
            f"File NR6 before {first_rent}; appoint the property manager as agent; set up monthly Part XIII remittances by the 15th.",
            deadline=first_rent, confirm=True))
    if facts.client_citizenship == "unknown":
        items.append(ReviewItem(
            "ownership", "Citizenship or permanent residence status is not on file",
            "It decides whether the non-resident speculation taxes (25% Ontario, 10% Toronto) apply to any transfer to a corporation or family member, and whether the federal purchase prohibition is relevant.",
            "Confirm Canadian citizenship or permanent residence for both owners with documents.",
            confirm=True))
    return items


def non_resident_rental_calendar(first_rent: date, *, year: int) -> list[Deadline]:
    out: list[Deadline] = []
    out.append(Deadline("nr6", min(first_rent, date(year, 1, 1)), "NR6 undertaking so withholding is on net rent; file before the first rent payment of the year", "CRA", str(year), confirm=True))
    for m in range(1, 13):
        due = date(year + (m == 12), m % 12 + 1, 15)
        if date(year, m, 1) >= date(first_rent.year, first_rent.month, 1):
            out.append(Deadline("part_xiii_remittance", due, f"Part XIII withholding on rent for {date(year, m, 1).strftime('%B %Y')}, remitted by the agent", "CRA", f"{year}-{m:02d}"))
    out.append(Deadline("nr4", date(year + 1, 3, 31), f"NR4 slips and summary for {year}", "CRA", str(year)))
    out.append(Deadline("s216_return", date(year + 1, 6, 30), f"s.216 return (T1159) for {year}, required by 30 June when an NR6 was approved; otherwise within two years", "CRA", str(year)))
    return sorted(out, key=lambda d: d.due_on)
