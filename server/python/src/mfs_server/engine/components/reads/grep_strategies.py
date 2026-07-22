"""GrepStrategy chain: Pushdown -> BM25 -> Linear (terminal on first hit).

Each strategy's `match(ctx)` returns either a final result list
(terminal - chain stops) or None (continue to next strategy). Pushdown is
terminal on hit; BM25 appends and continues; Linear appends and is terminal.
"""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass, field
from typing import Protocol

from ....common.retrieval import build_filter
from .text_views import _GREP_LINEAR_SCAN_MAX


@dataclass
class GrepContext:
    """Shared state for a single grep invocation across the strategy chain."""

    pattern: str
    path: str
    top_k: int
    regex: bool
    cid: str
    curi: str
    rel: str
    plugin: object
    scope_prefix: str | None
    ocfg: object
    results: list[dict] = field(default_factory=list)


class GrepStrategy(Protocol):
    """A grep dispatch step. Return a list to terminate the chain, None to continue."""

    async def match(self, ctx: GrepContext) -> list[dict] | None: ...


class PushdownGrep:
    """2a connector grep pushdown: exact, source-side (e.g. SQL ILIKE for
    structured connectors). Returns None when unsupported. Terminal on hit."""

    async def match(self, ctx: GrepContext) -> list[dict] | None:
        from ....connectors.base import GrepOptions

        try:
            gen = await ctx.plugin.grep(
                ctx.pattern,
                ctx.rel,
                GrepOptions(
                    pattern=ctx.pattern,
                    text_fields=ctx.ocfg.text_fields,
                    metadata_fields=ctx.ocfg.metadata_fields,
                ),
            )
        except Exception:  # noqa: BLE001 - pushdown failure shouldn't kill grep
            gen = None
        if gen is None:
            return None
        async for gm in gen:
            # Structured pushdown carries gm.locator (PK dict); text/code pushdown
            # carries gm.line_no. locator.lines is 1-based half-open [s,e), so a
            # single line n is [n, n+1] - not [n, n], which would round-trip as an
            # empty slice.
            loc = (
                gm.locator
                if gm.locator is not None
                else ({"lines": [gm.line_no, gm.line_no + 1]} if gm.line_no else None)
            )
            ctx.results.append(
                {
                    "source": ctx.curi + gm.path,
                    "locator": loc,
                    "content": gm.content,
                    "via": "pushdown",
                }
            )
        return ctx.results


class BM25Grep:
    """2b BM25 over indexed objects in scope. Appends and continues (not terminal)."""

    def __init__(self, milvus, ns):
        self._milvus = milvus
        self._ns = ns

    async def match(self, ctx: GrepContext) -> list[dict] | None:
        expr = build_filter(self._ns, ctx.curi, ctx.scope_prefix)
        hits = await asyncio.to_thread(
            self._milvus.sparse_search, self._ns, ctx.pattern, ctx.top_k, expr
        )
        for h in hits:
            e = h.get("entity", h)
            ctx.results.append(
                {
                    "source": e.get("object_uri"),
                    "locator": e.get("locator"),
                    "content": e.get("content"),
                    "via": "bm25",
                }
            )
        return None


class LinearGrep:
    """2c linear scan over not_indexed objects in scope (file connector). The
    linear scan uses the native accelerator (mfs_server_rs) when the object is a
    real local file, else falls back to reading bytes + pure-Python regex.
    Terminal (always returns the accumulated results)."""

    def __init__(self, objects):
        self._objects = objects

    async def match(self, ctx: GrepContext) -> list[dict] | None:
        from ....common import accel

        root_abs = (
            ctx.curi.replace("file://local", "", 1) if ctx.curi.startswith("file://local") else None
        )
        # Path-component boundary, same fix as build_filter: scope `/src` must match the
        # object itself OR `/src/...`, NOT a sibling `/src-old`. Escape SQL LIKE wildcards
        # ('_'/'%') in the literal prefix so a path with '_' doesn't over-match either.
        not_idx = await self._objects.list_not_indexed_in_scope(ctx.cid, ctx.rel)
        if len(not_idx) > _GREP_LINEAR_SCAN_MAX:
            # don't silently scan a subset and imply it was exhaustive - tell the agent
            # so it can narrow the path or index first.
            ctx.results.append(
                {
                    "source": None,
                    "locator": None,
                    "via": "notice",
                    "content": f"(grep linear scan capped at {_GREP_LINEAR_SCAN_MAX} of "
                    f"{len(not_idx)} not-indexed files in scope; narrow the path "
                    f"or run `mfs add` to index them for complete results)",
                }
            )
        for o in not_idx[:_GREP_LINEAR_SCAN_MAX]:
            relp = o["object_uri"]
            try:
                abs_file = (root_abs + relp) if root_abs else None
                if abs_file and os.path.isfile(abs_file):
                    # native (or pure-Python) streaming grep straight off disk
                    for ln, line in await asyncio.to_thread(
                        accel.linear_grep_file, abs_file, ctx.pattern, False, ctx.regex, 200
                    ):
                        ctx.results.append(
                            {
                                "source": ctx.curi + relp,
                                "locator": {"lines": [ln, ln + 1]},
                                "content": line,
                                "via": "linear",
                            }
                        )
                else:
                    rx = re.compile(ctx.pattern if ctx.regex else re.escape(ctx.pattern))
                    buf = bytearray()
                    async for ch in ctx.plugin.read(relp):
                        buf += ch
                    text = bytes(buf).decode("utf-8", errors="replace")
                    for i, line in enumerate(text.splitlines(), 1):
                        if rx.search(line):
                            ctx.results.append(
                                {
                                    "source": ctx.curi + relp,
                                    "locator": {"lines": [i, i + 1]},
                                    "content": line,
                                    "via": "linear",
                                }
                            )
            except Exception:  # noqa: BLE001
                pass
        return ctx.results


class GrepChain:
    """Ordered strategy chain: first terminal result wins."""

    def __init__(self, strategies: list[GrepStrategy]):
        self._strategies = strategies

    async def run(self, ctx: GrepContext) -> list[dict]:
        for s in self._strategies:
            result = await s.match(ctx)
            if result is not None:
                return result
        return ctx.results
