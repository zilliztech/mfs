#!/usr/bin/env python3
"""An MCP server that turns MFS into searchable context for any MCP client.

In the spirit of zilliztech/claude-context — "make the codebase the context for
any coding agent" — but the index is MFS, so a single server covers every source
you've indexed (code, docs, issues, chat, databases), not just one codebase.

It exposes two tools over the Model Context Protocol:

- ``search`` — hybrid (semantic + keyword) search across MFS-indexed sources;
- ``read``   — read a hit in full (or a line range) by its source URI.

Point it at a running MFS server with ``MFS_URL`` / ``MFS_TOKEN`` (defaults to
``http://127.0.0.1:13619`` and ``~/.mfs/server.token``). Register it with any MCP
client, e.g. Claude Code:

    claude mcp add mfs-context -- python /abs/path/to/server.py
"""

from __future__ import annotations

import os
from pathlib import Path

import mfs_sdk
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("mfs-context")


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
    or keyword. Leave ``scope`` empty to search everything, or pass a path / URI
    prefix to narrow it (e.g. ``github://org/repo`` or a local path). Returns
    ranked hits, each with a snippet and the ``source`` URI to pass to ``read``.
    """
    resp = _api(mfs_sdk.RetrievalApi).search(q=query, path=scope or None, top_k=top_k)
    if not resp.results:
        return "No matches."
    blocks = []
    for h in resp.results:
        score = f"{h.score:.2f}" if h.score is not None else "n/a"
        blocks.append(f"## {h.source}  (score={score})\n{h.content.strip()}")
    return "\n\n".join(blocks)


@mcp.tool()
def read(source: str, lines: str = "") -> str:
    """Read a source in full, or a line range like ``"40:80"``. ``source`` is the
    URI from a ``search`` hit. Use this to pull the exact code or text into context
    after ``search`` locates it."""
    resp = _api(mfs_sdk.BrowseApi).cat(source, range=lines or None)
    return resp.content


if __name__ == "__main__":
    mcp.run()
