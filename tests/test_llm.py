"""Tests for the LLM/VLM provider package.

These tests don't make any real network calls — providers are exercised via
mock objects to verify protocol structural typing, the lazy-import factory,
and the OpenAI vision-model guardrail.
"""

from __future__ import annotations

import sys
import types

import pytest

from mfs.llm import DEFAULT_MODELS, LLMProvider, VLMCapable, get_provider


# ---------------------------------------------------------------------------
# Protocol structural typing
# ---------------------------------------------------------------------------


class _StubLLM:
    def __init__(self, model: str = "stub-model"):
        self._model = model

    @property
    def model_name(self) -> str:
        return self._model

    def generate(self, prompt: str, *, system: str | None = None) -> str:
        return f"echo:{prompt}"


class _StubVLM(_StubLLM):
    def describe_image(self, image_path: str, *, prompt: str | None = None) -> str:
        return f"image:{image_path}"


def test_stub_llm_satisfies_llm_provider_protocol():
    assert isinstance(_StubLLM(), LLMProvider)


def test_stub_vlm_satisfies_both_protocols():
    vlm = _StubVLM()
    assert isinstance(vlm, LLMProvider)
    assert isinstance(vlm, VLMCapable)


def test_text_only_llm_does_not_satisfy_vlm_capable():
    assert not isinstance(_StubLLM(), VLMCapable)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def test_get_provider_unknown_name():
    with pytest.raises(ValueError, match="Unknown LLM provider"):
        get_provider("does-not-exist")


def test_get_provider_install_hint(monkeypatch):
    """Force an ImportError when loading an optional provider and assert the
    hint mentions the right extra name."""
    real_import_module = __import__("importlib").import_module

    def fake_import_module(name: str):
        if name == "mfs.llm.anthropic":
            raise ImportError("No module named anthropic")
        return real_import_module(name)

    monkeypatch.setattr("importlib.import_module", fake_import_module)
    with pytest.raises(ImportError, match=r"mfs-cli\[llm-anthropic\]"):
        get_provider("anthropic")


def test_get_provider_openai_passes_kwargs(monkeypatch):
    """The factory should construct OpenAILLM with the supplied model/api_key."""
    constructed: dict = {}

    class _FakeOpenAILLM:
        def __init__(self, *, model="default", api_key=None, base_url=None):
            constructed["model"] = model
            constructed["api_key"] = api_key
            constructed["base_url"] = base_url
            self._model = model

        @property
        def model_name(self) -> str:
            return self._model

        def generate(self, prompt: str, *, system: str | None = None) -> str:
            return ""

    fake_module = types.ModuleType("mfs.llm.openai")
    fake_module.OpenAILLM = _FakeOpenAILLM
    monkeypatch.setitem(sys.modules, "mfs.llm.openai", fake_module)

    inst = get_provider("openai", model="gpt-test", api_key="sk-x", base_url="http://x")
    assert isinstance(inst, _FakeOpenAILLM)
    assert constructed == {"model": "gpt-test", "api_key": "sk-x", "base_url": "http://x"}


def test_get_provider_uses_default_model_when_omitted(monkeypatch):
    constructed: dict = {}

    class _FakeOpenAILLM:
        def __init__(self, *, model="default", api_key=None, base_url=None):
            constructed["model"] = model
            self._model = model

        @property
        def model_name(self) -> str:
            return self._model

        def generate(self, prompt: str, *, system: str | None = None) -> str:
            return ""

    fake_module = types.ModuleType("mfs.llm.openai")
    fake_module.OpenAILLM = _FakeOpenAILLM
    monkeypatch.setitem(sys.modules, "mfs.llm.openai", fake_module)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")

    get_provider("openai", api_key="sk-x")
    assert constructed["model"] == DEFAULT_MODELS["openai"]


# ---------------------------------------------------------------------------
# OpenAI vision guardrail
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_openai(monkeypatch):
    """Inject a fake `openai` module so OpenAILLM constructs without network."""
    captured: dict = {}

    class _FakeChat:
        def __init__(self):
            self.completions = self

        def create(self, *, model, messages, **kw):
            captured["model"] = model
            captured["messages"] = messages
            captured.update(kw)
            return types.SimpleNamespace(
                choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="ok"))]
            )

    class _FakeOpenAI:
        def __init__(self, **kw):
            captured["client_kwargs"] = kw
            self.chat = _FakeChat()

    fake_mod = types.ModuleType("openai")
    fake_mod.OpenAI = _FakeOpenAI
    monkeypatch.setitem(sys.modules, "openai", fake_mod)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    return captured


def test_openai_llm_rejects_describe_for_non_vision_model(fake_openai):
    from mfs.llm.openai import OpenAILLM

    llm = OpenAILLM(model="gpt-3.5-turbo", api_key="sk-x")
    with pytest.raises(ValueError, match="does not support vision"):
        llm.describe_image("/tmp/missing.png")


def test_openai_llm_describe_image_for_vision_model(fake_openai, tmp_path):
    from mfs.llm.openai import OpenAILLM

    img = tmp_path / "tiny.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")  # not a valid PNG, but we don't decode
    llm = OpenAILLM(model="gpt-4o-mini", api_key="sk-x")
    out = llm.describe_image(str(img), prompt="What is this?")
    assert out == "ok"
    sent_messages = fake_openai["messages"]
    assert sent_messages[0]["role"] == "user"
    parts = sent_messages[0]["content"]
    assert parts[0]["type"] == "text"
    assert parts[0]["text"] == "What is this?"
    assert parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_openai_llm_generate(fake_openai):
    from mfs.llm.openai import OpenAILLM

    llm = OpenAILLM(model="gpt-4o-mini", api_key="sk-x")
    assert llm.generate("hi", system="be terse") == "ok"
    msgs = fake_openai["messages"]
    assert msgs[0] == {"role": "system", "content": "be terse"}
    assert msgs[1] == {"role": "user", "content": "hi"}


def test_openai_llm_requires_api_key(monkeypatch):
    fake_mod = types.ModuleType("openai")
    fake_mod.OpenAI = lambda **kw: None
    monkeypatch.setitem(sys.modules, "openai", fake_mod)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    from mfs.llm.openai import OpenAILLM

    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        OpenAILLM(model="gpt-4o-mini")


# ---------------------------------------------------------------------------
# utils
# ---------------------------------------------------------------------------


def test_encode_image_data_url_round_trip(tmp_path):
    from mfs.llm.utils import encode_image_data_url

    img = tmp_path / "x.jpg"
    img.write_bytes(b"hello-bytes")
    url = encode_image_data_url(img)
    assert url.startswith("data:image/jpeg;base64,")


def test_encode_image_data_url_unknown_extension_falls_back_to_png(tmp_path):
    from mfs.llm.utils import encode_image_data_url

    blob = tmp_path / "weird.xyz"
    blob.write_bytes(b"\x00\x01\x02")
    url = encode_image_data_url(blob)
    assert url.startswith("data:image/png;base64,")
