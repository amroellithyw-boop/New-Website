"""An engagement letter, read into structure.

The firm's own letters follow one layout: numbered sections, PHASE headings
with bullet deliverables, fees written as "$1,500 + HST" or "$600/month +
HST", an assumptions section, client responsibilities and exclusions. That
layout is parsed deterministically, which is what makes the result
reviewable line by line. A letter in another layout goes to a model with the
same schema, and the plan says so.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..money import Money

__all__ = ["PhaseSpec", "EngagementLetter", "EngagementLetterExtract", "parse_engagement_letter", "letter_from_extract"]

MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december")
FEE_RE = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)\s*(/\s*(?:month|mo|hour|hr))?", re.IGNORECASE)
PHASE_RE = re.compile(r"^\s*PHASE\s+(\d+)\s*[:.\-]?\s*(.*?)\s*$", re.IGNORECASE)
SECTION_RE = re.compile(r"^\s*(\d{1,2})\.\s+([A-Z][^\n]{2,80})$")
BULLET_RE = re.compile(r"^\s*[•\-\*·]\s+(.*)$")
MONTHLY_WORDS = ("monthly", "ongoing", "each month", "per month", "/month")


@dataclass
class PhaseSpec:
    number: int
    title: str
    deliverables: tuple[str, ...] = ()
    fee: Money | None = None
    cadence: str = "fixed"  # fixed | monthly | hourly | unknown
    authorised: bool = False

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["fee"] = str(self.fee.to_decimal()) if self.fee else None
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PhaseSpec:
        return cls(number=int(d["number"]), title=d["title"], deliverables=tuple(d.get("deliverables", ())),
                   fee=Money.from_decimal(d["fee"]) if d.get("fee") else None, cadence=d.get("cadence", "fixed"),
                   authorised=bool(d.get("authorised", False)))


@dataclass
class EngagementLetter:
    client_name: str = ""
    other_parties: tuple[str, ...] = ()
    subject: str = ""
    """The business, property or project the letter is about."""
    letter_date: date | None = None
    phases: tuple[PhaseSpec, ...] = ()
    assumptions: tuple[str, ...] = ()
    client_responsibilities: tuple[str, ...] = ()
    exclusions: tuple[str, ...] = ()
    hourly_rate: Money | None = None
    parsed_by: str = "template"  # template | model | manual
    checksum: str = ""
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def authorised_phases(self) -> tuple[PhaseSpec, ...]:
        return tuple(p for p in self.phases if p.authorised)

    @property
    def all_text(self) -> str:
        parts = [self.subject, *(p.title for p in self.phases)]
        for p in self.phases:
            parts.extend(p.deliverables)
        parts.extend(self.assumptions)
        return "\n".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "client_name": self.client_name, "other_parties": list(self.other_parties), "subject": self.subject,
            "letter_date": self.letter_date.isoformat() if self.letter_date else None,
            "phases": [p.to_dict() for p in self.phases], "assumptions": list(self.assumptions),
            "client_responsibilities": list(self.client_responsibilities), "exclusions": list(self.exclusions),
            "hourly_rate": str(self.hourly_rate.to_decimal()) if self.hourly_rate else None,
            "parsed_by": self.parsed_by, "checksum": self.checksum, "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> EngagementLetter:
        return cls(
            client_name=d.get("client_name", ""), other_parties=tuple(d.get("other_parties", ())), subject=d.get("subject", ""),
            letter_date=date.fromisoformat(d["letter_date"]) if d.get("letter_date") else None,
            phases=tuple(PhaseSpec.from_dict(p) for p in d.get("phases", ())), assumptions=tuple(d.get("assumptions", ())),
            client_responsibilities=tuple(d.get("client_responsibilities", ())), exclusions=tuple(d.get("exclusions", ())),
            hourly_rate=Money.from_decimal(d["hourly_rate"]) if d.get("hourly_rate") else None,
            parsed_by=d.get("parsed_by", "manual"), checksum=d.get("checksum", ""), warnings=tuple(d.get("warnings", ())),
        )


class PhaseExtract(BaseModel):
    model_config = ConfigDict(extra="forbid")
    number: int
    title: str
    deliverables: list[str] = Field(default_factory=list)
    fee: str | None = Field(default=None, description="Amount as digits, e.g. 1500.00, without tax")
    cadence: str = Field(default="fixed", description="fixed | monthly | hourly")
    authorised_by_signing: bool = False


class EngagementLetterExtract(BaseModel):
    """What a model returns for a letter outside the firm's template."""

    model_config = ConfigDict(extra="forbid")
    client_name: str = ""
    other_parties: list[str] = Field(default_factory=list)
    subject: str = ""
    letter_date: str | None = None
    phases: list[PhaseExtract] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    client_responsibilities: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)
    hourly_rate: str | None = None
    confidence: float = 0.0


def _parse_date(text: str) -> date | None:
    m = re.search(r"\b(" + "|".join(MONTHS) + r")\s+(\d{1,2}),\s+(\d{4})\b", text, re.IGNORECASE)
    if not m:
        return None
    return date(int(m.group(3)), MONTHS.index(m.group(1).lower()) + 1, int(m.group(2)))


def _money(s: str) -> Money:
    return Money.from_decimal(s.replace(",", ""))


def _labelled(text: str, label: str) -> str:
    m = re.search(rf"^\s*{label}\s+(.+?)\s*$", text, re.IGNORECASE | re.MULTILINE)
    return m.group(1).strip() if m else ""


