"""Provider abstraction + fallback router tests.

Two layers are exercised:
1. ``ModelRouter`` orchestration using stubbed providers (fast + deterministic).
2. ``_BaseProvider`` HTTP transport using ``httpx.MockTransport`` so we do not
   touch the network at all.
"""

from __future__ import annotations

import json
from typing import cast

import httpx
import pytest

from core.providers import (
    AllProvidersFailed,
    MetaImageProvider,
    ProviderError,
    _BaseProvider,
    build_image_provider,
    ModelRouter,
)


class _StubProvider:
    """Minimal provider double - no HTTP, deterministic behaviour."""

    def __init__(self, name: str, *, fail: bool = False, payload=None):
        self.name = name
        self.fail = fail
        self.calls = 0
        self.payload = payload if payload is not None else {"ok": True}

    async def complete(self, messages, schema=None, **kwargs):  # noqa: ANN001
        self.calls += 1
        if self.fail:
            raise ProviderError(self.name, "E_FAIL", "simulated failure")
        return self.payload


@pytest.mark.asyncio
async def test_router_returns_first_success_without_calling_backup() -> None:
    primary = _StubProvider("primary")
    backup = _StubProvider("backup")

    router = ModelRouter(providers=cast(list[_BaseProvider], [primary, backup]))
    name, payload = await router.complete([{"role": "user", "content": "hi"}])

    assert name == "primary"
    assert payload == {"ok": True}
    assert primary.calls == 1
    assert backup.calls == 0


@pytest.mark.asyncio
async def test_router_falls_back_when_primary_fails() -> None:
    primary = _StubProvider("primary", fail=True)
    backup = _StubProvider("backup", payload={"ok": "backup"})

    router = ModelRouter(providers=cast(list[_BaseProvider], [primary, backup]))
    name, payload = await router.complete([{"role": "user", "content": "hi"}])

    assert name == "backup"
    assert payload == {"ok": "backup"}
    assert primary.calls == 1
    assert backup.calls == 1


@pytest.mark.asyncio
async def test_router_raises_when_all_providers_fail() -> None:
    router = ModelRouter(
        providers=cast(list[_BaseProvider], [
            _StubProvider("primary", fail=True),
            _StubProvider("backup", fail=True),
        ])
    )

    with pytest.raises(AllProvidersFailed) as exc_info:
        await router.complete([{"role": "user", "content": "hi"}])

    errors = exc_info.value.errors
    assert len(errors) == 2
    assert any("primary" in e for e in errors)
    assert any("backup" in e for e in errors)


@pytest.mark.asyncio
async def test_base_provider_parses_valid_chat_completion() -> None:
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        body = {
            "choices": [
                {"message": {"content": json.dumps({"placement": "ok"})}}
            ]
        }
        return httpx.Response(200, json=body)

    provider = _BaseProvider(
        base_url="https://example.invalid/v1",
        api_key="test-key",
        model_id="test-model",
        transport=httpx.MockTransport(handler),
    )

    result = await provider.complete(
        [{"role": "user", "content": "hi"}],
        schema={"required": ["placement"]},
    )

    assert result == {"placement": "ok"}
    assert requested_urls == ["https://example.invalid/v1/chat/completions"]


@pytest.mark.asyncio
async def test_base_provider_rejects_http_4xx_without_leaking_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "bad key (never echoed)"})

    provider = _BaseProvider(
        base_url="https://example.invalid/v1",
        api_key="test-key",
        model_id="test-model",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ProviderError) as error_info:
        await provider.complete([{"role": "user", "content": "hi"}])

    assert error_info.value.code == "E_STATUS_401"
    assert "never echoed" not in str(error_info.value)


@pytest.mark.asyncio
async def test_base_provider_rejects_missing_api_key() -> None:
    provider = _BaseProvider(
        base_url="https://example.invalid/v1",
        api_key="",
        model_id="test-model",
    )

    with pytest.raises(ProviderError) as excinfo:
        await provider.complete([{"role": "user", "content": "hi"}])

    assert excinfo.value.code == "E_NO_KEY"


