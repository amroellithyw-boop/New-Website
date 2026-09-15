"""Deterministic synthesis for the offline provider.

Kept apart from the provider so the shape of a fake response is reviewable on
its own. It must produce output valid against every contract the system uses,
because a contract change that breaks this file breaks CI, which is the point.
"""

from __future__ import annotations

import json
import re
from typing import Any

__all__ = ["synthesise"]


def _extract_evidence_ids(user: str, limit: int = 4) -> list[str]:
    ids: list[str] = []
    try:
        start = user.index("{")
        data = json.loads(user[start: user.rindex("}") + 1])
        for ref in data.get("evidence", [])[:limit]:
            if "id" in ref:
                ids.append(str(ref["id"]))
    except Exception:  # noqa: BLE001
        pass
    if not ids:
        ids = re.findall(r"\b(?:TXN|SEED|OI|LN|ST|DOC)-[A-Za-z0-9\-]+", user)[:limit]
    return ids or ["evidence-unavailable"]


def synthesise(*, system: str, user: str, schema: dict[str, Any] | None) -> dict[str, Any]:
    role = "bookkeeper"
    m = re.search(r"You are the ([a-z_]+)", system)
    if m:
        role = m.group(1)
    title = (schema or {}).get("title", "")
    evidence = _extract_evidence_ids(user)

    if "ReviewDecision" in title:
        return {
            "role": role, "decision": "approve",
            "evidence_test_passed": True, "control_test_passed": True,
            "strongest_alternative": (
                "The condition could reflect a legitimate but unusual transaction; rejected "
                "because the deterministic calculations in the packet reproduce the exception."
            ),
            "notes": [],
            "residual_risk": "Offline review: deterministic evidence checked, no model judgement applied.",
            "approved_scope": "analysis_only", "escalate_to": None, "escalation_reason": None,
        }
    if "ExtractedDocument" in title:
        return {
            "document_type": "other", "document_date": None, "vendor_or_issuer": "",
            "total_amount": None, "subtotal": None, "tax_amount": None, "tax_number": "",
            "payment_method": "unknown", "currency": "CAD", "summary": "offline extraction",
            "transactions": [], "line_items": [], "closing_balance": None,
            "confidence": 0, "notes": "Offline provider performs no extraction.",
        }
    if "VendorResearch" in title:
        return {
            "vendor_name": "", "vendor_type": "unknown", "industry": "", "typical_category": "other",
            "tax_applicable": True, "input_tax_credit_claimable": True, "credit_notes": "",
            "cra_notes": "", "confidence": 0, "personal_use_risk": False, "personal_use_note": "",
        }
    if "AccountingProposal" in title:
        return {"entries": [], "questions_for_client": [], "confidence": 0,
                "notes": "Offline provider proposes no entries."}
    if "ClientEmail" in title:
        return {"subject": "Questions about a few transactions", "body": "Offline draft.", "transaction_count": 0}
    if "OnboardingPlan" in title:
        return {"welcome": "Offline plan.", "engagement_summary": "", "week_1": [], "week_2": [],
                "month_1": [], "documents_needed": [], "immediate_compliance_risks": [],
                "tax_planning_priorities": [], "first_call_agenda": [], "context_notes": ""}
    return {
        "role": role,
        "conclusion": "The control condition is reproduced by the deterministic calculations in the "
                      "evidence packet. Prepared offline without model reasoning.",
        "supporting_evidence_ids": evidence, "calculations_relied_on": [],
        "assumptions": ["Evidence packet is complete for the stated objective"],
        "alternative_explanations": [], "missing_evidence": [],
        "confidence": "0.5",
        "confidence_reason": "Offline deterministic preparer; confidence reflects the control, not a model.",
        "proposed_action": {"kind": "investigate",
                            "summary": "Review the evidence packet and confirm the control conclusion.",
                            "reversible": True, "requires_human_approval": True, "estimated_value": None},
    }
