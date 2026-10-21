"""GLM (Zhipu) vision adapter tests.

The proxy/relabel tests are pure and always run. The client-wiring tests import
``content_trust`` and are skipped automatically when the engine package is not
installed.

No test performs a network call: a live GLM request needs a real key, which the
repository must never contain.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.glm_vision import _ClientProxy
from app.services.trust_engine_runner import _relabel_vision_provider

# -- request shaping (pure) --------------------------------------------------


class _FakeCompletions:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return "ok"


class _FakeClient:
    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=_FakeCompletions())


def test_proxy_strips_dashscope_only_params_and_clamps_max_tokens():
    """`reasoning_effort` is Qwen-only and must never reach GLM; 8192 is capped."""
    inner = _FakeClient()
    proxy = _ClientProxy(inner, drop={"reasoning_effort"}, max_tokens=4096)

    proxy.chat.completions.create(
        model="glm-4.6v-flash", max_tokens=8192, reasoning_effort="none", temperature=0
    )

    sent = inner.chat.completions.calls[0]
    assert "reasoning_effort" not in sent
    assert sent["max_tokens"] == 4096
    assert sent["model"] == "glm-4.6v-flash"
    assert sent["temperature"] == 0


def test_proxy_keeps_a_smaller_requested_max_tokens():
    inner = _FakeClient()
    proxy = _ClientProxy(inner, drop={"reasoning_effort"}, max_tokens=4096)

    proxy.chat.completions.create(max_tokens=512)
    assert inner.chat.completions.calls[0]["max_tokens"] == 512


# -- trace relabelling (pure) ------------------------------------------------


def test_relabel_corrects_hardcoded_qwen_label():
    payload = {
        "role_runs": [
            {
                "role": "image",
                "provider": "qwen_vision",
                "model": "qwen3.8-flash",
                "prompt_version": "qwen_v1",
            },
            {"role": "text", "provider": "deepseek", "model": "deepseek-flash", "prompt_version": "v10"},
        ],
        "model_metadata": {
            "role_provider_map": {
                "text": "deepseek",
                "comment": "deepseek",
                "account": "deepseek",
                "image": "qwen_vision",
            }
        },
    }
    provider = SimpleNamespace(
        provider_name="glm_vision", model_name="glm-4.6v-flash", prompt_version="glm_v1"
    )

    _relabel_vision_provider(payload, provider)

    image_role = payload["role_runs"][0]
    assert image_role["provider"] == "glm_vision"
    assert image_role["model"] == "glm-4.6v-flash"
    assert image_role["prompt_version"] == "glm_v1"
    # The text role is untouched.
    assert payload["role_runs"][1]["provider"] == "deepseek"
    assert payload["model_metadata"]["role_provider_map"]["image"] == "glm_vision"
    assert payload["model_metadata"]["vision_provider"] == "glm_vision"
    assert payload["model_metadata"]["vision_model"] == "glm-4.6v-flash"


def test_relabel_is_a_noop_without_a_named_provider():
    payload = {"role_runs": [{"role": "image", "provider": "qwen_vision"}], "model_metadata": {}}
    _relabel_vision_provider(payload, SimpleNamespace())
    assert payload["role_runs"][0]["provider"] == "qwen_vision"


# -- wiring (needs the engine package) ---------------------------------------


def test_runner_injects_glm_as_the_vision_provider():
    pytest.importorskip("content_trust")

    from app.core.config import Settings
    from app.services.trust_engine_runner import TrustEngineRunner

    settings = Settings(glm_api_key="dummy-key", glm_model="glm-5.3-flashx")
    runner = TrustEngineRunner.from_settings(settings)

    assert runner.available is True
    assert runner.vision_provider is not None
    assert runner.vision_provider.provider_name == "glm_vision"
    # Assert against the configured value, not a hardcoded model name — the model
    # is a deployment choice and has changed once already.
    assert runner.vision_provider.model_name == settings.glm_model


def test_runner_falls_back_to_the_engine_vision_when_no_glm_key():
    pytest.importorskip("content_trust")

    from app.core.config import Settings
    from app.services.trust_engine_runner import TrustEngineRunner

    runner = TrustEngineRunner.from_settings(Settings())

    assert runner.available is True
    assert runner.vision_provider is None


def test_glm_client_reads_glm_settings_and_keeps_mime_aware_data_uri():
    pytest.importorskip("content_trust")

    from app.core.config import Settings
    from app.services.glm_vision import GLMVisionClient

    client = GLMVisionClient(Settings(glm_api_key="dummy-key", glm_model="glm-4.6v-flash"))

    assert client.provider_name == "glm_vision"
    assert client.model_name == "glm-4.6v-flash"
    assert client.prompt_version == "glm_v1"
    # Keep the MIME type so WebP inputs are unambiguous to the provider.
    assert (
        client._image_data_url(None, "data:image/webp;base64,QUJD")
        == "data:image/webp;base64,QUJD"
    )
    # Remote URLs pass through untouched.
    assert (
        client._image_data_url(None, "https://example.test/a.png")
        == "https://example.test/a.png"
    )


def test_glm_client_can_opt_into_raw_base64_for_legacy_endpoints():
    pytest.importorskip("content_trust")

    from app.core.config import Settings
    from app.services.glm_vision import GLMVisionClient

    client = GLMVisionClient(
        Settings(glm_api_key="dummy-key", glm_image_url_format="raw")
    )
    assert client._image_data_url(None, "data:image/png;base64,QUJD") == "QUJD"


def test_build_glm_vision_returns_none_without_a_key():
    pytest.importorskip("content_trust")

    from app.core.config import Settings
    from app.services.glm_vision import build_glm_vision

    assert build_glm_vision(Settings()) is None
