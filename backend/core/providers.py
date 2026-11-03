"""Dual-provider LLM abstraction - Meta Llama API + Fireworks AI fallback.

Every provider speaks the OpenAI-compatible chat-completions protocol, so the
payload stays identical across both. The only difference is base URL, API key
and model identifier - all injected via env vars (Modal secret or local .env).

Failure policy
--------------
* `ProviderError` (timeout / 4xx / 5xx / unparseable / schema mismatch) is
  *soft*: the router moves on to the next provider.
* `AllProvidersFailed` (every provider failed) is *hard*: the corrector loop
  catches it and falls back to its deterministic physics-only relaxation.

No secrets are ever logged or echoed in the exception messages.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

__all__ = [
    "AllProvidersFailed",
    "FireworksImageProvider",
    "FireworksProvider",
    "MetaImageProvider",
    "MetaProvider",
    "ModelRouter",
    "ProviderError",
    "build_fireworks_image_provider",
    "build_image_provider",
    "build_image_providers",
]

DEFAULT_TIMEOUT = httpx.Timeout(connect=8.0, read=25.0, write=25.0, pool=5.0)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------
class ProviderError(Exception):
    """Recoverable provider failure - the router should try the next provider."""

    def __init__(self, provider: str, code: str, message: str):
        self.provider = provider
        self.code = code
        self.message = message
        super().__init__(f"[{provider}:{code}] {message}")


class AllProvidersFailed(Exception):
    """Terminal: every configured provider failed on this request."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__(f"all providers failed ({len(errors)} attempt(s))")


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------
def _validate_required(parsed: Any, schema: dict[str, Any] | None) -> bool:
    """Cheap schema check - only top-level `required` keys."""
    if not schema:
        return True
    if not isinstance(parsed, dict):
        return False
    for key in schema.get("required", []):
        if key not in parsed:
            return False
    return True


class _BaseProvider:
    name: str = "base"
    #: Safe fallback when a caller does not specify ``max_tokens`` (0/None).
    DEFAULT_MAX_TOKENS: int = 1024

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model_id: str,
        timeout: httpx.Timeout | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model_id = model_id
        self.timeout = timeout or DEFAULT_TIMEOUT
        self.transport = transport  # injected in tests via httpx.MockTransport

    # -- HTTP ---------------------------------------------------------------
    @property
    def headers(self) -> dict[str, str]:
        return {
            "authorization": f"Bearer {self.api_key}",
            "content-type": "application/json",
            "accept": "application/json",
        }

    async def _post(self, payload: dict[str, Any]) -> httpx.Response:
        url = f"{self.base_url}/chat/completions"
        async with httpx.AsyncClient(
            timeout=self.timeout, transport=self.transport
        ) as client:
            return await client.post(url, headers=self.headers, json=payload)

    def _body_overrides(self) -> dict[str, Any]:
        """Optional provider-specific fields merged into the chat-completions body."""
        return {}

    # -- Public API ---------------------------------------------------------
    async def complete(
        self,
        messages: list[dict[str, Any]],
        schema: dict[str, Any] | None = None,
        *,
        max_tokens: int = 0,
        temperature: float = 0.2,
    ) -> dict[str, Any]:
        if not self.api_key:
            raise ProviderError(self.name, "E_NO_KEY", f"{self.name} API key missing")

        if max_tokens <= 0:
            max_tokens = self.DEFAULT_MAX_TOKENS

        payload: dict[str, Any] = {
            "model": self.model_id,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            "stream": False,
            **self._body_overrides(),
        }

        try:
            resp = await self._post(payload)
        except httpx.TimeoutException as exc:
            raise ProviderError(self.name, "E_TIMEOUT", str(exc)) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(self.name, "E_HTTP", str(exc)) from exc

        if resp.status_code >= 400:
            # Never echo the body verbatim (may contain PII or key fragments).
            raise ProviderError(
                self.name,
                f"E_STATUS_{resp.status_code}",
                f"HTTP {resp.status_code} from {self.name}",
            )

        try:
            data = resp.json()
            choice = data["choices"][0]
            content = choice["message"]["content"]
        except (KeyError, TypeError, IndexError) as exc:
            raise ProviderError(self.name, "E_PARSE", str(exc)) from exc

        if not isinstance(content, str) or not content.strip():
            # Two distinct causes: reasoning models can spend the whole budget
            # on internal thinking, while chat-instruct models simply ran into
            # max_tokens. Point the operator at the knob that actually applies.
            finish = choice.get("finish_reason")
            usage = data.get("usage") or {}
            details = usage.get("completion_tokens_details") or {}
            reasoning = details.get("reasoning_tokens") or 0
            hint = (
                f"finish_reason={finish}, reasoning_tokens={reasoning}, "
                f"completion_tokens={usage.get('completion_tokens')}"
            )
            code = "E_EMPTY_CONTENT"
            message = f"{self.name} returned empty content ({hint})"
            if reasoning > 0:
                message += (
                    "; the model consumed all tokens on reasoning ("
                    f"reasoning_tokens={reasoning}) - lower "
                    "FIREWORKS_REASONING_EFFORT or raise FIREWORKS_MAX_TOKENS"
                )
            elif finish == "length":
                message += (
                    "; the reply was truncated by max_tokens - raise "
                    "FIREWORKS_MAX_TOKENS or shorten the system prompt "
                    "(model carries no reasoning tokens)"
                )
            raise ProviderError(self.name, code, message)

        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ProviderError(self.name, "E_PARSE", str(exc)) from exc

        if not _validate_required(parsed, schema):
            raise ProviderError(
                self.name, "E_SCHEMA", "response missing required keys"
            )
        return parsed


