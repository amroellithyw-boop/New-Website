"""The engagement as it stands: phases, deliverables, documents owed, questions open."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from .letter import EngagementLetter
from .types import ENGAGEMENT_TYPES, detect_types

__all__ = ["DeliverableStatus", "DocumentRequestStatus", "EngagementRecord"]


@dataclass
class DeliverableStatus:
    phase: int
    text: str
    status: str = "not_started"  # not_started | in_progress | delivered | not_authorised
    delivered_on: str | None = None
    note: str = ""


@dataclass
class DocumentRequestStatus:
    request_id: str
    kind: str
    description: str
    why: str
    status: str = "requested"  # requested | received | processed | waived
    document_id: str | None = None
    received_on: str | None = None


@dataclass
class EngagementRecord:
    engagement_id: str
    client_id: str
    letter: EngagementLetter
    types: list[str] = field(default_factory=list)
    deliverables: list[DeliverableStatus] = field(default_factory=list)
    document_requests: list[DocumentRequestStatus] = field(default_factory=list)
    question_ids: list[str] = field(default_factory=list)
    created_on: str = ""
    path: Path | None = None

    @property
    def primary_type(self) -> str | None:
        return self.types[0] if self.types else None

    @property
    def facts_schema(self) -> str | None:
        for t in self.types:
            schema = ENGAGEMENT_TYPES[t].facts_schema
            if schema:
                return schema
        return None

    @property
    def services(self) -> tuple[str, ...]:
        out: list[str] = []
        for t in self.types:
            for s in ENGAGEMENT_TYPES[t].services:
                if s not in out:
                    out.append(s)
        return tuple(out)

    @classmethod
    def from_letter(cls, letter: EngagementLetter, *, client_id: str, engagement_id: str, today: date,
                    types: list[str] | None = None) -> EngagementRecord:
        detected = types or [k for k, _ in detect_types(letter)]
        rec = cls(engagement_id=engagement_id, client_id=client_id, letter=letter, types=detected, created_on=today.isoformat())
        for p in letter.phases:
            for d in p.deliverables:
                rec.deliverables.append(DeliverableStatus(p.number, d, "not_started" if p.authorised else "not_authorised"))
        seen: set[str] = set()
        n = 0
        for t in detected:
            for req in ENGAGEMENT_TYPES[t].document_requests:
                if req.kind in seen:
                    continue
                seen.add(req.kind)
                n += 1
                rec.document_requests.append(DocumentRequestStatus(f"REQ-{n:02d}", req.kind, req.description, req.why))
        return rec

    # ---- progress ------------------------------------------------------

    def receive(self, kind: str, document_id: str, *, on: date) -> DocumentRequestStatus | None:
        for r in self.document_requests:
            if r.kind == kind and r.status == "requested":
                r.status, r.document_id, r.received_on = "received", document_id, on.isoformat()
                return r
        return None

    def mark(self, phase: int, deliverable_prefix: str, status: str, *, on: date, note: str = "") -> bool:
        for d in self.deliverables:
            if d.phase == phase and d.text.lower().startswith(deliverable_prefix.lower()):
                d.status, d.note = status, note
                if status == "delivered":
                    d.delivered_on = on.isoformat()
                return True
        return False

    def authorise(self, phase: int) -> None:
        for p in self.letter.phases:
            if p.number == phase:
                p.authorised = True
        for d in self.deliverables:
            if d.phase == phase and d.status == "not_authorised":
                d.status = "not_started"

    def progress(self) -> dict[str, Any]:
        auth = [d for d in self.deliverables if d.status != "not_authorised"]
        done = [d for d in auth if d.status == "delivered"]
        docs_out = [r for r in self.document_requests if r.status == "requested"]
        return {
            "types": self.types, "phases": len(self.letter.phases),
            "authorised_phases": [p.number for p in self.letter.authorised_phases],
            "deliverables_authorised": len(auth), "deliverables_delivered": len(done),
            "documents_requested": len(self.document_requests), "documents_outstanding": len(docs_out),
            "monthly_fees": str(sum((p.fee.to_decimal() for p in self.letter.phases if p.fee and p.cadence == "monthly"), start=0)),
            "fixed_fees": str(sum((p.fee.to_decimal() for p in self.letter.phases if p.fee and p.cadence == "fixed"), start=0)),
        }

    # ---- persistence ---------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {"engagement_id": self.engagement_id, "client_id": self.client_id, "letter": self.letter.to_dict(), "types": self.types,
                "deliverables": [asdict(d) for d in self.deliverables], "document_requests": [asdict(r) for r in self.document_requests],
                "question_ids": self.question_ids, "created_on": self.created_on}

    @classmethod
    def from_dict(cls, d: dict[str, Any], *, path: Path | None = None) -> EngagementRecord:
        return cls(engagement_id=d["engagement_id"], client_id=d["client_id"], letter=EngagementLetter.from_dict(d["letter"]), types=list(d.get("types", ())),
                   deliverables=[DeliverableStatus(**x) for x in d.get("deliverables", ())],
                   document_requests=[DocumentRequestStatus(**x) for x in d.get("document_requests", ())],
                   question_ids=list(d.get("question_ids", ())), created_on=d.get("created_on", ""), path=path)

    def save(self) -> None:
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.to_dict(), indent=2))

    @classmethod
    def load(cls, path: Path) -> EngagementRecord | None:
        if not Path(path).exists():
            return None
        return cls.from_dict(json.loads(Path(path).read_text()), path=Path(path))