def parse_engagement_letter(text: str) -> EngagementLetter:
    """Parse the firm's template. Returns whatever it can with warnings for the rest."""
    lines = [ln.rstrip() for ln in text.splitlines()]
    warnings: list[str] = []
    checksum = hashlib.sha256(text.encode()).hexdigest()[:16]

    client = _labelled(text, "Client") or ""
    client = re.split(r"\s+\(the\b", client)[0].strip()
    others = tuple(v for v in (_labelled(text, "Co-owner"), _labelled(text, "Co-applicant"), _labelled(text, "Spouse")) if v)
    subject = _labelled(text, "Property") or _labelled(text, "Business") or _labelled(text, "Project") or ""
    letter_date = _parse_date(text)

    # Sections: "2. Services and fees", "4. Current project assumptions", ...
    section_starts: list[tuple[int, str]] = []
    for i, ln in enumerate(lines):
        m = SECTION_RE.match(ln)
        if m:
            section_starts.append((i, m.group(2).strip().lower()))

    def section_lines(*needles: str) -> list[str]:
        for idx, (start, title) in enumerate(section_starts):
            if any(n in title for n in needles):
                end = section_starts[idx + 1][0] if idx + 1 < len(section_starts) else len(lines)
                return lines[start + 1:end]
        return []

    def bullets(block: list[str]) -> tuple[str, ...]:
        out: list[str] = []
        for ln in block:
            m = BULLET_RE.match(ln)
            if m:
                out.append(m.group(1).strip())
            elif out and ln.strip() and not PHASE_RE.match(ln) and not FEE_RE.fullmatch(ln.strip().replace("+ HST", "").strip()):
                out[-1] = out[-1] + " " + ln.strip()
        return tuple(s for s in out if s)

    # Phases and their deliverables.
    phases: list[PhaseSpec] = []
    fee_tokens: list[tuple[Money, str]] = []
    services = section_lines("services", "scope", "fees")
    i = 0
    while i < len(services):
        m = PHASE_RE.match(services[i])
        if m:
            number, title = int(m.group(1)), m.group(2).strip()
            j = i + 1
            block: list[str] = []
            while j < len(services) and not PHASE_RE.match(services[j]):
                block.append(services[j])
                j += 1
            phases.append(PhaseSpec(number=number, title=title, deliverables=bullets(block)))
            i = j
        else:
            i += 1
    for ln in services:
        stripped = ln.strip()
        for m in FEE_RE.finditer(stripped):
            if "hour" in (m.group(2) or "").lower() or "/hr" in (m.group(2) or "").lower():
                continue
            cadence = "monthly" if m.group(2) else "fixed"
            # A bare "$600/month" on one line followed by "+ HST" on the next is still one fee.
            fee_tokens.append((_money(m.group(1)), cadence))
    monthly_fees = [f for f, c in fee_tokens if c == "monthly"]
    fixed_fees = [f for f, c in fee_tokens if c == "fixed"]
    for p in phases:
        text_p = (p.title + " " + " ".join(p.deliverables)).lower()
        p.cadence = "monthly" if any(w in text_p for w in MONTHLY_WORDS) else "fixed"
    for p in phases:
        pool = monthly_fees if p.cadence == "monthly" else fixed_fees
        if pool:
            p.fee = pool.pop(0)
        else:
            warnings.append(f"Phase {p.number}: no {p.cadence} fee found in the letter")
    if monthly_fees or fixed_fees:
        warnings.append("Some fee amounts in the letter were not matched to a phase")

    # Authorisation: "authorizes Phase 1 only", "authorizes Phases 1 and 2".
    auth = re.search(r"authori[sz]es?\s+phases?\s+([\d,\sand]+)", text, re.IGNORECASE)
    if auth:
        nums = {int(n) for n in re.findall(r"\d+", auth.group(1))}
        for p in phases:
            p.authorised = p.number in nums
    elif phases:
        phases[0].authorised = True
        warnings.append("No phase authorisation sentence found; phase 1 assumed authorised")

    hourly = None
    hm = re.search(r"\$\s?(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)\s*/\s*(?:hour|hr)", text, re.IGNORECASE)
    if hm:
        hourly = _money(hm.group(1))

    assumptions = bullets(section_lines("assumption"))
    responsibilities = bullets(section_lines("client responsibilit"))
    exclusions = bullets(section_lines("limitation", "excluded", "exclusion"))
    if not phases:
        warnings.append("No PHASE headings found; the letter is not in the template layout")
    if not client:
        warnings.append("Client name not found")
    return EngagementLetter(client_name=client, other_parties=others, subject=subject, letter_date=letter_date, phases=tuple(phases),
                            assumptions=assumptions, client_responsibilities=responsibilities, exclusions=exclusions,
                            hourly_rate=hourly, parsed_by="template", checksum=checksum, warnings=tuple(warnings))


def letter_from_extract(x: EngagementLetterExtract, *, checksum: str) -> EngagementLetter:
    phases = tuple(PhaseSpec(number=p.number, title=p.title, deliverables=tuple(p.deliverables),
                             fee=Money.from_decimal(p.fee) if p.fee else None, cadence=p.cadence or "fixed",
                             authorised=p.authorised_by_signing) for p in x.phases)
    return EngagementLetter(client_name=x.client_name, other_parties=tuple(x.other_parties), subject=x.subject,
                            letter_date=date.fromisoformat(x.letter_date) if x.letter_date else None, phases=phases,
                            assumptions=tuple(x.assumptions), client_responsibilities=tuple(x.client_responsibilities),
                            exclusions=tuple(x.exclusions), hourly_rate=Money.from_decimal(x.hourly_rate) if x.hourly_rate else None,
                            parsed_by="model", checksum=checksum,
                            warnings=("Parsed by a model; check every phase, fee and deliverable against the letter",) if x.confidence < 0.9 else ())
