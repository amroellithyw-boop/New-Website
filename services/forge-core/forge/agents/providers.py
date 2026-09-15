"""Model providers.

Two real adapters cover the entire market:

* :class:`AnthropicProvider` talks to Claude through the official SDK, using
  native structured outputs (``output_config.format``), adaptive thinking,
  prompt caching on the stable system prompt, and server-side refusal fallbacks.
* :class:`OpenAICompatibleProvider` talks to everything else. OpenAI, xAI's
  Grok, Google Gemini, Groq, OpenRouter, Together, Fireworks, Mistral, DeepSeek,
  and every local runtime (Ollama, vLLM, LM Studio) expose the same
  chat-completions wire format, so one adapter with a base URL and a key reaches
  all of them, including open-weight models such as Hermes, Llama and Qwen.

Both return the same :class:`ModelResponse`, and neither knows anything about
finance. Every provider is imported lazily so the core has no hard dependency on
any vendor SDK.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from .catalogue import PROVIDER_ENV, ModelSpec

__all__ = [
    "ModelResponse",
    "Provider",
    "ProviderError",
    "ProviderRefused",
    "OfflineDeterministicProvider",
    "AnthropicProvider",
    "OpenAICompatibleProvider",
    "ImageInput",
    "build_provider",
]


class ProviderError(RuntimeError):
    """The provider could not complete the call (network, auth, 5xx)."""

    def __init__(self, message: str, *, retryable: bool = False, status: int | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status = status


class ProviderRefused(ProviderError):
    """The model declined the request. Not retryable on the same provider."""


@dataclass(frozen=True)
class ImageInput:
    """An image or PDF for a vision-capable model, as base64."""

    media_type: str
    data_base64: str

    @property
    def is_pdf(self) -> bool:
        return self.media_type == "application/pdf"


@dataclass
class ModelResponse:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    model_used: str = ""
    stop_reason: str = ""
    raw: Any = None


class Provider(ABC):
    kind: str = "abstract"

    @abstractmethod
    def complete(
        self,
        *,
        spec: ModelSpec,
        system: str,
        user: str,
        schema: dict[str, Any] | None = None,
        images: Sequence[ImageInput] = (),
        max_tokens: int = 4096,
        web_search: bool = False,
    ) -> ModelResponse: ...


# ---------------------------------------------------------------------------
# Offline
# ---------------------------------------------------------------------------


class OfflineDeterministicProvider(Provider):
    """No network, no key. Schema-valid output derived from the packet.

    This is what lets ForgeBench run on every commit and what lets the whole
    orchestration be exercised in a test. It proves plumbing, not judgement.
    """

    kind = "offline"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def complete(self, *, spec, system, user, schema=None, images=(), max_tokens=4096, web_search=False):
        from .offline import synthesise

        self.calls.append(spec.key)
        payload = synthesise(system=system, user=user, schema=schema)
        text = json.dumps(payload)
        return ModelResponse(
            text=text,
            input_tokens=max(len(system) + len(user), 1) // 4,
            output_tokens=max(len(text), 1) // 4,
            model_used=spec.model,
            stop_reason="end_turn",
        )


# ---------------------------------------------------------------------------
# Anthropic, via the official SDK
# ---------------------------------------------------------------------------


class AnthropicProvider(Provider):
    """Claude through ``anthropic.Anthropic``.

    Structured output uses ``output_config.format`` so the first text block is
    guaranteed valid JSON against the schema; no forced tool call is involved,
    which matters because the newest models reject forced tool choice. The
    system prompt carries a cache breakpoint: role mandates and operating rules
    are identical across thousands of calls and should be paid for once.
    """

    kind = "anthropic"

    def __init__(self, api_key: str | None = None, *, enable_fallbacks: bool = True) -> None:
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self._client = None
        self._enable_fallbacks = enable_fallbacks

    def _ensure(self):
        if self._client is None:
            try:
                import anthropic  # noqa: PLC0415 - lazy on purpose
            except ImportError as exc:
                raise ProviderError(
                    "the anthropic package is not installed; pip install 'forge-core[anthropic]'"
                ) from exc
            self._client = anthropic.Anthropic(api_key=self._api_key) if self._api_key else anthropic.Anthropic()
        return self._client

    def complete(self, *, spec, system, user, schema=None, images=(), max_tokens=4096, web_search=False):
        import anthropic  # noqa: PLC0415

        client = self._ensure()
        content: list[dict[str, Any]] = []
        for image in images:
            if image.is_pdf:
                content.append({"type": "document", "source": {
                    "type": "base64", "media_type": "application/pdf", "data": image.data_base64}})
            else:
                content.append({"type": "image", "source": {
                    "type": "base64", "media_type": image.media_type, "data": image.data_base64}})
        content.append({"type": "text", "text": user})

        kwargs: dict[str, Any] = {
            "model": spec.model,
            "max_tokens": min(max_tokens, spec.max_output_tokens),
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": content}],
        }
        if schema is not None:
            kwargs["output_config"] = {"format": {"type": "json_schema", "schema": _strict(schema)}}
        if web_search and spec.supports_web_search:
            kwargs["tools"] = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 3}]
        if self._enable_fallbacks and spec.model in ("claude-fable-5-1", "claude-opus-5"):
            kwargs["betas"] = ["server-side-fallback-2026-07-01"]
            kwargs["fallbacks"] = "default"

        try:
            if "betas" in kwargs:
                response = client.beta.messages.create(**kwargs)
            else:
                response = client.messages.create(**kwargs)
        except anthropic.RateLimitError as exc:
            raise ProviderError(str(exc), retryable=True, status=429) from exc
        except anthropic.APIStatusError as exc:
            raise ProviderError(str(exc), retryable=exc.status_code >= 500, status=exc.status_code) from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError(str(exc), retryable=True) from exc

        stop = getattr(response, "stop_reason", "") or ""
        if stop == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None) if details else None
            raise ProviderRefused(f"model refused ({category or 'unspecified'})")

        text = "".join(getattr(b, "text", "") for b in response.content if getattr(b, "type", "") == "text")
        usage = getattr(response, "usage", None)
        return ModelResponse(
            text=text,
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            model_used=getattr(response, "model", spec.model),
            stop_reason=stop,
            raw=response,
        )


def _strict(schema: dict[str, Any]) -> dict[str, Any]:
    """Make a pydantic schema acceptable to strict JSON-schema modes.

    Strict modes want ``additionalProperties: false`` on every object and every
    property listed as required. Pydantic emits neither by default for optional
    fields, so this walks the schema once and normalises it.
    """
    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            out = {k: walk(v) for k, v in node.items()}
            if out.get("type") == "object" and "properties" in out:
                out.setdefault("additionalProperties", False)
                out["required"] = list(out["properties"].keys())
            return out
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)


# ---------------------------------------------------------------------------
# Everything that speaks chat-completions
# ---------------------------------------------------------------------------


class OpenAICompatibleProvider(Provider):
    """One adapter for OpenAI, xAI, Gemini, Groq, OpenRouter, Together,
    Fireworks, Mistral, DeepSeek, Ollama, vLLM and LM Studio.

    Uses the standard library rather than a vendor SDK so that adding a provider
    is a base URL and a key, not a dependency. Structured output uses
    ``response_format: json_schema`` where the provider supports it, and falls
    back to a JSON-object instruction plus client-side validation where it does
    not; the gateway re-prompts on validation failure either way.
    """

    kind = "openai_compatible"

    def __init__(self, provider: str, *, api_key: str | None = None, base_url: str | None = None,
                 timeout: int = 120) -> None:
        env_var, default_base = PROVIDER_ENV.get(provider, ("", None))
        self.provider = provider
        self._base_url = (base_url or os.environ.get(f"{provider.upper()}_BASE_URL") or default_base or "").rstrip("/")
        raw_key = api_key or (os.environ.get(env_var) if env_var else None)
        # Local runtimes accept any key; Ollama's env var is a host, not a key.
        self._api_key = raw_key if provider not in ("ollama", "vllm") else (raw_key or "local")
        if provider == "ollama" and os.environ.get("OLLAMA_HOST") and not base_url:
            host = os.environ["OLLAMA_HOST"].rstrip("/")
            self._base_url = host if host.endswith("/v1") else f"{host}/v1"
        self._timeout = timeout
        if not self._base_url:
            raise ProviderError(f"no base URL configured for provider {provider!r}")

    def complete(self, *, spec, system, user, schema=None, images=(), max_tokens=4096, web_search=False):
        user_content: Any
        if images:
            parts: list[dict[str, Any]] = [{"type": "text", "text": user}]
            for image in images:
                if image.is_pdf:
                    # Most chat-completions providers do not accept PDFs inline.
                    # The gateway routes PDFs to a provider that does; if one
                    # reaches here, say so rather than silently sending nothing.
                    raise ProviderError(f"{self.provider} does not accept PDF input on this route")
                parts.append({"type": "image_url", "image_url": {
                    "url": f"data:{image.media_type};base64,{image.data_base64}"}})
            user_content = parts
        else:
            user_content = user

        body: dict[str, Any] = {
            "model": spec.model,
            "max_tokens": min(max_tokens, spec.max_output_tokens),
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
        }
        if schema is not None:
            if spec.supports_json_schema:
                body["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": schema.get("title", "result"), "schema": _strict(schema), "strict": True},
                }
            else:
                body["response_format"] = {"type": "json_object"}
                body["messages"][0]["content"] += (
                    "\n\nRespond with a single JSON object matching this schema exactly:\n"
                    + json.dumps(schema)
                )

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }
        if self.provider == "openrouter":
            headers["HTTP-Referer"] = "https://profitforge.ca"
            headers["X-Title"] = "ForgeOS"

        request = urllib.request.Request(
            f"{self._base_url}/chat/completions",
            data=json.dumps(body).encode(),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as resp:
                payload = json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:500]
            retryable = exc.code in (408, 409, 429) or exc.code >= 500
            raise ProviderError(f"{self.provider} HTTP {exc.code}: {detail}", retryable=retryable, status=exc.code) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ProviderError(f"{self.provider} unreachable: {exc}", retryable=True) from exc

        choices = payload.get("choices") or []
        if not choices:
            raise ProviderError(f"{self.provider} returned no choices: {str(payload)[:300]}")
        message = choices[0].get("message", {}) or {}
        finish = choices[0].get("finish_reason", "") or ""
        if finish == "content_filter":
            raise ProviderRefused(f"{self.provider} content filter")
        text = message.get("content") or ""
        if isinstance(text, list):
            text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
        usage = payload.get("usage", {}) or {}
        return ModelResponse(
            text=text,
            input_tokens=int(usage.get("prompt_tokens", 0) or 0),
            output_tokens=int(usage.get("completion_tokens", 0) or 0),
            cache_read_tokens=int((usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0),
            model_used=payload.get("model", spec.model),
            stop_reason=finish,
            raw=payload,
        )


def build_provider(provider: str, **kwargs: Any) -> Provider:
    """Instantiate the right adapter for a logical provider key."""
    if provider == "offline":
        return OfflineDeterministicProvider()
    if provider == "anthropic":
        return AnthropicProvider(**kwargs)
    return OpenAICompatibleProvider(provider, **kwargs)
