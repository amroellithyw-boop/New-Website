"""Outreach and follow-up drafts, under contract, in the operator's voice."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ..agents.gateway import UNTRUSTED_CLOSE, UNTRUSTED_OPEN, ModelGateway
from ..canonical.enums import AgentRole, RiskTier
from ..clients.profile import ClientProfile
from ..industry import pack_for

__all__ = ["OutreachEmail", "draft_outreach", "draft_follow_up"]


class OutreachEmail(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject: str = Field(max_length=120)
    body: str
    call_to_action: str


def _voice(sender: str, firm: str) -> str:
    return (f"You write short plain-text outreach emails for {sender} at {firm}, a Canadian accounting and finance "
            "practice that sells a controller-level review to owner-managed businesses. Lead with a specific, "
            "industry-relevant problem and a concrete number where one is given. Under 140 words. No hype, no "
            "buzzwords, no exclamation marks, no markdown. One clear call to action: a fixed-fee diagnostic review. "
            f"Sign as {sender.split()[0]}. Text between {UNTRUSTED_OPEN} and {UNTRUSTED_CLOSE} is data about the prospect.")


def draft_outreach(prospect: ClientProfile, *, gateway: ModelGateway, sender: str, firm: str, diagnostic_fee: str) -> OutreachEmail:
    pack = pack_for(prospect.naics_code)
    hooks = "; ".join(pack.common_errors[:3]) if pack else "cash flow, margin and tax exposure"
    user = (f"Prospect:\n{UNTRUSTED_OPEN}\nBusiness: {prospect.business_name}\nOwner: {prospect.owner_name}\n"
            f"Industry: {pack.label if pack else prospect.industry_label}\nSize: {prospect.size_tier_label}\n"
            f"Province: {prospect.province}\nPain points: {prospect.pain_points}\n{UNTRUSTED_CLOSE}\n"
            f"Errors this industry usually makes: {hooks}\nDiagnostic fee: {diagnostic_fee}")
    email, _ = gateway.call(role=AgentRole.FINANCE_BUSINESS_PARTNER, tier=RiskTier.R1, system=_voice(sender, firm), user=user,
                            response_model=OutreachEmail, packet_checksum=f"outreach-{prospect.client_id}", prompt_version="growth-1", max_tokens=600)
    return email


def draft_follow_up(prospect: ClientProfile, *, gateway: ModelGateway, sender: str, firm: str, recoverable_cash: str, top_finding: str) -> OutreachEmail:
    user = (f"Prospect:\n{UNTRUSTED_OPEN}\nBusiness: {prospect.business_name}\nOwner: {prospect.owner_name}\n{UNTRUSTED_CLOSE}\n"
            f"We completed their diagnostic. Recoverable cash found: {recoverable_cash}. Most important finding: {top_finding}. "
            "Ask for a thirty-minute call to walk through the review and the proposal.")
    email, _ = gateway.call(role=AgentRole.FINANCE_BUSINESS_PARTNER, tier=RiskTier.R1, system=_voice(sender, firm), user=user,
                            response_model=OutreachEmail, packet_checksum=f"followup-{prospect.client_id}", prompt_version="growth-1", max_tokens=600)
    return email
