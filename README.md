# MFS

**Semantic file search CLI powered by Milvus.** An `ls` / `tree` / `cat` / `grep` /
`search` toolchain that understands your files — built for humans at the terminal
and for AI agents driving a shell.

MFS indexes local files into a Milvus collection and exposes familiar POSIX-style
commands over that index. It combines dense-vector and BM25 retrieval with RRF
fusion, and layers a progressive browsing model (`peek` / `skim` / `deep`) on top
so an agent can navigate a repository from a bird's-eye view down to exact lines
without burning its context window.

---

## Why MFS

- **Hybrid retrieval, not just embeddings.** Dense vectors for meaning, BM25 for
  exact tokens, RRF for fusion. Short and long queries both work.
- **Progressive browsing.** `mfs ls` / `tree` / `cat` expose three density
  presets (`--peek` / `--skim` / `--deep`) controlled by width / height / depth
  knobs — the same mental model for every file type.
- **Multi-format indexing.** Markdown, source code (AST-split across 15 languages
  via tree-sitter), and PDF (via pymupdf4llm) are indexed out of the box. JSON,
  CSV, HTML and friends are always grep-able.
- **Smart grep routing.** Indexed files go through Milvus BM25; everything else
  falls back to the system `grep` — you don't have to think about which is which.
- **Optional LLM / VLM enrichment.** Opt-in document summaries and image
  descriptions, injected into the index to sharpen recall on vague queries.
- **Zero intrusion.** All state lives in `~/.mfs/`. Your project directory gets
  nothing added to it.
- **No LLM in the hot path.** Chunking, summarization heuristics, and embedding
  run without calling any LLM. LLM/VLM enrichment is strictly opt-in.

---

## Install