@pytest.mark.asyncio
async def test_base_provider_surfaces_schema_mismatch() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps({"wrong": 1})}}]},
        )

    provider = _BaseProvider(
        base_url="https://example.invalid/v1",
        api_key="test-key",
        model_id="test-model",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ProviderError) as excinfo:
        await provider.complete(
            [{"role": "user", "content": "hi"}],
            schema={"required": ["placement"]},
        )

    assert excinfo.value.code == "E_SCHEMA"


# ---------------------------------------------------------------------------
# MetaImageProvider (Stage 2 spatial renderer)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_meta_image_provider_calls_image_model_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("META_LLAMA_KEY", "test-key")
    monkeypatch.setenv("META_IMAGE_MODEL_ID", "meta-muse-image-2026")
    monkeypatch.setenv("META_IMAGE_BASE_URL", "https://image.invalid/v1")

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "footprints": [
                                        {
                                            "id": "A",
                                            "origin": [10, 10],
                                            "size": [4, 4],
                                        }
                                    ]
                                }
                            )
                        }
                    }
                ]
            },
        )

    provider = MetaImageProvider()
    provider.transport = httpx.MockTransport(handler)

    reply = await provider.complete(
        [{"role": "user", "content": "render"}],
        schema={"required": ["footprints"]},
    )

    assert reply["footprints"][0]["id"] == "A"
    assert captured["url"] == "https://image.invalid/v1/chat/completions"
    assert captured["auth"] == "Bearer test-key"
    # The image model id must be distinct from the strategy model id.
    assert captured["body"]["model"] == "meta-muse-image-2026"


@pytest.mark.asyncio
async def test_meta_image_provider_requires_model_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("META_LLAMA_KEY", "test-key")
    monkeypatch.delenv("META_IMAGE_MODEL_ID", raising=False)

    provider = MetaImageProvider()

    with pytest.raises(ProviderError) as excinfo:
        await provider.complete([{"role": "user", "content": "x"}])

    assert excinfo.value.code == "E_NO_MODEL"


@pytest.mark.asyncio
async def test_meta_image_provider_requires_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("META_LLAMA_KEY", raising=False)
    monkeypatch.setenv("META_IMAGE_MODEL_ID", "meta-muse-image-2026")

    provider = MetaImageProvider()

    with pytest.raises(ProviderError) as excinfo:
        await provider.complete([{"role": "user", "content": "x"}])

    assert excinfo.value.code == "E_NO_KEY"


def test_build_image_provider_returns_none_without_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("META_IMAGE_MODEL_ID", raising=False)
    monkeypatch.delenv("META_LLAMA_KEY", raising=False)
    assert build_image_provider() is None


def test_build_image_provider_returns_instance_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("META_LLAMA_KEY", "test-key")
    monkeypatch.setenv("META_IMAGE_MODEL_ID", "meta-muse-image-2026")

    provider = build_image_provider()
    assert isinstance(provider, MetaImageProvider)
    assert provider.model_id == "meta-muse-image-2026"


# ---------------------------------------------------------------------------
# FireworksProvider — env-driven timeout + output-token cap
# ---------------------------------------------------------------------------
def test_fireworks_provider_reads_timeout_and_cap_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.providers import FireworksProvider

    monkeypatch.setenv("FIREWORKS_API_KEY", "fw-test")
    monkeypatch.setenv("FIREWORKS_READ_TIMEOUT_S", "60")
    monkeypatch.setenv("FIREWORKS_MAX_TOKENS", "2048")

    provider = FireworksProvider()

    assert provider.timeout.read == 60.0
    assert provider.timeout.write == 60.0
    assert provider.max_tokens == 2048


def test_fireworks_provider_defaults_when_env_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.providers import FireworksProvider

    monkeypatch.delenv("FIREWORKS_READ_TIMEOUT_S", raising=False)
    monkeypatch.delenv("FIREWORKS_MAX_TOKENS", raising=False)
    monkeypatch.delenv("FIREWORKS_REASONING_EFFORT", raising=False)

    provider = FireworksProvider()

    assert provider.timeout.read == 45.0
    # Chat-instruct default: no reasoning headroom required.
    assert provider.max_tokens == 1024
    assert provider.reasoning_effort == ""


