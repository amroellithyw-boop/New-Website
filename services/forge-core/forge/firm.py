"""The firm's own settings: who is sending, and what things cost.

Kept in a JSON file outside the code so pricing and identity are yours to
change without a deploy. ``firm.json`` in the working directory, or the path
in ``FORGE_FIRM``, is read; when neither exists the defaults in the code apply
and ``forge firm`` says so.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

__all__ = ["FirmConfig", "load_firm"]


@dataclass
class FirmConfig:
    name: str = "Profit Forge"
    sender: str = "Amro"
    email: str = ""
    phone: str = ""
    website: str = ""
    province: str = "ON"
    # Pricing overrides. Any key left out keeps the default in forge/growth/pricing.py.
    base_by_tier: dict[str, str] = field(default_factory=dict)
    service_fees: dict[str, str] = field(default_factory=dict)
    diagnostic_by_tier: dict[str, str] = field(default_factory=dict)
    payroll_per_employee: str | None = None
    volume_per_100_txns: str | None = None
    cleanup_per_month_behind: str | None = None
    source: str = ""
    """Where this configuration came from; empty means code defaults."""

    @classmethod
    def load(cls, path: Path | None = None) -> FirmConfig:
        candidate = path or Path(os.environ.get("FORGE_FIRM", "firm.json"))
        if not candidate.exists():
            return cls()
        raw = json.loads(candidate.read_text())
        known = {k: v for k, v in raw.items() if k in cls.__dataclass_fields__ and k != "source"}
        return cls(**known, source=str(candidate))

    def to_dict(self) -> dict:
        return asdict(self)


def load_firm(path: Path | None = None) -> FirmConfig:
    return FirmConfig.load(path)
