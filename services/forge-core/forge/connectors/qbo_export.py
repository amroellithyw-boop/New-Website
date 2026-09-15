"""Pull a QuickBooks company to one file, optionally anonymised, and rebuild from it.

The file is the "recorded payloads" path: every entity the connector syncs plus
QuickBooks' own trial balance report for the period, in one JSON document. It
lets a real company file be shared, replayed and regression-tested without a
network or a token, and it is what unblocks the remaining entity mappers.

Anonymisation is consistent, not random: each customer, vendor and employee
gets a stable placeholder derived from its position, and that placeholder
replaces the original name everywhere it appears in any string, including
memos and bank descriptions. Emails, phones, addresses, tax numbers and notes
are removed outright. Amounts, dates, accounts and document numbers are left
exactly as they are, because those are what the controls test.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from .base import RawRecord
from .qbo import SYNC_ENTITIES, QboConnector, QboTrialBalance, parse_trial_balance_report

__all__ = ["pull_company", "anonymise", "load_export", "records_from_export", "trial_balance_from_export"]

PARTY_ENTITIES = {"Customer": "Customer", "Vendor": "Vendor", "Employee": "Employee"}
NAME_FIELDS = ("DisplayName", "CompanyName", "FullyQualifiedName", "PrintOnCheckName", "GivenName",
               "MiddleName", "FamilyName", "Title", "Suffix")
STRIP_FIELDS = ("PrimaryEmailAddr", "PrimaryPhone", "AlternatePhone", "Mobile", "Fax", "WebAddr", "BillAddr",
                "ShipAddr", "OtherAddr", "Notes", "TaxIdentifier", "BusinessNumber", "SSN", "SIN", "AcctNum",
                "BillEmail", "BillEmailCc", "BillEmailBcc", "ShipFromAddr", "SalesTermRef", "ContactInfo",
                "PrimaryAddr", "BirthDate")


def pull_company(connector: QboConnector, *, period_start: date, period_end: date,
                 entities: tuple[str, ...] = SYNC_ENTITIES) -> dict[str, Any]:
    """Fetch every entity and the trial balance report into one document."""
    records: dict[str, list[dict[str, Any]]] = {}
    for entity in entities:
        records[entity] = [dict(r.payload) for r in connector.fetch(entity)]
    tb = connector._client.report("TrialBalance", start_date=period_start.isoformat(),
                                  end_date=period_end.isoformat(), accounting_method="Accrual")
    return {
        "format": "forgeos-qbo-export/1",
        "pulled_at": datetime.now(UTC).isoformat(),
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "records": records,
        "trial_balance": tb,
    }


def _walk_replace(node: Any, pattern: re.Pattern[str] | None, mapping: dict[str, str]) -> Any:
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if k in STRIP_FIELDS:
                continue
            out[k] = _walk_replace(v, pattern, mapping)
        return out
    if isinstance(node, list):
        return [_walk_replace(v, pattern, mapping) for v in node]
    if isinstance(node, str) and pattern is not None:
        return pattern.sub(lambda m: mapping[m.group(0)], node)
    return node


def anonymise(export: Mapping[str, Any]) -> dict[str, Any]:
    """Replace every party name consistently and strip contact details."""
    mapping: dict[str, str] = {}
    records = export.get("records", {})
    for entity, label in PARTY_ENTITIES.items():
        for i, row in enumerate(records.get(entity, []), start=1):
            placeholder = f"{label} {i:04d}"
            for field in NAME_FIELDS:
                value = row.get(field)
                if isinstance(value, str) and len(value.strip()) >= 3:
                    mapping.setdefault(value.strip(), placeholder)
    for row in records.get("CompanyInfo", []):
        for field in ("CompanyName", "LegalName"):
            value = row.get(field)
            if isinstance(value, str) and value.strip():
                mapping.setdefault(value.strip(), "Anonymised Company Ltd.")
    pattern = None
    if mapping:
        # Longest names first so "Acme Paving Inc" is replaced before "Acme".
        alternatives = sorted(mapping, key=len, reverse=True)
        pattern = re.compile("|".join(re.escape(a) for a in alternatives))
    out = _walk_replace(dict(export), pattern, mapping)
    out["anonymised"] = True
    out["anonymised_parties"] = len(mapping)
    return out


def load_export(path: Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text())
    if data.get("format") != "forgeos-qbo-export/1":
        raise ValueError(f"{path} is not a ForgeOS QuickBooks export")
    return data


def records_from_export(export: Mapping[str, Any]) -> list[RawRecord]:
    fetched = datetime.fromisoformat(export["pulled_at"])
    out: list[RawRecord] = []
    for resource, rows in export["records"].items():
        for row in rows:
            updated = (row.get("MetaData") or {}).get("LastUpdatedTime")
            out.append(RawRecord(
                source_system="quickbooks_online", resource=resource, source_id=str(row.get("Id", "")),
                payload=row, fetched_at=fetched,
                source_updated_at=datetime.fromisoformat(str(updated).replace("Z", "+00:00")) if updated else None,
            ))
    return out


def trial_balance_from_export(export: Mapping[str, Any]) -> QboTrialBalance:
    return parse_trial_balance_report(export["trial_balance"], date.fromisoformat(export["period_end"]))
