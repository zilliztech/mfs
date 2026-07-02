#!/usr/bin/env python3
"""A replica of zilliztech/claude-context ("make the codebase the context for any
coding agent"), rebuilt on top of MFS instead of a single-codebase index. MFS's
`search` + `read` API is enough to reproduce it in about 60 lines — and because the
index is MFS, this single server covers every source you've indexed (code, docs,
issues, chat, databases), not just one codebase.

It exposes two tools over the Model Context Protocol:

- ``search`` — hybrid (semantic + keyword) search across MFS-indexed sources;
- ``read``   — read a hit in full (or a line range) by its source URI.

Point it at a running MFS server with ``MFS_URL`` / ``MFS_TOKEN`` (defaults to
``http://127.0.0.1:13619`` and ``~/.mfs/server.token``). Register it with any MCP
client, e.g. Claude Code:

    claude mcp add claude-context -- python /abs/path/to/server.py
"""

from __future__ import annotations

import os
from pathlib import Path

import mfs_sdk
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("claude-context")

# Optional access boundary: a comma-separated list of URI / path prefixes this
# server may search and read. Empty = unrestricted (the whole MFS index). Set it
# on the MCP registration, e.g.
#   --env MFS_ALLOWED_SCOPES=github://org/repo,file://local/abs/path
_ALLOWED = [s.strip() for s in os.getenv("MFS_ALLOWED_SCOPES", "").split(",") if s.strip()]


def _within(uri: str) -> bool:
    """True when `uri` is one of, or under, an allowed prefix (or no allowlist is set)."""
    if not _ALLOWED:
        return True
    return any(uri == p or uri.startswith(p.rstrip("/") + "/") for p in _ALLOWED)


def _token() -> str | None:
    if os.getenv("MFS_TOKEN"):
        return os.environ["MFS_TOKEN"]
    tok = Path.home() / ".mfs" / "server.token"
    return tok.read_text().strip() if tok.exists() else None


def _api(api_cls):
    client = mfs_sdk.ApiClient(
        mfs_sdk.Configuration(host=os.getenv("MFS_URL", "http://127.0.0.1:13619"))
    )
    token = _token()
    if token:
        client.set_default_header("Authorization", f"Bearer {token}")
    return api_cls(client)


@mcp.tool()
def search(query: str, scope: str = "", top_k: int = 8) -> str:
    """Search MFS-indexed sources (code, docs, issues, chat, databases) by meaning
    or keyword. Leave ``scope`` empty to search every allowed source, or pass a
    path / URI prefix to narrow it (e.g. ``github://org/repo`` or a local path).
    Returns ranked hits, each with a snippet and the ``source`` URI to pass to
    ``read``.
    """
    if scope and not _within(scope):
        return f"Refused: scope {scope!r} is outside the allowed scopes ({', '.join(_ALLOWED)})."
    targets = [scope] if scope else (list(_ALLOWED) or [None])

    api = _api(mfs_sdk.RetrievalApi)
    hits = []
    for target in targets:
        hits.extend(api.search(q=query, path=target, top_k=top_k).results)
    hits = [h for h in hits if _within(h.source)]  # belt-and-suspenders
    hits.sort(key=lambda h: h.score if h.score is not None else 0.0, reverse=True)
    if not hits:
        return "No matches."
    blocks = []
    for h in hits[:top_k]:
        score = f"{h.score:.2f}" if h.score is not None else "n/a"
        blocks.append(f"## {h.source}  (score={score})\n{h.content.strip()}")
    return "\n\n".join(blocks)


@mcp.tool()
def read(source: str, lines: str = "") -> str:
    """Read a source in full, or a line range like ``"40:80"``. ``source`` is the
    URI from a ``search`` hit. Use this to pull the exact code or text into context
    after ``search`` locates it."""
    if not _within(source):
        return f"Refused: {source!r} is outside the allowed scopes ({', '.join(_ALLOWED)})."
    resp = _api(mfs_sdk.BrowseApi).cat(source, range=lines or None)
    return resp.content


if __name__ == "__main__":
    mcp.run()
