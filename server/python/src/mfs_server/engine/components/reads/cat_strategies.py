"""CatRouter + CatStrategy: first-match dispatch over cat's branch matrix.

Each strategy `applies(ctx)` + `read(ctx) -> Any | None`: None means
"not applicable / try next", a non-None value is terminal. Density only applies
to the artifact-backed / plain-text paths, so it lives in those strategies via
_apply_density; the locator / structured / meta / image paths return before
density.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from ....common.converter import CONVERT_EXTS
from .text_views import _BARE_CAT_MAX_BYTES, _density_view, _locator_matches


@dataclass
class CatContext:
    path: str
    range: tuple[int, int] | None
    meta: bool
    density: str | None
    locator: dict | None
    cid: str
    curi: str
    rel: str
    plugin: object
    st: object
    okind: str
    structured: bool
    ext: str


class CatStrategy(Protocol):
    def applies(self, ctx: CatContext) -> bool: ...

    async def read(self, ctx: CatContext) -> Any | None: ...


def _apply_density(text: str, ctx: CatContext) -> str:
    """density view only over document/code; matches cat's tail check."""
    if ctx.density and ctx.density != "deep":
        if ctx.okind not in ("document", "code"):
            raise ValueError("density_unsupported")
        return _density_view(text, ctx.ext, ctx.density)
    return text


class MetaStrategy:
    def applies(self, ctx: CatContext) -> bool:
        return ctx.meta

    async def read(self, ctx: CatContext) -> Any | None:
        return {
            "source": ctx.curi + ctx.rel,
            "media_type": ctx.st.media_type,
            "size_hint": ctx.st.size_hint,
            "fingerprint": ctx.st.fingerprint,
        }


class LocatorLinesStrategy:
    """locator with reserved "lines" key (1-based half-open [s,e)) on a
    non-structured object -> slice by line range. plugin.read takes 0-based."""

    def applies(self, ctx: CatContext) -> bool:
        return (
            ctx.locator is not None
            and isinstance(ctx.locator, dict)
            and "lines" in ctx.locator
            and len(ctx.locator) == 1
            and not ctx.structured
        )

    async def read(self, ctx: CatContext) -> Any | None:
        from ....connectors.base import Range

        s, e = int(ctx.locator["lines"][0]), int(ctx.locator["lines"][1])
        rg = Range(max(0, s - 1), max(0, e - 1))
        buf = bytearray()
        async for ch in ctx.plugin.read(ctx.rel, rg):
            buf += ch
        return bytes(buf).decode("utf-8", errors="replace")


class LocatorRecordStrategy:
    """locator (PK dict) -> reopen a single structured record."""

    def applies(self, ctx: CatContext) -> bool:
        return ctx.locator is not None

    async def read(self, ctx: CatContext) -> Any | None:
        import json as _json
        from contextlib import aclosing

        records = ctx.plugin.read_records(ctx.rel)
        if records is None:
            raise ValueError("range_unsupported")  # not a structured object
        ocfg = ctx.plugin.ctx.object_config_for(ctx.rel)
        i = 0
        # aclosing: a match returns mid-iteration, so the record generator must be
        # closed deterministically - else a connector holding a cursor/transaction
        # (e.g. asyncpg) leaks the connection and pool.close() later blocks ~60s.
        async with aclosing(records):
            async for rec in records:
                if _locator_matches(rec, ocfg, i, ctx.locator):
                    return {
                        "source": ctx.curi + ctx.rel,
                        "locator": ctx.locator,
                        "content": _json.dumps(rec, default=str, ensure_ascii=False),
                    }
                i += 1
        raise ValueError("locator_not_found")