# ---------------------------------------------------------------------------
# Concrete providers
# ---------------------------------------------------------------------------
class MetaProvider(_BaseProvider):
    name = "meta"

    def __init__(self) -> None:
        super().__init__(
            base_url=os.environ.get("META_BASE_URL", "https://api.llama.com/v1"),
            api_key=os.environ.get("META_LLAMA_KEY", ""),
            model_id=os.environ.get("META_MODEL_ID", "meta-muse-wonder-2026"),
        )


class FireworksProvider(_BaseProvider):
    name = "fireworks"
    #: The current default model is ``muse-spark-open`` - a Meta-family
    #: **reasoning** model (verified live via ``/inference/v1/models``). It
    #: needs headroom for its internal thinking (~640 reasoning tokens at
    #: ``reasoning_effort=low``), so the default cap is 4096 rather than the
    #: chat-instruct 1024. If you point ``FIREWORKS_MODEL_ID`` at a
    #: non-reasoning chat model, set ``FIREWORKS_MAX_TOKENS=1024`` and leave
    #: ``FIREWORKS_REASONING_EFFORT`` empty (chat-instruct models reject the
    #: field).
    DEFAULT_MAX_TOKENS = 4096

    def __init__(self) -> None:
        # Strategy agent only. 45 s covers the reasoning round-trip with slack;
        # raise ``FIREWORKS_READ_TIMEOUT_S`` for slower models.
        read_s = float(os.environ.get("FIREWORKS_READ_TIMEOUT_S", "45"))
        super().__init__(
            base_url=os.environ.get(
                "FIREWORKS_BASE_URL", "https://api.fireworks.ai/inference/v1"
            ),
            api_key=os.environ.get("FIREWORKS_API_KEY", ""),
            model_id=os.environ.get(
                "FIREWORKS_MODEL_ID",
                "accounts/fireworks/models/muse-spark-open",
            ),
            timeout=httpx.Timeout(
                connect=8.0, read=read_s, write=read_s, pool=5.0
            ),
        )
        # Output-token cap applied to every complete() call on this provider.
        self.max_tokens = int(
            os.environ.get("FIREWORKS_MAX_TOKENS", str(self.DEFAULT_MAX_TOKENS))
        )
        # Reasoning effort: defaults to ``low`` because the default model is a
        # reasoning model. Set ``FIREWORKS_REASONING_EFFORT=`` (explicitly
        # empty) to omit the field entirely, which some chat-instruct models
        # require.
        self.reasoning_effort = (
            os.environ.get("FIREWORKS_REASONING_EFFORT", "low") or ""
        ).strip()

    def _body_overrides(self) -> dict[str, Any]:
        if not self.reasoning_effort:
            return {}
        return {"reasoning_effort": self.reasoning_effort}

    async def complete(  # type: ignore[override]
        self,
        messages: list[dict[str, Any]],
        schema: dict[str, Any] | None = None,
        *,
        max_tokens: int = 0,  # 0 = "use self.max_tokens" (the env cap)
        temperature: float = 0.2,
    ) -> dict[str, Any]:
        # Caller-supplied value wins but never exceeds the env cap, so a
        # runaway prompt cannot silently blow through Fireworks' rate budget.
        effective = max_tokens if max_tokens > 0 else self.max_tokens
        if self.max_tokens > 0:
            effective = min(effective, self.max_tokens)
        return await super().complete(
            messages,
            schema,
            max_tokens=effective,
            temperature=temperature,
        )