@pytest.mark.asyncio
async def test_fireworks_provider_sends_reasoning_effort_when_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.providers import FireworksProvider

    monkeypatch.setenv("FIREWORKS_API_KEY", "fw-test")
    monkeypatch.setenv("FIREWORKS_MODEL_ID", "accounts/fireworks/models/muse-spark-open")
    monkeypatch.setenv("FIREWORKS_REASONING_EFFORT", "low")

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": json.dumps({"adjustments": []})},
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    provider = FireworksProvider()
    provider.transport = httpx.MockTransport(handler)
    await provider.complete(
        [{"role": "user", "content": "x"}],
        schema={"required": ["adjustments"]},
    )

    assert captured["body"]["reasoning_effort"] == "low"


@pytest.mark.asyncio
async def test_fireworks_provider_omits_reasoning_effort_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.providers import FireworksProvider

    monkeypatch.setenv("FIREWORKS_API_KEY", "fw-test")
    monkeypatch.delenv("FIREWORKS_REASONING_EFFORT", raising=False)

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": json.dumps({"adjustments": []})},
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    provider = FireworksProvider()
    provider.transport = httpx.MockTransport(handler)
    await provider.complete(
        [{"role": "user", "content": "x"}],
        schema={"required": ["adjustments"]},
    )

    assert "reasoning_effort" not in captured["body"]


@pytest.mark.asyncio
async def test_base_provider_reports_empty_content_from_reasoning_burn() -> None:
    """A reasoning model can spend the whole budget on thinking -> empty content."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": ""}, "finish_reason": "length"}
                ],
                "usage": {
                    "completion_tokens": 900,
                    "completion_tokens_details": {"reasoning_tokens": 897},
                },
            },
        )

    provider = _BaseProvider(
        base_url="https://example.invalid/v1",
        api_key="test-key",
        model_id="test-model",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ProviderError) as excinfo:
        await provider.complete([{"role": "user", "content": "x"}])

    assert excinfo.value.code == "E_EMPTY_CONTENT"
    assert "reasoning_tokens=897" in excinfo.value.message
    assert "FIREWORKS_REASONING_EFFORT" in excinfo.value.message


@pytest.mark.asyncio
async def test_base_provider_reports_empty_content_without_reasoning() -> None:
    """Llama 3.3 70B has no reasoning tokens: hint must point at max_tokens."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": ""}, "finish_reason": "length"}
                ],
                "usage": {"completion_tokens": 1024},
            },
        )

    provider = _BaseProvider(
        base_url="https://example.invalid/v1",
        api_key="test-key",
        model_id="test-model",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ProviderError) as excinfo:
        await provider.complete([{"role": "user", "content": "x"}])

    assert excinfo.value.code == "E_EMPTY_CONTENT"
    assert "truncated by max_tokens" in excinfo.value.message
    assert "FIREWORKS_MAX_TOKENS" in excinfo.value.message
    assert "reasoning_tokens=0" in excinfo.value.message



@pytest.mark.asyncio
async def test_fireworks_provider_caps_caller_max_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Corrector's max_tokens=900 must be capped by FIREWORKS_MAX_TOKENS."""
    from core.providers import FireworksProvider

    monkeypatch.setenv("FIREWORKS_API_KEY", "fw-test")
    monkeypatch.setenv("FIREWORKS_MODEL_ID", "accounts/fireworks/models/x")
    monkeypatch.setenv("FIREWORKS_MAX_TOKENS", "512")

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps({"adjustments": []})}}]
            },
        )

    provider = FireworksProvider()
    provider.transport = httpx.MockTransport(handler)

    await provider.complete(
        [{"role": "user", "content": "x"}],
        schema={"required": ["adjustments"]},
        max_tokens=900,
    )

    assert captured["body"]["max_tokens"] == 512


@pytest.mark.asyncio
async def test_fireworks_provider_uses_env_cap_when_caller_omits_max_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.providers import FireworksProvider

    monkeypatch.setenv("FIREWORKS_API_KEY", "fw-test")
    monkeypatch.setenv("FIREWORKS_MODEL_ID", "accounts/fireworks/models/x")
    monkeypatch.setenv("FIREWORKS_MAX_TOKENS", "2048")

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps({"adjustments": []})}}]
            },
        )

    provider = FireworksProvider()
    provider.transport = httpx.MockTransport(handler)

    await provider.complete(
        [{"role": "user", "content": "x"}],
        schema={"required": ["adjustments"]},
    )

    assert captured["body"]["max_tokens"] == 2048