class StructuredStreamStrategy:
    """structured object, no locator -> JSONL stream (range=None) or paged slice."""

    def applies(self, ctx: CatContext) -> bool:
        return ctx.structured

    async def read(self, ctx: CatContext) -> Any | None:
        import json as _json
        from contextlib import aclosing

        from ....connectors.base import Range

        if ctx.range is None:
            # Bare cat of a structured object: stream records as JSONL up to
            # _BARE_CAT_MAX_BYTES; large ones raise object_too_large_for_cat so the
            # caller falls back to head / cat --range / export.
            records = ctx.plugin.read_records(ctx.rel)
            if records is None:
                raise ValueError("object_too_large_for_cat")
            budget = _BARE_CAT_MAX_BYTES
            out: list[str] = []
            size = 0
            async with aclosing(records):
                async for rec in records:
                    line = _json.dumps(rec, default=str, ensure_ascii=False)
                    size += len(line.encode("utf-8")) + 1  # +1 newline
                    if size > budget:
                        raise ValueError("object_too_large_for_cat")
                    out.append(line)
            return "\n".join(out)
        start, end = ctx.range[0], ctx.range[1]
        # Pass Range(0, end) - LIMIT-only - and slice [start, end) here. Connectors
        # disagree on honoring Range (DB push OFFSET+LIMIT, SaaS ignore it), yet all
        # declare paged_cat=True. offset=0 + single `i >= start` slice is correct for
        # both (see human_todo [dborder/D65] for restoring true offset pushdown).
        records = ctx.plugin.read_records(ctx.rel, Range(0, end))
        if records is not None:
            out, i = [], 0
            async with aclosing(records):  # break-early must close the generator
                async for rec in records:
                    if i >= end:
                        break
                    if i >= start:
                        out.append(_json.dumps(rec, default=str, ensure_ascii=False))
                    i += 1
            return "\n".join(out)
        return None


class ArtifactTextStrategy:
    """converted-markdown artifact: pdf/docx/html (CONVERT_EXTS) AND web/github
    pages whose .md is generated at ingest. On-read freshness via fingerprint.
    Returns None if no artifact (chain falls through to image / plain text)."""

    def __init__(self, artifacts, infra, ns):
        self._artifacts = artifacts
        self._infra = infra
        self._ns = ns

    def applies(self, ctx: CatContext) -> bool:
        return ctx.ext in CONVERT_EXTS or ctx.curi.startswith(("web://", "github://"))

    async def read(self, ctx: CatContext) -> Any | None:
        text: str | None = None
        if ctx.ext in CONVERT_EXTS:
            # stat() already fetched the live fingerprint; if it changed, the cached
            # markdown is stale -> re-convert from the current source.
            if await self._artifacts.converted_md_stale(ctx.cid, ctx.rel, ctx.st.fingerprint):
                raw = bytearray()
                async for ch in ctx.plugin.read(ctx.rel):
                    raw += ch
                text = await self._infra.converter.convert(bytes(raw), ctx.ext)
            else:
                art = await self._artifacts.read_artifact(
                    self._ns, ctx.curi + ctx.rel, "converted_md"
                )
                if art is not None:
                    text = art.decode("utf-8", errors="replace")
        else:  # web:// / github:// - generated markdown, read from artifact store
            art = await self._artifacts.read_artifact(self._ns, ctx.curi + ctx.rel, "converted_md")
            if art is not None:
                text = art.decode("utf-8", errors="replace")
        if text is None:
            return None  # no artifact -> fall through to image / plain text
        return _apply_density(text, ctx)


class ImageDescriptionStrategy:
    """image + description.enabled -> VLM describe. Returns None on provider
    failure so the chain falls through to the raw read (original try/except pass)."""

    def __init__(self, infra, cfg):
        self._infra = infra
        self._cfg = cfg

    def applies(self, ctx: CatContext) -> bool:
        return ctx.okind == "image" and self._cfg.description.enabled

    async def read(self, ctx: CatContext) -> Any | None:
        raw = bytearray()
        async for ch in ctx.plugin.read(ctx.rel):
            raw += ch
        try:
            return await self._infra.vlm.describe(bytes(raw), ctx.ext)
        except Exception:  # noqa: BLE001 - fall through to the raw read on provider error
            return None


class PlainTextStrategy:
    """fallback: raw text read (with the bare-cat size guard). Density applies."""

    def applies(self, ctx: CatContext) -> bool:
        return True

    async def read(self, ctx: CatContext) -> Any | None:
        from ....connectors.base import Range

        if ctx.range is None and ctx.st.size_hint and ctx.st.size_hint > _BARE_CAT_MAX_BYTES:
            raise ValueError("object_too_large_for_cat")
        rg = Range(ctx.range[0], ctx.range[1]) if ctx.range else None
        buf = bytearray()
        async for ch in ctx.plugin.read(ctx.rel, rg):
            buf += ch
        text = bytes(buf).decode("utf-8", errors="replace")
        return _apply_density(text, ctx)


class CatRouter:
    """Ordered first-match strategy table."""

    def __init__(self, strategies: list[CatStrategy]):
        self._strategies = strategies

    async def read(self, ctx: CatContext) -> Any:
        for s in self._strategies:
            if s.applies(ctx):
                result = await s.read(ctx)
                if result is not None:
                    return result
        raise ValueError("cat_unsupported")
