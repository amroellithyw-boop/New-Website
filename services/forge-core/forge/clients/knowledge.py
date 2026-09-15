"""Per-client knowledge and precedents.

The blueprint's memory rule: never rely on a chat transcript as the memory of
the business. Facts, approved treatments and open questions live in explicit
stores. This module is the simplest honest implementation, a JSON file per
client with an interface a database can replace, and it feeds two things the
rest of the system already reads: the evidence packet's context, and the risk
router's ``known_pattern`` flag that lets a precedent lower review depth.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

__all__ = ["Fact", "Precedent", "OpenQuestion", "ClientKnowledge"]


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class Fact:
    key: str
    value: str
    source: str  # intake | operator | document | model
    recorded_at: str = field(default_factory=_now)


@dataclass
class Precedent:
    """An approved treatment. Matching future items are known patterns."""

    pattern: str  # e.g. "vendor:northstone aggregate" or "rule:FOS-R024:6700"
    treatment: str
    approved_by: str
    approved_at: str = field(default_factory=_now)
    note: str = ""


@dataclass
class OpenQuestion:
    question_id: str
    question: str
    raised_by: str
    raised_at: str = field(default_factory=_now)
    answer: str | None = None
    answered_at: str | None = None


@dataclass
class ClientKnowledge:
    client_id: str
    facts: dict[str, Fact] = field(default_factory=dict)
    precedents: dict[str, Precedent] = field(default_factory=dict)
    questions: dict[str, OpenQuestion] = field(default_factory=dict)
    path: Path | None = None

    # ---- persistence ------------------------------------------------------

    @classmethod
    def load(cls, client_id: str, directory: Path) -> ClientKnowledge:
        path = Path(directory) / f"{client_id}.knowledge.json"
        if not path.exists():
            return cls(client_id=client_id, path=path)
        raw = json.loads(path.read_text())
        return cls(
            client_id=client_id, path=path,
            facts={k: Fact(**v) for k, v in raw.get("facts", {}).items()},
            precedents={k: Precedent(**v) for k, v in raw.get("precedents", {}).items()},
            questions={k: OpenQuestion(**v) for k, v in raw.get("questions", {}).items()},
        )

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({
            "facts": {k: asdict(v) for k, v in self.facts.items()},
            "precedents": {k: asdict(v) for k, v in self.precedents.items()},
            "questions": {k: asdict(v) for k, v in self.questions.items()},
        }, indent=2, sort_keys=True))

    # ---- facts ----------------------------------------------------------------

    def remember(self, key: str, value: str, *, source: str) -> None:
        self.facts[key] = Fact(key=key, value=value, source=source)

    def context_lines(self, limit: int = 40) -> list[str]:
        """Facts as data lines for an evidence packet. Never instructions."""
        return [f"{f.key}: {f.value}" for f in list(self.facts.values())[:limit]]

    # ---- precedents ----------------------------------------------------------

    def approve(self, pattern: str, treatment: str, *, by: str, note: str = "") -> None:
        self.precedents[pattern] = Precedent(pattern=pattern, treatment=treatment, approved_by=by, note=note)

    def is_known(self, *patterns: str) -> bool:
        return any(p in self.precedents for p in patterns)

    def precedent_for(self, *patterns: str) -> Precedent | None:
        for p in patterns:
            if p in self.precedents:
                return self.precedents[p]
        return None

    # ---- questions ------------------------------------------------------------

    def ask(self, question_id: str, question: str, *, by: str) -> OpenQuestion:
        q = self.questions.get(question_id) or OpenQuestion(question_id=question_id, question=question, raised_by=by)
        self.questions[question_id] = q
        return q

    def answer(self, question_id: str, answer: str) -> None:
        q = self.questions[question_id]
        q.answer, q.answered_at = answer, _now()

    def unanswered(self) -> Iterable[OpenQuestion]:
        return (q for q in self.questions.values() if q.answer is None)

    def to_dict(self) -> dict[str, Any]:
        return {"client_id": self.client_id, "facts": len(self.facts), "precedents": len(self.precedents),
                "open_questions": sum(1 for _ in self.unanswered())}