MFS is managed with [`uv`](https://docs.astral.sh/uv/) and `pyproject.toml`.

```bash
git clone https://github.com/zilliztech/mfs.git
cd mfs
uv sync                   # base install (OpenAI embedding + LLM ready)
uv run mfs --help
```

### Optional extras

Install only what you need:

```bash
# Embedding providers
uv sync --extra onnx              # local bge-m3 ONNX INT8, no API key
uv sync --extra google            # Google Gemini embeddings
uv sync --extra voyage            # Voyage AI
uv sync --extra jina              # Jina
uv sync --extra ollama            # local Ollama models
uv sync --extra mistral           # Mistral
uv sync --extra local             # sentence-transformers (GPU)

# LLM / VLM providers (for --summarize / --describe)
uv sync --extra llm-anthropic     # Claude
uv sync --extra llm-google        # Gemini
uv sync --extra llm-ollama        # local Ollama
uv sync --extra llm-mistral       # Mistral

# Everything at once
uv sync --extra all
```

### Environment variables

```bash
export OPENAI_API_KEY="sk-..."       # default provider for embedding and LLM
# Optional, only if you use them:
export GOOGLE_API_KEY="..."          # or GEMINI_API_KEY
export ANTHROPIC_API_KEY="..."
export VOYAGE_API_KEY="..."
export JINA_API_KEY="..."
export MISTRAL_API_KEY="..."
```

---

## Quick start

```bash
# 1. Index the current directory
mfs add .

# 2. Semantic search
mfs search "how do we handle token expiration"

# 3. Exact search
mfs grep "ERR_TOKEN_EXPIRED"

# 4. Browse a folder with summaries
mfs ls ./docs/

# 5. Check indexing progress
mfs status
```

---

## Core commands

### `mfs add <path...>` — index files and directories

```bash
mfs add .                                   # index cwd
mfs add ./docs/ ./src/                      # multiple paths
mfs add ./report.pdf                        # PDF is converted to Markdown first
mfs add . --watch                           # background file watcher
mfs add . --force                           # re-hash all files (mtime untrusted)
mfs add . --async                           # kick off a background worker
mfs add ./docs/ --summarize                 # auto-generate LLM summaries
mfs add ./assets/ --describe                # auto-generate VLM image captions
```

Incremental sync is automatic: re-running `mfs add .` only processes files whose
content hash has changed.

### `mfs search <query>` — semantic search

```bash
mfs search "OAuth2 flow"                    # hybrid (dense + BM25 + RRF)
mfs search "OAuth2" --mode semantic         # dense only
mfs search "ERR_TOKEN" --mode keyword       # BM25 only
mfs search "auth" --top-k 20
mfs search "auth" --all                     # search the entire index
mfs search "auth" --path ./src/
mfs search "auth" --json                    # structured output for agents
```

Pipe-aware: `mfs cat <file> | mfs search "..."` reuses the existing index entry
for that file and filters the search to it, skipping the re-embed round trip.

### `mfs grep <pattern> [path]` — exact-match search

```bash
mfs grep "ERR_TOKEN_EXPIRED"
mfs grep -C 5 "OAuth" ./docs/               # context lines
mfs grep -i "error" ./src/                  # case-insensitive
mfs grep "def.*token" ./src/                # regex
```

Indexed files (Markdown, code, PDF-derived text) are matched via Milvus BM25 on
the original text; everything else automatically falls back to system `grep`.

### `mfs ls [path]` and `mfs tree [path]` — progressive directory views

```bash
mfs ls ./docs/               # default: --skim (heading + short summary)
mfs ls --peek ./docs/        # file names only
mfs ls --deep ./docs/        # full expansion

mfs tree ./docs/             # default: --skim
mfs tree --peek -L 2 ./docs/ # two levels, skeleton only
mfs tree --deep ./docs/
```

### `mfs cat <file>` — read a file or show an overview

```bash
mfs cat ./docs/auth.md                      # full content, like system cat
mfs cat -n 40:60 ./docs/auth.md             # specific line range
mfs cat --peek ./docs/auth.md               # heading skeleton
mfs cat --skim ./docs/auth.md               # headings + first paragraphs
mfs cat --deep ./docs/auth.md               # detailed expansion
mfs cat --peek auth.md --no-line-numbers    # drop the source-line gutter
```

The three density presets share one underlying model:

- `-W <n>` — characters shown per node (0 = heading only)
- `-H <n>` — number of nodes from the top of the file
- `-D <n>` — depth expanded (for hierarchical types: Markdown, code, JSON, dirs)

### `mfs remove <source>` — drop entries from the index

```bash
mfs remove ./docs/old.md
mfs remove ./old_dir/
```

### `mfs status` — indexing status

```bash
mfs status
mfs status --json
mfs status --needs-summary                  # files that still lack an LLM summary
```

---

## Configuration

Configuration lives at `~/.mfs/config.toml`. Nothing is required — defaults pick
OpenAI embeddings and Milvus Lite. A minimal example:

```toml
[embedding]
provider = "openai"                          # openai | onnx | google | voyage | jina | mistral | ollama | local
model    = "text-embedding-3-small"

[llm]
provider = "openai"
model    = "gpt-4o-mini"

[milvus]
# uri = "~/.mfs/milvus.db"                    # default: Milvus Lite, embedded
# uri = "http://localhost:19530"              # self-hosted Milvus
# uri = "https://xxx.zillizcloud.com"         # Zilliz Cloud
# token = "..."
```

Use `mfs config show` to inspect the effective config and `mfs config set <key>
<value>` to edit it from the CLI.

### Supported Milvus backends

| Backend         | URI                                  | Notes                               |
| --------------- | ------------------------------------ | ----------------------------------- |
| Milvus Lite     | `~/.mfs/milvus.db`                   | Default. Zero config. Single writer. |
| Self-hosted Milvus | `http://localhost:19530`          | Concurrent writers, full BM25.       |
| Zilliz Cloud    | `https://*.zillizcloud.com` + token  | Managed. Full BM25.                  |

### Supported embedding providers

| Provider | Example model                   | Dim  |
| -------- | ------------------------------- | ---- |
| openai   | `text-embedding-3-small`        | 1536 |
| onnx     | `gpahal/bge-m3-onnx-int8`       | 1024 |
| google   | `gemini-embedding-001`          | 768  |
| voyage   | `voyage-3-lite`                 | 512  |
| jina     | `jina-embeddings-v3`            | 1024 |
| ollama   | `bge-m3`, `nomic-embed-text`, … | varies |

### Project-level ignores

MFS respects `.gitignore` automatically and will also pick up a `.mfsignore` at
the project root (same syntax).

---

## For agents

MFS is designed to be driven by an LLM agent living inside a shell. A few things
that matter when writing an agent prompt:

- Every command has a `--json` mode with a unified `Hit` envelope
  (`{source, lines, content, score, metadata}`). `search`, `grep`, `ls`, and
  `tree` all share the same shape; `metadata.kind` disambiguates.
- `mfs cat <file> | mfs search "..."` and `mfs cat <file> | mfs grep "..."` are
  first-class pipelines. Piped input is treated as a scope filter, never as a
  query fallback.
- `--peek` on `ls` / `tree` / `cat` is the cheapest way to get an agent oriented
  in a new repo without reading full files.

---

## Development

```bash
uv sync                         # install dev dependencies
uv run pytest tests/ -v         # run the test suite
uv run ruff check src/ tests/   # lint
```

The codebase lives under `src/mfs/`:

- `cli.py` — Click entry point
- `ingest/` — scanner, chunker (incl. tree-sitter AST), PDF converter, worker
- `embedder/` — embedding providers (OpenAI, ONNX, Gemini, Voyage, Jina, Ollama, …)
- `llm/` — LLM / VLM providers for opt-in enrichment
- `search/` — search, grep, summary, density presets
- `output/` — display, pipe handshake, JSON schema
- `store.py` — Milvus collection wrapper

---

## License

Apache License 2.0. See [LICENSE](LICENSE).
