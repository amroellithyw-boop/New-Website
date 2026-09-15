"""The model catalogue: every model ForgeOS can route to, as data.

Adding a provider is a row here plus an API key in the environment. Nothing
above the gateway changes. That is what "provider independence" means in
practice, and it is also what lets the system be cheap without being worse:
the cheapest model that is *capable enough* for a tier does the work, the
expensive ones are reserved for adversarial review and material judgement, and
an open-weight model running locally does extraction for nothing at all.

Prices are USD per million tokens and are illustrative defaults for cost
telemetry. They drift monthly; the deployment should override them from
configuration. Anthropic prices are taken from the current API reference; the
rest are published list prices at the time of writing and should be verified.

Families matter more than names. Reviewer independence is enforced at family
level because two models from one family fail the same way.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

__all__ = ["ModelSpec", "CATALOGUE", "PROVIDER_ENV", "available_models", "ProviderKind"]

ProviderKind = str  # "anthropic" | "openai_compatible" | "offline"


@dataclass(frozen=True)
class ModelSpec:
    provider: str
    """Logical provider key: anthropic, openai, xai, groq, openrouter, together,
    mistral, deepseek, ollama, vllm, offline."""
    model: str
    family: str
    kind: ProviderKind
    input_cost_per_mtok: Decimal
    output_cost_per_mtok: Decimal
    quality: int
    """1 (small, fast) to 5 (frontier). Drives tier routing."""
    supports_vision: bool = False
    supports_json_schema: bool = True
    supports_web_search: bool = False
    context_tokens: int = 128_000
    max_output_tokens: int = 8_192
    local: bool = False
    base_url: str | None = None
    notes: str = ""

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.model}"

    @property
    def is_free(self) -> bool:
        return self.input_cost_per_mtok == 0 and self.output_cost_per_mtok == 0

    def cost_micros(self, input_tokens: int, output_tokens: int) -> int:
        cost = (
            Decimal(input_tokens) * self.input_cost_per_mtok
            + Decimal(output_tokens) * self.output_cost_per_mtok
        ) / Decimal(1_000_000)
        return int((cost * Decimal(1_000_000)).quantize(Decimal(1)))


def _m(provider, model, family, kind, inp, out, q, **kw) -> ModelSpec:
    return ModelSpec(provider, model, family, kind, Decimal(str(inp)), Decimal(str(out)), q, **kw)


# Environment variable that activates each provider, and the base URL for the
# OpenAI-compatible ones. Every entry here speaks the same chat-completions
# wire format, which is why one adapter covers all of them.
PROVIDER_ENV: dict[str, tuple[str, str | None]] = {
    "anthropic": ("ANTHROPIC_API_KEY", None),
    "openai": ("OPENAI_API_KEY", "https://api.openai.com/v1"),
    "xai": ("XAI_API_KEY", "https://api.x.ai/v1"),
    "groq": ("GROQ_API_KEY", "https://api.groq.com/openai/v1"),
    "openrouter": ("OPENROUTER_API_KEY", "https://openrouter.ai/api/v1"),
    "together": ("TOGETHER_API_KEY", "https://api.together.xyz/v1"),
    "fireworks": ("FIREWORKS_API_KEY", "https://api.fireworks.ai/inference/v1"),
    "mistral": ("MISTRAL_API_KEY", "https://api.mistral.ai/v1"),
    "deepseek": ("DEEPSEEK_API_KEY", "https://api.deepseek.com/v1"),
    "gemini": ("GEMINI_API_KEY", "https://generativelanguage.googleapis.com/v1beta/openai"),
    "ollama": ("OLLAMA_HOST", "http://localhost:11434/v1"),
    "vllm": ("VLLM_BASE_URL", None),
    "offline": ("", None),
}


CATALOGUE: tuple[ModelSpec, ...] = (
    # ---- offline, always available ----------------------------------------
    _m("offline", "deterministic", "offline", "offline", 0, 0, 1,
       notes="Schema-valid deterministic output from the packet; CI and tests"),

    # ---- Anthropic (official SDK) ------------------------------------------
    _m("anthropic", "claude-haiku-4-5", "claude", "anthropic", 1.00, 5.00, 2,
       supports_vision=True, supports_web_search=True, context_tokens=200_000,
       notes="Extraction, classification, routine first pass"),
    _m("anthropic", "claude-sonnet-5", "claude", "anthropic", 2.00, 10.00, 3,
       supports_vision=True, supports_web_search=True, context_tokens=1_000_000, max_output_tokens=64_000,
       notes="Manager review, vendor research, narratives"),
    _m("anthropic", "claude-opus-5", "claude", "anthropic", 5.00, 25.00, 4,
       supports_vision=True, supports_web_search=True, context_tokens=1_000_000, max_output_tokens=64_000,
       notes="Controller, adversary, material judgement"),
    _m("anthropic", "claude-fable-5-1", "claude", "anthropic", 10.00, 50.00, 5,
       supports_vision=True, supports_web_search=True, context_tokens=1_000_000, max_output_tokens=128_000,
       notes="Reserved for R4 synthesis when explicitly enabled"),

    # ---- OpenAI ------------------------------------------------------------
    _m("openai", "gpt-4o-mini", "gpt", "openai_compatible", 0.15, 0.60, 2, supports_vision=True),
    _m("openai", "gpt-4.1", "gpt", "openai_compatible", 2.00, 8.00, 3, supports_vision=True, context_tokens=1_000_000),
    _m("openai", "o4-mini", "gpt", "openai_compatible", 1.10, 4.40, 3, notes="Reasoning; JSON schema supported"),
    _m("openai", "gpt-5", "gpt", "openai_compatible", 1.25, 10.00, 4, supports_vision=True, context_tokens=400_000,
       notes="Independent reviewer family for Claude-prepared R3/R4 work"),

    # ---- xAI Grok ----------------------------------------------------------
    _m("xai", "grok-4-fast", "grok", "openai_compatible", 0.20, 0.50, 3, supports_vision=True, context_tokens=2_000_000),
    _m("xai", "grok-4", "grok", "openai_compatible", 3.00, 15.00, 4, supports_vision=True, context_tokens=256_000,
       notes="Third independent family for R4 adversarial review"),

    # ---- Google Gemini (OpenAI-compatible endpoint) ------------------------
    _m("gemini", "gemini-2.5-flash", "gemini", "openai_compatible", 0.30, 2.50, 3, supports_vision=True, context_tokens=1_000_000),
    _m("gemini", "gemini-2.5-pro", "gemini", "openai_compatible", 1.25, 10.00, 4, supports_vision=True, context_tokens=1_000_000),

    # ---- Open-weight via hosted inference ----------------------------------
    _m("groq", "llama-3.3-70b-versatile", "llama", "openai_compatible", 0.59, 0.79, 2,
       notes="Very fast; batch classification and extraction of text"),
    _m("groq", "openai/gpt-oss-120b", "gpt-oss", "openai_compatible", 0.15, 0.60, 3,
       notes="Open-weight OpenAI model; cheap independent reviewer"),
    _m("together", "meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8", "llama", "openai_compatible", 0.27, 0.85, 3, supports_vision=True),
    _m("openrouter", "nousresearch/hermes-4-405b", "hermes", "openai_compatible", 0.90, 0.90, 3,
       notes="Nous Research Hermes; strong at structured tool-style output"),
    _m("openrouter", "deepseek/deepseek-r1", "deepseek", "openai_compatible", 0.55, 2.19, 4,
       notes="Open-weight reasoning; cheap adversarial second opinion"),
    _m("deepseek", "deepseek-chat", "deepseek", "openai_compatible", 0.27, 1.10, 3),
    _m("mistral", "mistral-large-latest", "mistral", "openai_compatible", 2.00, 6.00, 3),

    # ---- Local, free -------------------------------------------------------
    _m("ollama", "llama3.3", "llama", "openai_compatible", 0, 0, 2, local=True,
       notes="Free local extraction and classification when Ollama is running"),
    _m("ollama", "qwen2.5:14b", "qwen", "openai_compatible", 0, 0, 2, local=True),
    _m("ollama", "hermes3", "hermes", "openai_compatible", 0, 0, 2, local=True,
       notes="Nous Hermes locally; no data leaves the machine"),
    _m("ollama", "llava", "llava", "openai_compatible", 0, 0, 1, local=True, supports_vision=True,
       notes="Local vision for receipts when privacy demands it"),
)


def available_models(env: dict[str, str] | None = None) -> list[ModelSpec]:
    """Models whose provider has credentials (or needs none) in the environment.

    The offline model is always available, which is what lets the whole system
    run with no keys at all. Everything else appears the moment its key is set.
    """
    environ = env if env is not None else dict(os.environ)
    out: list[ModelSpec] = []
    for spec in CATALOGUE:
        var, _base = PROVIDER_ENV.get(spec.provider, ("", None))
        if spec.provider == "offline" or (var and environ.get(var)):
            out.append(spec)
    return out


def families(models: Iterable[ModelSpec]) -> set[str]:
    return {m.family for m in models}
