# MFS MCP server

A tiny [Model Context Protocol](https://modelcontextprotocol.io) server that turns
MFS into searchable context for any MCP client — Claude Code, Cursor, Codex,
Windsurf, and the rest.

It's in the spirit of [`claude-context`](https://github.com/zilliztech/claude-context)
("make the codebase the context for any coding agent"), but the index is MFS, so a
single server covers **every source you've indexed** — code, docs, issues, chat,
databases — not just one codebase.

## Tools

[`server.py`](server.py) exposes two tools over MCP:

- **`search(query, scope="", top_k=8)`** — hybrid (semantic + keyword) search
  across MFS-indexed sources. Leave `scope` empty to search everything, or pass a
  path / URI prefix (e.g. `github://org/repo`) to narrow it. Returns ranked hits
  with a snippet and the `source` URI.
- **`read(source, lines="")`** — read a hit in full, or a line range like `"40:80"`.

The loop an agent runs is the same one MFS is built around: `search` to locate,
`read` to pull the exact unit into context.

## Prerequisites

A running MFS server with at least one indexed source (see the
[docs](https://github.com/zilliztech/mfs/tree/main/docs) — a local repo is the
quickest start). The server reads `MFS_URL` / `MFS_TOKEN` (defaulting to
`http://127.0.0.1:13619` and `~/.mfs/server.token`).

## Register it

With Claude Code, from your project:

```bash
claude mcp add mfs-context \
  --env MFS_URL=http://127.0.0.1:13619 \
  -- uv run --with mcp --with /abs/path/to/mfs/sdks/python python /abs/path/to/mfs/examples/mfs-mcp/server.py
```

`claude mcp list` should report `mfs-context: ✔ Connected`. Then just ask — the
agent calls `search` / `read` on its own:

> Where is rate limiting implemented? Search our indexed sources.

Any MCP client works; point its stdio server config at the same command. The
server needs the [`mcp`](https://pypi.org/project/mcp/) package and the MFS Python
SDK (`mfs_sdk`, under [`sdks/python`](../../sdks/python)) on its path — the
`uv run --with …` invocation above pulls both in.
