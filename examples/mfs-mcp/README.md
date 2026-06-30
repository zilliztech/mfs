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

## Configure MFS (matches claude-context)

[`claude-context`](https://github.com/zilliztech/claude-context) ships with
**OpenAI `text-embedding-3-small`** embeddings and a **Zilliz Cloud** vector
database. This example defaults to the same, so point your MFS server at that
model and managed store. MFS reads the vector store from the environment and the
embedding model from its config:

```bash
export OPENAI_API_KEY=sk-...                              # embeddings
export ZILLIZ_URI=https://<your-cluster>.zillizcloud.com  # vector store
export ZILLIZ_TOKEN=<your-zilliz-key>
```

```toml
# server.toml  (or run: mfs-server setup --section embedding)
[embedding]
provider = "openai"
model    = "text-embedding-3-small"
dim      = 1536
```

Then `mfs-server run` and index at least one source. The MCP server itself only
needs `MFS_URL` / `MFS_TOKEN` (defaulting to `http://127.0.0.1:13619` and
`~/.mfs/server.token`) to reach that server — the embedding and vector-store
choice lives with MFS, not here.

> Drop the OpenAI / Zilliz settings and MFS runs fully local instead — ONNX
> embeddings + Milvus Lite, no keys.

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

## Restrict its reach

By default the server can search and read everything the MFS server has indexed.
To bound it, set `MFS_ALLOWED_SCOPES` to a comma-separated list of URI / path
prefixes when you register it:

```bash
claude mcp add mfs-context \
  --env MFS_URL=http://127.0.0.1:13619 \
  --env MFS_ALLOWED_SCOPES=github://your-org/your-repo,file://local/abs/path \
  -- uv run --with mcp --with /abs/path/to/mfs/sdks/python python /abs/path/to/mfs/examples/mfs-mcp/server.py
```

`search` then only returns hits under those prefixes — an empty scope searches all
of them, not the whole index — and `read` refuses any source outside them. Unlike
the per-query `scope` argument (which the agent chooses), this is enforced by the
server, so the client can't reach past it.