class MetaImageProvider(_BaseProvider):
    """Stage-2 spatial renderer (Muse Image).

    Deliberately **not** a member of ``ModelRouter``'s fallback chain: an
    image/spatial model is not interchangeable with a strategy model. If this
    provider is unavailable the spatial renderer falls back to a deterministic
    local rasteriser instead (see ``agents/spatial_renderer.py``).
    """

    name = "meta_image"

    def __init__(self) -> None:
        image_timeout = float(os.environ.get("META_IMAGE_TIMEOUT_S", "45"))
        super().__init__(
            base_url=os.environ.get("META_IMAGE_BASE_URL")
            or os.environ.get("META_BASE_URL", "https://api.llama.com/v1"),
            api_key=os.environ.get("META_LLAMA_KEY", ""),
            model_id=os.environ.get("META_IMAGE_MODEL_ID", ""),
            timeout=httpx.Timeout(
                connect=8.0, read=image_timeout, write=image_timeout, pool=5.0
            ),
        )

    async def complete(
        self,
        messages: list[dict[str, Any]],
        schema: dict[str, Any] | None = None,
        *,
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> dict[str, Any]:
        if not self.api_key:
            raise ProviderError(self.name, "E_NO_KEY", "META_LLAMA_KEY missing")
        if not self.model_id:
            raise ProviderError(
                self.name, "E_NO_MODEL", "META_IMAGE_MODEL_ID not configured"
            )
        return await super().complete(
            messages, schema, max_tokens=max_tokens, temperature=temperature
        )


class FireworksImageProvider(_BaseProvider):
    """Stage-2 spatial renderer fallback - the Fireworks mirror of Muse Image.

    Counterpart to :class:`MetaImageProvider`: same signature, same expected
    JSON shape (``{"footprints": [...], "notes": [...]}``), but pointed at the
    Fireworks inference endpoint so the spatial stage survives a Meta API
    outage instead of dropping straight to the deterministic rasteriser.

    Uses its own env-var namespace so the spatial model can differ from the
    strategy model if you want:

    * ``FIREWORKS_IMAGE_MODEL_ID`` (default ``accounts/fireworks/models/muse-spark-open``)
    * ``FIREWORKS_IMAGE_BASE_URL`` (defaults to ``FIREWORKS_BASE_URL``)
    * ``FIREWORKS_IMAGE_TIMEOUT_S`` (defaults to ``FIREWORKS_READ_TIMEOUT_S`` / 45s)
    * ``FIREWORKS_IMAGE_MAX_TOKENS`` (defaults to ``FIREWORKS_MAX_TOKENS`` / 4096)
    * ``FIREWORKS_IMAGE_REASONING_EFFORT`` (defaults to ``FIREWORKS_REASONING_EFFORT`` / ``low``)
    """

    name = "fireworks_image"
    #: mirror of :class:`FireworksProvider` - reasoning models need headroom.
    DEFAULT_MAX_TOKENS = 4096

    def __init__(self) -> None:
        image_timeout = float(
            os.environ.get("FIREWORKS_IMAGE_TIMEOUT_S")
            or os.environ.get("FIREWORKS_READ_TIMEOUT_S", "45")
        )
        super().__init__(
            base_url=os.environ.get("FIREWORKS_IMAGE_BASE_URL")
            or os.environ.get(
                "FIREWORKS_BASE_URL", "https://api.fireworks.ai/inference/v1"
            ),
            api_key=os.environ.get("FIREWORKS_API_KEY", ""),
            model_id=os.environ.get("FIREWORKS_IMAGE_MODEL_ID")
            or os.environ.get(
                "FIREWORKS_MODEL_ID",
                "accounts/fireworks/models/muse-spark-open",
            ),
            timeout=httpx.Timeout(
                connect=8.0, read=image_timeout, write=image_timeout, pool=5.0
            ),
        )
        self.max_tokens = int(
            os.environ.get("FIREWORKS_IMAGE_MAX_TOKENS")
            or os.environ.get(
                "FIREWORKS_MAX_TOKENS", str(self.DEFAULT_MAX_TOKENS)
            )
        )
        self.reasoning_effort = (
            os.environ.get("FIREWORKS_IMAGE_REASONING_EFFORT")
            or os.environ.get("FIREWORKS_REASONING_EFFORT", "low")
        ).strip()

    def _body_overrides(self) -> dict[str, Any]:
        if not self.reasoning_effort:
            return {}
        return {"reasoning_effort": self.reasoning_effort}

    async def complete(  # type: ignore[override]
        self,
        messages: list[dict[str, Any]],
        schema: dict[str, Any] | None = None,
        *,
        max_tokens: int = 0,  # 0 = "use self.max_tokens" (the env cap)
        temperature: float = 0.2,
    ) -> dict[str, Any]:
        if not self.api_key:
            raise ProviderError(self.name, "E_NO_KEY", "FIREWORKS_API_KEY missing")
        if not self.model_id:
            raise ProviderError(
                self.name,
                "E_NO_MODEL",
                "FIREWORKS_IMAGE_MODEL_ID not configured",
            )
        effective = max_tokens if max_tokens > 0 else self.max_tokens
        if self.max_tokens > 0:
            effective = min(effective, self.max_tokens)
        return await super().complete(
            messages,
            schema,
            max_tokens=effective,
            temperature=temperature,
        )


def build_image_provider() -> MetaImageProvider | None:
    """Instantiate the Muse Image provider **only** when it is configured.

    Keeps /simulate free of surprise outbound calls on machines without keys,
    and lets tests assert the FALLBACK path deterministically.
    """
    if not os.environ.get("META_IMAGE_MODEL_ID"):
        return None
    if not os.environ.get("META_LLAMA_KEY"):
        return None
    return MetaImageProvider()


def build_fireworks_image_provider() -> FireworksImageProvider | None:
    """Instantiate the Fireworks spatial fallback only when it is configured.

    Requires both ``FIREWORKS_API_KEY`` and a model id (either
    ``FIREWORKS_IMAGE_MODEL_ID`` or a non-empty ``FIREWORKS_MODEL_ID``).
    """
    if not os.environ.get("FIREWORKS_API_KEY"):
        return None
    if not (
        os.environ.get("FIREWORKS_IMAGE_MODEL_ID")
        or os.environ.get("FIREWORKS_MODEL_ID")
    ):
        return None
    return FireworksImageProvider()


def build_image_providers() -> list[_BaseProvider]:
    """Ordered spatial-stage fallback chain: Meta Image -> Fireworks Image.

    Returns only the providers that are actually configured, so an
    unconfigured deployment (no Meta key, no Fireworks key) still gets the
    deterministic rasteriser via the empty list.
    """
    providers: list[_BaseProvider] = []
    meta = build_image_provider()
    if meta is not None:
        providers.append(meta)
    fireworks = build_fireworks_image_provider()
    if fireworks is not None:
        providers.append(fireworks)
    return providers


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------
class ModelRouter:
    """Ordered fallback chain across providers.

    ``PROVIDER_PRIMARY=fireworks`` flips the default order for local testing.
    """

    def __init__(self, providers: list[_BaseProvider] | None = None) -> None:
        if providers is not None:
            self.providers = providers
            return

        meta = MetaProvider()
        fireworks = FireworksProvider()
        primary = (os.environ.get("PROVIDER_PRIMARY") or "meta").lower()
        self.providers = [fireworks, meta] if primary == "fireworks" else [meta, fireworks]

    async def complete(
        self,
        messages: list[dict[str, Any]],
        schema: dict[str, Any] | None = None,
        *,
        max_tokens: int = 0,
        temperature: float = 0.2,
    ) -> tuple[str, dict[str, Any]]:
        """Return ``(provider_name, parsed_json)`` from the first success."""
        errors: list[str] = []
        for provider in self.providers:
            try:
                result = await provider.complete(
                    messages,
                    schema,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
            except ProviderError as exc:
                logger.warning(
                    "provider_fail provider=%s code=%s", provider.name, exc.code
                )
                errors.append(str(exc))
                continue
            logger.info("provider_success provider=%s", provider.name)
            return provider.name, result

        raise AllProvidersFailed(errors)
