"""Connector contract.

A connector's only job is to hand back raw payloads and canonical records with
lineage attached. It must not interpret, judge, or calculate. Everything a
connector returns is treated as untrusted input until the normalizer has mapped
it and the deterministic engine has tied it out.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterator, Mapping

__all__ = ["RawRecord", "SyncCursor", "SyncResult", "Connector"]


@dataclass(frozen=True)
class RawRecord:
    """One untouched payload from a source system, ready for the vault."""

    source_system: str
    resource: str
    source_id: str
    payload: Mapping[str, Any]
    fetched_at: datetime
    source_updated_at: datetime | None = None

    @property
    def key(self) -> str:
        return f"{self.source_system}/{self.resource}/{self.source_id}"


@dataclass(frozen=True)
class SyncCursor:
    """Incremental sync position. Persisted so a resync never double-counts."""

    resource: str
    last_updated_at: datetime | None = None
    last_source_id: str | None = None


@dataclass
class SyncResult:
    connector: str
    records: list[RawRecord] = field(default_factory=list)
    cursors: dict[str, SyncCursor] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def by_resource(self, resource: str) -> list[RawRecord]:
        return [r for r in self.records if r.resource == resource]


class Connector(ABC):
    """Read-only source adapter.

    Write capability is a *separate* interface added only at autonomy level A2,
    with its own credentials. Keeping read and write apart at the type level is
    what makes "read-only in the alpha" an architectural fact rather than a
    promise in a document.
    """

    source_system: str = "unknown"

    @abstractmethod
    def resources(self) -> tuple[str, ...]:
        """Resource names this connector can fetch."""

    @abstractmethod
    def fetch(
        self, resource: str, *, since: SyncCursor | None = None
    ) -> Iterator[RawRecord]:
        """Yield raw records for one resource, newest-updated last."""

    def sync(self, *, cursors: Mapping[str, SyncCursor] | None = None) -> SyncResult:
        result = SyncResult(connector=self.source_system)
        existing = dict(cursors or {})
        for resource in self.resources():
            cursor = existing.get(resource)
            try:
                latest = cursor
                for record in self.fetch(resource, since=cursor):
                    result.records.append(record)
                    if record.source_updated_at is not None and (
                        latest is None
                        or latest.last_updated_at is None
                        or record.source_updated_at >= latest.last_updated_at
                    ):
                        latest = SyncCursor(
                            resource=resource,
                            last_updated_at=record.source_updated_at,
                            last_source_id=record.source_id,
                        )
                if latest is not None:
                    result.cursors[resource] = latest
            except Exception as exc:  # noqa: BLE001 - surfaced, never swallowed
                result.errors.append(f"{resource}: {exc}")
        return result
