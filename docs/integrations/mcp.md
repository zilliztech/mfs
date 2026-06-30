# MCP server

Expose MFS over the [Model Context Protocol](https://modelcontextprotocol.io) and
any MCP client — Claude Code, Cursor, Codex, Windsurf — can search your indexed
sources as context. It's in the spirit of
[claude-context](https://github.com/zilliztech/claude-context) ("make the codebase
the context for any coding agent"), but the index is MFS, so one server covers
**every** source you've indexed — code, docs, issues, chat, databases — not just
one codebase.

The runnable server is in the
[example](https://github.com/zilliztech/mfs/tree/main/examples/mfs-mcp); it's about
60 lines over the [Python SDK](../sdks.md).

## Two tools

```python
from mcp.server.fastmcp import FastMCP
import mfs_sdk

mcp = FastMCP("mfs-context")


@mcp.tool()
def search(query: str, scope: str = "", top_k: int = 8) -> str:
    """Hybrid search across MFS-indexed sources. Empty scope = everything;
    or pass a path / URI prefix like "github://org/repo"."""
    resp = retrieval.search(q=query, path=scope or None, top_k=top_k)
    return "\n\n".join(f"## {h.source}\n{h.content.strip()}" for h in resp.results)


@mcp.tool()
def read(source: str, lines: str = "") -> str:
    """Read a hit in full, or a line range like "40:80", by its source URI."""
    return browse.cat(source, range=lines or None).content


if __name__ == "__main__":
    mcp.run()
```

`search` locates, `read` pulls the exact unit into context — the same loop MFS is
built around. (`retrieval` / `browse` are `mfs_sdk.RetrievalApi` / `BrowseApi`
pointed at your server; see the example for the few lines of setup.)

## Configure MFS (matches claude-context)

[claude-context](https://github.com/zilliztech/claude-context) ships with **OpenAI
`text-embedding-3-small`** embeddings and a **Zilliz Cloud** vector database; this
example defaults to the same. MFS reads the vector store from the environment and
the embedding model from its config, so launch the MFS server with:

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

The MCP server is a thin client — it only needs `MFS_URL` / `MFS_TOKEN` to reach
that server; the embedding and vector-store choice lives with MFS. (Drop these and
MFS runs fully local — ONNX embeddings + Milvus Lite, no keys.)

## Register it

With Claude Code, from your project:

```bash
claude mcp add mfs-context \
  --env MFS_URL=http://127.0.0.1:13619 \
  -- uv run --with mcp --with /abs/path/to/mfs/sdks/python python /abs/path/to/mfs/examples/mfs-mcp/server.py
```

`claude mcp list` should report `mfs-context: ✔ Connected`, after which the agent
calls the tools on its own — "where is rate limiting implemented? search our
sources." Any MCP client works; point its stdio server config at the same command.

## Restrict its reach

By default the server can search and read everything the MFS server has indexed.
Add `--env MFS_ALLOWED_SCOPES=github://org/repo,file://local/abs/path` (a
comma-separated list of URI / path prefixes) to bound it: `search` only returns
hits under those prefixes (an empty scope searches all of them, not the whole
index) and `read` refuses any source outside them. This is enforced by the server,
unlike the per-query `scope` argument the agent chooses.
