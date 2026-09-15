"""Model-backed preparers and reviewers, under enforced contract."""

from .contracts import (
    AgentCall,
    CorrectionResponse,
    PreparerProposal,
    ProposedAction,
    ReviewDecision,
    ReviewNote,
)
from .gateway import GatewayError, ModelGateway, ModelSpec, OfflineDeterministicProvider
from .roles import PROMPT_VERSION, build_system_prompt, build_user_prompt, run_review_loop

__all__ = [
    "PreparerProposal", "ProposedAction", "ReviewDecision", "ReviewNote",
    "CorrectionResponse", "AgentCall",
    "ModelGateway", "ModelSpec", "OfflineDeterministicProvider", "GatewayError",
    "build_system_prompt", "build_user_prompt", "run_review_loop", "PROMPT_VERSION",
]
