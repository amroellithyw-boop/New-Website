"""The sales pipeline: prospects moving toward paid engagements, as a file.

Stages are fixed and each move is timestamped, so conversion and stage age
are queries rather than guesses. This is deliberately not a CRM; it is the
minimum record that lets the operations queue say who to call today.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

__all__ = ["STAGES", "Prospect", "SalesPipeline"]

STAGES: tuple[str, ...] = ("lead", "contacted", "diagnostic_booked", "diagnostic_delivered", "proposal_sent", "won", "lost")


@dataclass
class Prospect:
    prospect_id: str
    business_name: str
    stage: str = "lead"
    naics_code: str | None = None
    province: str = "ON"
    revenue_estimate: str | None = None
    source: str = ""
    next_action: str = "Send outreach"
    next_action_on: str | None = None
    history: list[dict] = field(default_factory=list)
    profile_path: str | None = None

    def move(self, stage: str, *, note: str = "", next_action: str = "", days: int = 3) -> None:
        if stage not in STAGES:
            raise ValueError(f"unknown stage {stage!r}")
        self.history.append({"from": self.stage, "to": stage, "at": datetime.now(UTC).isoformat(), "note": note})
        self.stage = stage
        self.next_action = next_action or {"contacted": "Follow up", "diagnostic_booked": "Collect access and statements",
                                           "diagnostic_delivered": "Send proposal", "proposal_sent": "Call to close",
                                           "won": "Onboard", "lost": ""}.get(stage, "")
        from datetime import timedelta
        self.next_action_on = (date.today() + timedelta(days=days)).isoformat() if self.next_action else None

    @property
    def stage_age_days(self) -> int:
        if not self.history:
            return 0
        last = datetime.fromisoformat(self.history[-1]["at"])
        return (datetime.now(UTC) - last).days


@dataclass
class SalesPipeline:
    prospects: dict[str, Prospect] = field(default_factory=dict)
    path: Path | None = None

    @classmethod
    def load(cls, path: Path) -> SalesPipeline:
        p = Path(path)
        if not p.exists():
            return cls(path=p)
        raw = json.loads(p.read_text())
        return cls(prospects={k: Prospect(**v) for k, v in raw.items()}, path=p)

    def save(self) -> None:
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({k: asdict(v) for k, v in self.prospects.items()}, indent=2))

    def add(self, prospect: Prospect) -> Prospect:
        self.prospects[prospect.prospect_id] = prospect
        return prospect

    def due_today(self, today: date | None = None) -> list[Prospect]:
        t = (today or date.today()).isoformat()
        return sorted((p for p in self.prospects.values() if p.next_action_on and p.next_action_on <= t and p.stage not in ("won", "lost")),
                      key=lambda p: STAGES.index(p.stage), reverse=True)

    def funnel(self) -> dict[str, int]:
        out = dict.fromkeys(STAGES, 0)
        for p in self.prospects.values():
            out[p.stage] += 1
        return out

    def conversion(self) -> dict[str, str]:
        f = self.funnel()
        total = sum(f.values()) or 1
        won = f["won"]
        delivered = f["diagnostic_delivered"] + f["proposal_sent"] + won + f["lost"]
        return {"lead_to_diagnostic": f"{(f['diagnostic_booked'] + delivered) / total:.0%}",
                "diagnostic_to_won": f"{won / delivered:.0%}" if delivered else "n/a",
                "overall": f"{won / total:.0%}"}
