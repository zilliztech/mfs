"""OpenAI-compatible LLM provider (text + vision).

Targets any endpoint that implements the OpenAI Chat Completions protocol
behind a custom ``base_url`` / ``api_key`` / ``model`` — DeepSeek, Moonshot,
Qwen, vLLM, Ollama's ``/v1``, and similar. Unlike :mod:`openai` (OpenAI's
cloud, credentials read from ``OPENAI_API_KEY``), ``base_url`` is mandatory
here: there is no implicit cloud fallback. ``api_key`` is optional, so an
unauthenticated local endpoint (e.g. a plain vLLM/Ollama server) can be used
with a placeholder.

Credential indirection
----------------------
Both ``base_url`` and ``api_key`` accept an ``env:VAR`` / ``file:/path``
reference, matching the connector credential-ref convention. The resolver is
inlined below rather than imported from ``engine.components.connector_factory``
(``CredentialService.resolve``): ``common`` is a lower layer than ``engine``,
so importing back into it would close a ``common → engine → common`` cycle.
The duplication is tracked — see the TODO on ``_resolve_secret_ref``.
"""

from __future__ import annotations

import base64
import os

from openai import AsyncOpenAI
from openai.types.chat import (
    ChatCompletionContentPartImageParam,
    ChatCompletionContentPartTextParam,
    ChatCompletionUserMessageParam,
)
from openai.types.chat.chat_completion_content_part_image_param import ImageURL


def _resolve_secret_ref(v: str | None) -> str | None:
    """Resolve an ``env:VAR`` / ``file:/path`` credential reference to its value.

    Non-ref values pass through unchanged. ``secret:`` and ``vault:`` raise
    ``ValueError`` rather than fall through, so an unimplemented scheme cannot
    masquerade as a working reference and fail silently at auth time.

    TODO(secrets): duplicated from ``CredentialService.resolve`` in
    ``engine.components.connector_factory``. Consolidate by lifting that
    resolver into a ``common.secrets`` module (breaking the
    ``common → engine`` layering), then import it both here and from
    ``connector_factory``.
    """
    if not v:
        return None
    if v.startswith("env:"):
        name = v[4:].strip()
        if not name:
            raise ValueError("env: needs a variable name, e.g. env:MY_API_KEY")
        if name not in os.environ:
            raise ValueError(f"env var {name!r} is not set in the environment")
        return os.environ[name]
    if v.startswith("file:"):
        path = v[5:]
        try:
            with open(path, encoding="utf-8") as f:
                return f.read().strip()
        except OSError as e:
            raise ValueError(f"cannot read secret file {path!r}: {e}") from e
    if v.startswith(("secret:", "vault:")):
        raise ValueError(f"{v.split(':', 1)[0]!r} scheme is not implemented (use env: or file:)")
    return v


class OpenAICompatibleLlm:
    """LLM provider for OpenAI-compatible endpoints.

    ``base_url`` is required. ``model`` is not defaulted at construction;
    callers pass it per ``chat``/``vision`` call — the framework already does,
    reading ``[summary].model`` / ``[description].model``.
    """

    def __init__(self, *, base_url: str | None = None, api_key: str | None = None) -> None:
        resolved_base = _resolve_secret_ref(base_url)
        if not resolved_base:
            raise ValueError(
                "openai_compatible provider requires base_url (e.g. https://api.deepseek.com/v1)"
            )
        kwargs: dict = {"base_url": resolved_base}
        resolved_key = _resolve_secret_ref(api_key)
        if resolved_key:
            kwargs["api_key"] = resolved_key
        else:
            # Without an explicit api_key, AsyncOpenAI falls back to OPENAI_API_KEY
            # from the environment — which would silently point a custom endpoint
            # at OpenAI cloud credentials. Substitute a placeholder so a
            # misconfigured endpoint fails at the server rather than borrowing
            # unrelated cloud creds.
            kwargs["api_key"] = "mfs-openai-compatible-no-key"
        self._client = AsyncOpenAI(**kwargs)

    async def chat(
        self,
        prompt: str,
        *,
        model: str | None = None,
        max_tokens: int = 800,
        temperature: float = 0.3,
    ) -> str:
        if not model:
            raise ValueError(
                "openai_compatible provider requires a model "
                "(set [summary].model / [description].model)"
            )
        resp = await self._client.chat.completions.create(
            model=model,
            messages=[ChatCompletionUserMessageParam(role="user", content=prompt)],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return resp.choices[0].message.content or ""

    async def vision(
        self,
        prompt: str,
        image_bytes: bytes,
        mime: str,
        *,
        model: str | None = None,
        max_tokens: int = 800,
    ) -> str:
        if not model:
            raise ValueError(
                "openai_compatible provider requires a model "
                "(set [summary].model / [description].model)"
            )
        b64 = base64.b64encode(image_bytes).decode("ascii")
        image_url = ImageURL(url=f"data:{mime};base64,{b64}")
        resp = await self._client.chat.completions.create(
            model=model,
            messages=[
                ChatCompletionUserMessageParam(
                    role="user",
                    content=[
                        ChatCompletionContentPartTextParam(type="text", text=prompt),
                        ChatCompletionContentPartImageParam(type="image_url", image_url=image_url),
                    ],
                )
            ],
            max_tokens=max_tokens,
        )
        return resp.choices[0].message.content or ""
