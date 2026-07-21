"""ReadService: search / ls / cat / head / tail / grep / export / resolve_connector_uri.

Extracted verbatim from the Engine god-class (engine-redesign §4.6 stage 3).
Method bodies are unchanged; only `self.<dep>` resolution targets moved (see
the dependency-rewire table in docs-dev/engine-redesign-read-upload.md §3.2).
The locator pair (open_path / match_connector) is a public method connecting
directly to ConnectorFactory + ObjectRepository - no reverse reference to
Engine (D1: direct connect + public method, not delayed-lambda).
"""

from __future__ import annotations

import asyncio
import logging
import os

from ....common.converter import CONVERT_EXTS
from ....common.retrieval import build_filter, collapse_results, to_envelope
from ....storage.milvus import MILVUS_MAX_RESULT_WINDOW
from ..connector_factory import ConnectorLocator
from .grep_strategies import (
    BM25Grep,
    GrepChain,
    GrepContext,
    LinearGrep,
    PushdownGrep,
)
from .text_views import (
    _BARE_CAT_MAX_BYTES,
    _HEAD_CACHE_N,
    _density_view,
    _locator_matches,
)

logger = logging.getLogger(__name__)


class ReadService:
    """Read path over registered connectors: search the vector index, and
    ls/cat/head/tail/grep/export object content. Holds no mutable state."""

    def __init__(self, cfg, infra, factory, objects, artifacts):
        self._cfg = cfg
        self._infra = infra
        self._factory = factory
        self._objects = objects
        self._artifacts = artifacts
        self._ns = cfg.namespace
        self._grep_chain = GrepChain(
            [PushdownGrep(), BM25Grep(self._infra.milvus, self._ns), LinearGrep(self._objects)]
        )

    # --- locators: direct connect to factory + objects, no Engine back-reference (D1) ---
    async def open_path(self, path: str):
        """(connector_id, connector_uri, relpath, plugin) for the registered connector
        whose root is the longest prefix of `path`. Thin delegate to
        ConnectorFactory.open_path."""
        rows = await self._objects.list_connectors_all()
        resolved = await self._factory.open_path(rows, path)
        return resolved.cid, resolved.connector_uri, resolved.relpath, resolved.built.plugin

    async def match_connector(self, path: str) -> tuple[dict, str] | None:
        """Find the registered connector whose root is the longest prefix of `path`;
        return (connector_row, relpath) or None. Thin delegate to
        ConnectorLocator.match; rows are fetched from ObjectRepository so the factory
        stays SQL-free."""
        rows = await self._objects.list_connectors_all()
        return ConnectorLocator.match(rows, path)

    # --- search ---
    async def _has_registered_search_scope(self, connector_uri: str | None) -> bool:
        """Return whether a search scope can match registered connector-owned chunks.

        Searching an empty namespace, or a path that resolves to an unregistered connector
        URI, cannot produce hits. Fast-pathing that case avoids cold-starting the query
        embedder for a guaranteed-empty result.
        """
        if connector_uri is None:
            return await self._objects.has_any_connector()
        return await self._objects.has_connector_uri(connector_uri)

    async def search(
        self,
        query: str,
        connector_uri: str | None = None,
        object_prefix: str | None = None,
        mode: str = "hybrid",
        top_k: int = 10,
        chunk_kinds: list[str] | None = None,
        collapse: bool = False,
    ) -> list[dict]:
        if top_k <= 0 or not query or not query.strip():
            return []  # nothing to ask for: skip the embed call and Milvus' limit>0 rule
        # Coarse fast-path: reject an absurd top_k before embedding/querying. Hybrid
        # over-fetches each sub-search by over_fetch_ratio, so the request Milvus sees is
        # top_k * ratio; other modes send top_k directly. This only catches values above the
        # hard window - the backend's real per-search cap is lower and backend-specific
        # (Milvus Lite tops out far below Zilliz), so MilvusStore translates the actual
        # MilvusException into the same `top_k_too_large` error as the authoritative guard.
        effective = top_k * self._cfg.search.over_fetch_ratio if mode == "hybrid" else top_k
        if effective > MILVUS_MAX_RESULT_WINDOW:
            raise ValueError("top_k_too_large")
        if not await self._has_registered_search_scope(connector_uri):
            return []
        expr = build_filter(self._ns, connector_uri, object_prefix, chunk_kinds)
        if mode == "keyword":
            hits = await asyncio.to_thread(
                self._infra.milvus.sparse_search, self._ns, query, top_k, expr
            )
        else:
            qvec = (await self._infra.embed.batch_embed([query]))[0]
            if mode == "semantic":
                hits = await asyncio.to_thread(
                    self._infra.milvus.search_dense, self._ns, qvec, top_k, expr
                )
            else:  # hybrid
                hits = await asyncio.to_thread(
                    self._infra.milvus.hybrid_search,
                    self._ns,
                    qvec,
                    query,
                    top_k,
                    expr,
                    None,
                    self._cfg.search.over_fetch_ratio,
                )
        envs = [to_envelope(h) for h in hits]
        return collapse_results(envs) if collapse else envs

    async def resolve_connector_uri(self, target: str) -> tuple[str, str | None]:
        """Map a user path/URI to (connector_uri, object_prefix) for search/grep scope.
        Matches the registered connector whose root is the longest prefix of `target`,
        so `search q /repo/src` (after `add /repo`) scopes to the /src subtree instead
        of fabricating a brand-new connector_uri that would match no indexed chunks."""
        match = await self.match_connector(target)
        if match is None:
            # not under any registered connector: fall back to literal resolution so an
            # exact connector root still scopes correctly (object_prefix unknown -> None).
            r = self._factory.resolve_target(target)
            return r.connector_uri, None
        row, rel = match
        connector_uri = row["root_uri"]
        # stored chunk object_uri == connector_uri + relpath, so prefix on the full URI
        object_prefix = (connector_uri + rel) if rel not in ("", "/") else None
        return connector_uri, object_prefix

    async def ls(self, path: str) -> dict:
        """List children, each enriched with its full path + index state from the
        objects table, plus the connector's capabilities."""
        cid, curi, rel, plugin = await self.open_path(path)
        try:
            entries = await plugin.list(rel)
            caps = plugin.CAPABILITIES.to_dict()
        finally:
            await plugin.close()
        out = []
        base = rel.rstrip("/")
        for e in entries:
            child_rel = f"{base}/{e.name}" if base else "/" + e.name
            row = await self._objects.get_object_search_status(cid, child_rel)
            out.append(
                {
                    "name": e.name,
                    "type": e.type,
                    "media_type": e.media_type,
                    "size_hint": e.size_hint,
                    "path": curi + child_rel,
                    "search_status": row["search_status"] if row else None,
                    "indexable": (
                        bool(row["indexable"]) if row and row["indexable"] is not None else None
                    ),
                    "real_id": e.extra.get("real_id"),
                }
            )
        return {"entries": out, "capabilities": caps}

    async def cat(
        self,
        path: str,
        range: tuple[int, int] | None = None,
        meta: bool = False,
        density: str | None = None,
        locator: dict | None = None,
    ):
        import json as _json
        from contextlib import aclosing

        from ....connectors.base import Range

        cid, curi, rel, plugin = await self.open_path(path)
        try:
            st = await plugin.stat(rel)
            if st.type == "dir":
                raise IsADirectoryError(path)
            if meta:
                return {
                    "source": curi + rel,
                    "media_type": st.media_type,
                    "size_hint": st.size_hint,
                    "fingerprint": st.fingerprint,
                }
            okind = plugin.object_kind_of(rel)
            structured = okind in ("table_rows", "record_collection", "message_stream")
            # Binary objects have no line-based view - reading them with --range
            # would return mojibake (UTF-8 errors="replace") under the guise of a
            # text slice. Refuse cleanly so the caller falls back to `export`.
            if range is not None and okind == "binary":
                raise ValueError("range_unsupported")

            # --- locator with reserved "lines" key: route to the range path ---
            # Body / code / document chunks store identity as {"lines":[s,e]};
            # reopening one means slicing the file by line range, not iterating
            # structured records. locator.lines is 1-based half-open (matches
            # how cat --range is exposed); plugin.read takes 0-based half-open.
            if (
                locator is not None
                and isinstance(locator, dict)
                and "lines" in locator
                and len(locator) == 1
                and not structured
            ):
                s, e = int(locator["lines"][0]), int(locator["lines"][1])
                rg = Range(max(0, s - 1), max(0, e - 1))
                buf = bytearray()
                async for ch in plugin.read(rel, rg):
                    buf += ch
                return bytes(buf).decode("utf-8", errors="replace")

            # --- locator: reopen a single structured record ---
            if locator is not None:
                records = plugin.read_records(rel)
                if records is None:
                    raise ValueError("range_unsupported")  # not a structured object
                ocfg = plugin.ctx.object_config_for(rel)
                i = 0
                # aclosing: a match returns mid-iteration, so the record generator must be
                # closed deterministically - else a connector holding a cursor/transaction
                # (e.g. asyncpg) leaks the connection and pool.close() later blocks ~60s.
                async with aclosing(records):
                    async for rec in records:
                        if _locator_matches(rec, ocfg, i, locator):
                            return {
                                "source": curi + rel,
                                "locator": locator,
                                "content": _json.dumps(rec, default=str, ensure_ascii=False),
                            }
                        i += 1
                raise ValueError("locator_not_found")

            # --- structured object: range pushdown over records (lazy, not materialized) ---
            if structured:
                if range is None:
                    # Bare cat of a structured object: stream the records as JSONL
                    # into a buffer up to _BARE_CAT_MAX_BYTES, then return. Small
                    # objects (Slack users.jsonl, small GitHub issue feeds, dozens-
                    # of-row tables) fit comfortably and round-trip as JSONL. Large
                    # ones (a postgres table with 1M rows) blow the budget mid-
                    # stream and raise the same object_too_large_for_cat so the
                    # caller still falls back to head / cat --range / export.
                    records = plugin.read_records(rel)
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
                start, end = range[0], range[1]
                # Pass Range(0, end) - a LIMIT-only hint - and slice [start, end) HERE, in one
                # place. Connectors disagree on whether they honor the Range: the DB ones
                # (mysql/postgres/mongo/bigquery) push OFFSET start + LIMIT down, while the SaaS
                # ones (jira/slack/notion/…) ignore it and return from row 0 - yet ALL declare
                # paged_cat=True, so the engine can't tell them apart. Pushing OFFSET start AND
                # then re-slicing `i >= start` double-applied the offset on the DB connectors, so
                # `cat --range 100:200` returned an empty/wrong page. With offset=0 every
                # connector returns rows from 0 and the single `i >= start` slice is correct for
                # both. (Trade-off: the DB connectors lose OFFSET pushdown and read `end` rows for
                # a deep page - still LIMIT-bounded; restoring true offset-pushdown needs an
                # explicit "range honored" capability - see human_todo [dborder/D65].)
                records = plugin.read_records(rel, Range(0, end))
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

            ext = os.path.splitext(rel)[1].lower()
            text: str | None = None
            # converted markdown artifact: pdf/docx/html (CONVERT_EXTS) AND web/github pages,
            # whose .md is generated at ingest - read it from the artifact store so cat works
            # across restarts / fresh plugin instances, not just in-memory.
            if ext in CONVERT_EXTS:
                # On-read freshness: stat() above already fetched the live source fingerprint,
                # so comparing it to the one recorded at ingest is free. If it changed, the
                # cached markdown is stale -> re-convert from the current source.
                if await self._artifacts.converted_md_stale(cid, rel, st.fingerprint):
                    raw = bytearray()
                    async for ch in plugin.read(rel):
                        raw += ch
                    text = await self._infra.converter.convert(bytes(raw), ext)
                else:
                    art = await self._artifacts.read_artifact(self._ns, curi + rel, "converted_md")
                    if art is not None:
                        text = art.decode("utf-8", errors="replace")
            elif curi.startswith(("web://", "github://")):
                art = await self._artifacts.read_artifact(self._ns, curi + rel, "converted_md")
                if art is not None:
                    text = art.decode("utf-8", errors="replace")
            if text is None and okind == "image" and self._cfg.description.enabled:
                # An image's description is a model output served through the transformation
                # cache: describe() returns the description memoized at ingest, or computes it
                # on a miss. (Re-reading the bytes here is the source round-trip - cheap for
                # local files; see TODO §10.9 for the snapshot path on remote connectors.)
                raw = bytearray()
                async for ch in plugin.read(rel):
                    raw += ch
                try:
                    return await self._infra.vlm.describe(bytes(raw), ext)
                except Exception:  # noqa: BLE001 - fall through to the raw read on provider error
                    pass
            if text is None:
                if range is None and st.size_hint and st.size_hint > _BARE_CAT_MAX_BYTES:
                    raise ValueError("object_too_large_for_cat")
                rg = Range(range[0], range[1]) if range else None
                buf = bytearray()
                async for ch in plugin.read(rel, rg):
                    buf += ch
                text = bytes(buf).decode("utf-8", errors="replace")
            if density and density != "deep":
                okind = plugin.object_kind_of(rel)
                if okind not in ("document", "code"):
                    raise ValueError("density_unsupported")
                return _density_view(text, ext, density)
            return text
        finally:
            await plugin.close()

    async def _read_full(self, path: str) -> tuple[str, bool]:
        """Whole object content for export / tail: returns (text, partial).
        partial=True when the connector capped the read (structured objects
        above max_read_rows). The bare-cat size guard is not applied. Backs
        export and tail; tail discards the partial flag (just wants the last
        N lines), export surfaces it."""
        import json as _json

        cid, curi, rel, plugin = await self.open_path(path)
        try:
            st = await plugin.stat(rel)
            if st.type == "dir":
                raise IsADirectoryError(path)
            okind = plugin.object_kind_of(rel)
            if okind in ("table_rows", "record_collection", "message_stream"):
                records = plugin.read_records(rel)
                if records is not None:
                    out = []
                    async for rec in records:
                        out.append(_json.dumps(rec, default=str, ensure_ascii=False))
                    text = "\n".join(out)
                    # ctx.declare_partial is the channel structured connectors use
                    # to flag "we capped at max_read_rows". Read it back here so
                    # export tells the truth instead of silently returning a slice.
                    partial = bool(getattr(plugin.ctx, "was_partial", lambda _r: False)(rel))
                    self._warn_if_huge_export(curi + rel, text)
                    return text, partial
            ext = os.path.splitext(rel)[1].lower()
            if ext in CONVERT_EXTS:
                # On-read freshness (same as cat): a changed source fingerprint means the
                # cached markdown is stale, so re-convert from the current source.
                if await self._artifacts.converted_md_stale(cid, rel, st.fingerprint):
                    raw = bytearray()
                    async for ch in plugin.read(rel):
                        raw += ch
                    text = await self._infra.converter.convert(bytes(raw), ext)
                    self._warn_if_huge_export(curi + rel, text)
                    return text, False
                art = await self._artifacts.read_artifact(self._ns, curi + rel, "converted_md")
                if art is not None:
                    text = art.decode("utf-8", errors="replace")
                    self._warn_if_huge_export(curi + rel, text)
                    return text, False
            elif curi.startswith(("web://", "github://")):
                art = await self._artifacts.read_artifact(self._ns, curi + rel, "converted_md")
                if art is not None:
                    text = art.decode("utf-8", errors="replace")
                    self._warn_if_huge_export(curi + rel, text)
                    return text, False
            if okind == "image" and self._cfg.description.enabled:
                raw = bytearray()
                async for ch in plugin.read(rel):
                    raw += ch
                try:
                    text = await self._infra.vlm.describe(bytes(raw), ext)
                    self._warn_if_huge_export(curi + rel, text)
                    return text, False
                except Exception:  # noqa: BLE001 - fall through to the raw read on provider error
                    pass
            buf = bytearray()
            async for ch in plugin.read(rel):
                buf += ch
            text = bytes(buf).decode("utf-8", errors="replace")
            self._warn_if_huge_export(curi + rel, text)
            return text, False
        finally:
            await plugin.close()

    def _warn_if_huge_export(self, uri: str, text: str) -> None:
        """Single-host export materializes the whole object in memory; warn on anything over
        100 MB so the operator sees the cost before the next OOM rather than after. A streaming
        export path is the proper fix but is deferred - objects this large are rare on the
        single-host deployment this guard covers, and the warning makes the cost explicit."""
        size = len(text.encode("utf-8", errors="ignore")) if text else 0
        if size > 100 * 1024 * 1024:
            logger.warning(
                "export %s materialized %d MB in memory (streaming export not yet implemented)",
                uri,
                size // (1024 * 1024),
            )

    async def export(self, path: str) -> tuple[str, bool]:
        """Full content for `mfs export`: returns (text, partial). Honest
        boundary - structured connectors with more rows than max_read_rows
        return partial=True; the caller (API layer) surfaces it in the
        CatResponse. The bare-cat size guard does not apply, but each
        connector's own row cap does (true streaming export is deferred)."""
        return await self._read_full(path)

    async def head(self, path: str, n: int = 20) -> str:
        cid, curi, rel, plugin = await self.open_path(path)
        try:
            okind = plugin.object_kind_of(rel)
            structured = okind in ("table_rows", "record_collection", "message_stream")
            if structured:
                # fast path: pre-cached first rows. The cache is capped at _HEAD_CACHE_N, so
                # it's authoritative ONLY when it holds the whole object (< the cap) OR n fits
                # within it; for a larger n on a capped cache, fall through to the live bounded
                # query below - otherwise `head -n 200` would silently return just the 100
                # cached rows instead of 200 (head must give min(n, total), not min(n, cache)).
                art = await self._artifacts.read_artifact(self._ns, curi + rel, "head_cache")
                if art is not None:
                    cached = art.decode("utf-8", errors="replace").splitlines()
                    if len(cached) < _HEAD_CACHE_N or n <= len(cached):
                        return "\n".join(cached[:n])
            else:
                ext = os.path.splitext(rel)[1].lower()
                # plain text / code / logs: stream just the first n lines so a large file
                # never materializes and never trips bare-cat's size guard - head is exactly
                # the escape hatch for big objects. Artifact-backed
                # objects (pdf/docx/html, web/github pages, images) have bounded cached text,
                # so they fall through to cat below.
                if not (
                    okind == "image"
                    or ext in CONVERT_EXTS
                    or curi.startswith(("web://", "github://"))
                ):
                    lines: list[str] = []
                    buf = b""
                    async for chunk in plugin.read(rel):
                        buf += chunk
                        while len(lines) < n:
                            nl = buf.find(b"\n")
                            if nl < 0:
                                break
                            lines.append(buf[:nl].decode("utf-8", errors="replace"))
                            buf = buf[nl + 1 :]
                        if len(lines) >= n:
                            break
                    if len(lines) < n and buf:
                        lines.append(buf.decode("utf-8", errors="replace"))
                    return "\n".join(lines[:n])
        finally:
            await plugin.close()
        if structured:
            return await self.cat(path, range=(0, n))  # bounded page, not the whole table
        text = await self.cat(path)  # artifact-backed text, bounded
        return "\n".join(text.splitlines()[:n])

    async def tail(self, path: str, n: int = 20) -> str:
        if n <= 0:
            return ""
        # plain-text real local file: read the last n lines straight off disk (native
        # accelerator / bounded reverse-read), so a huge log isn't fully materialized.
        # Artifact-backed (pdf/docx/html, web/github) and structured objects fall back.
        from ....common import accel

        cid, curi, rel, plugin = await self.open_path(path)
        try:
            okind = plugin.object_kind_of(rel)
            if okind in ("table_rows", "record_collection", "message_stream"):
                raise ValueError("tail_unsupported")
            ext = os.path.splitext(rel)[1].lower()
            plain_local = (
                curi.startswith("file://local")
                and okind
                not in (
                    "image",
                    "table_rows",
                    "record_collection",
                    "message_stream",
                    "table_schema",
                )
                and ext not in CONVERT_EXTS
            )
            abs_file = (curi.replace("file://local", "", 1) + rel) if plain_local else None
        finally:
            await plugin.close()
        if abs_file and os.path.isfile(abs_file):
            return "\n".join(await asyncio.to_thread(accel.tail_lines, abs_file, n))
        text, _partial = await self._read_full(
            path
        )  # artifact-backed / structured / non-local; tail ignores the partial flag
        return "\n".join(text.splitlines()[-n:])

    async def grep(
        self, pattern: str, path: str, top_k: int = 100, regex: bool = False
    ) -> list[dict]:
        """Dispatch: pushdown (file: none) -> BM25 (indexed scope) -> linear scan
        (not_indexed objects in scope), via the GrepStrategy chain (stage 6)."""
        cid, curi, rel, plugin = await self.open_path(path)
        scope_prefix = (curi + rel) if rel != "/" else None
        try:
            # open_path only resolves which connector owns the prefix, not whether
            # `rel` exists under it -- unlike ls/cat, nothing downstream fails loudly
            # for a missing path (pushdown/BM25/linear-scan all just yield zero
            # matches). Stat it explicitly so a bad path 404s like ls/cat instead of
            # looking like a real, empty search.
            await plugin.stat(rel)
            ocfg = plugin.ctx.object_config_for(rel)
            ctx = GrepContext(
                pattern, path, top_k, regex, cid, curi, rel, plugin, scope_prefix, ocfg
            )
            return await self._grep_chain.run(ctx)
        finally:
            await plugin.close()
