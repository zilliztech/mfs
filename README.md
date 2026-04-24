<h1 align="center">MFS</h1>

<p align="center">
  <strong>Semantic file search CLI — built for AI agents driving a shell.</strong>
</p>

<p align="center">
  <a href="https://github.com/zilliztech/mfs/blob/main/LICENSE"><img src="https://img.shields.io/github/license/zilliztech/mfs?style=flat-square" alt="License"></a>
  <img src="https://img.shields.io/badge/python-%3E%3D3.10-blue?style=flat-square&logo=python&logoColor=white" alt="Python">
  <a href="https://milvus.io/"><img src="https://img.shields.io/badge/powered%20by-Milvus-00A1EA?style=flat-square" alt="Milvus"></a>
  <a href="https://github.com/zilliztech/mfs/stargazers"><img src="https://img.shields.io/github/stars/zilliztech/mfs?style=flat-square" alt="Stars"></a>
</p>

**MFS** stands for both **M**emory **F**ile **S**earch and **M**ilvus **F**ile **S**earch.
It's a POSIX-style CLI (`ls` / `tree` / `cat` / `grep` / `search`) that gives an AI
agent semantic access to any folder — and gives a human the same toolkit at the
terminal. Files are the source of truth; Milvus is the derived index underneath.

---

## Where MFS sits

MFS is a **middle layer**: below it, Milvus / Zilliz Cloud is abstracted away;
above it, any agent application that has folders full of files — memory logs,
skill definitions, session transcripts, source code — can plug in without
touching a vector database.

```
┌────────────────────────────────────────────────────────────────────┐
│   Agent Applications                                               │
│   ─────────────────────────────────────────────────────────        │
│   memory systems      skill managers       codebase copilots       │
│   (daily .md logs)    (trees of SKILL.md)  (repo-aware chat)       │
│   session replayers   knowledge bases      …your next agent app    │
│   (session .jsonl)    (docs, PDFs)                                 │
└────────────────────────────────┬───────────────────────────────────┘
                                 │  invokes CLI / Skill
                                 ▼
┌────────────────────────────────────────────────────────────────────┐
│   MFS   ← you are here                                             │
│   ─────────────────────                                            │
│   📟  CLI    mfs add · search · grep · ls · tree · cat             │
│   🧠  Skill  /mfs-search   (in development — for Claude Code etc.) │
│                                                                    │
│   Hybrid retrieval · density presets · JSON Hit envelope ·         │
│   tree-sitter AST · pipe-aware · ~/.mfs/ state only                │
└────────────────────────────────┬───────────────────────────────────┘
                                 │  wraps / abstracts
                                 ▼
┌────────────────────────────────────────────────────────────────────┐
│   Milvus   (Lite · Self-hosted · Zilliz Cloud)                     │
│   ────────────────────────────────────────────                     │
│   dense vectors · BM25 sparse · RRF fusion · metadata filters      │
└────────────────────────────────────────────────────────────────────┘
```

### Why a CLI — not an SDK or an HTTP API?

Because **agents already speak shell.** An LLM agent can plan
`mfs tree --peek` → `mfs cat --skim` → `mfs search "..."` with zero integration
code — the same way a human developer would. No client library to version, no
service to keep alive, no schema to import; just a binary on `$PATH` and a
`--json` flag when the caller is a machine.

MFS ships the two things an agent-first tool actually needs:

- **📟 a CLI** — for any agent that can run shell commands (Claude Code, Codex
  CLI, OpenCode, your own)
- **🧠 a companion Skill** — `/mfs-search`, an in-development Claude Code skill
  that teaches an agent when and how to reach for MFS, so users get semantic
  retrieval without writing prompt scaffolding themselves

Closing the loop: **MFS is the tool agents use to build agent apps.** The same
CLI that powers a memory system or a skill manager is also what you hand to
*your own* agent while you're building it.

---

## Why MFS

- **🤖 Shell-native, agent-first.** The commands an agent already knows (`ls`, `cat`,
  `grep`) — now semantically aware. Every command has a `--json` mode with a unified
  `Hit` envelope so an agent can parse without regex.
- **🔎 Hybrid retrieval.** Dense vectors for meaning, BM25 for exact tokens, RRF for
  fusion. Short and long queries both work.
- **📏 Progressive browsing.** `--peek` / `--skim` / `--deep` on `ls` / `tree` / `cat`
  share one density model — orient an agent in a new repo without burning its
  context window.
- **🧩 Multi-format indexing.** Markdown, source code (tree-sitter AST across 15
  languages), PDF (via pymupdf4llm). JSON, CSV, HTML and friends stay grep-able.
- **🔀 Smart grep routing.** Indexed files go through Milvus BM25; everything else
  falls back to the system `grep` — you don't think about which is which.
- **🚫 No LLM in the hot path.** Chunking, summarization heuristics, embedding — all
  run without calling any LLM. LLM / VLM enrichment is strictly opt-in.
- **🧼 Zero intrusion.** All state lives in `~/.mfs/`. Your project directory gets
  nothing added to it.

---

## Install

```bash
git clone https://github.com/zilliztech/mfs.git
cd mfs
uv sync                      # base install (OpenAI embedding ready)
uv run mfs --help
```

MFS is managed with [`uv`](https://docs.astral.sh/uv/) and `pyproject.toml`.

<details>
<summary><b>Optional extras — other embedding / LLM providers</b></summary>

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

Environment variables (only the ones you actually use):

```bash
export OPENAI_API_KEY="sk-..."       # default provider
export GOOGLE_API_KEY="..."          # or GEMINI_API_KEY
export ANTHROPIC_API_KEY="..."
export VOYAGE_API_KEY="..."
export JINA_API_KEY="..."
export MISTRAL_API_KEY="..."
```

</details>

---

## Quick start

```bash
# 1. Index the current directory (incremental — re-runs are cheap)
$ mfs add .
Processing 184 files under /repo
Indexed: 184 files scanned, 184 touched, 0 deleted, 2341 chunks queued.
Worker running in background. Run `mfs status` to check progress.

# 2. Semantic search — pass a positional <path> scope (POSIX-style, like grep)
#    or --all to search every indexed folder.
#    Header is [N] <path>  score=…, with line numbers in the left gutter of
#    each body line (ripgrep-style).
$ mfs search "how do we handle token expiration" .
[1] src/auth/token.py  score=0.890
142  def refresh_token(user_id: str, refresh_jwt: str) -> Token:
143      """Exchange a refresh token for a new access token.
144
145      Raises TokenExpiredError if the refresh token is past its TTL —
146      the caller should redirect to login.
147      """
148      ...

[2] docs/auth.md  score=0.710
 24  ## Token expiration
 25
 26  Access tokens live 15 minutes; refresh tokens live 14 days.

# 3. Exact-match — Milvus BM25 for indexed files, system grep for the rest
$ mfs grep "ERR_TOKEN_EXPIRED" .
src/auth/token.py
167      raise TokenExpiredError("ERR_TOKEN_EXPIRED")

# 4. Orient yourself in an unfamiliar folder — cheap peek
$ mfs tree --peek -L 2 ./docs/

# 5. Check indexing status
$ mfs status
```

---

## 🤖 For agents driving a shell

MFS gives an agent two complementary command families:

- **🔎 Search** — flat retrieval over the whole corpus (dense + BM25 + RRF)
- **📖 Browse** — walk along the natural hierarchy of files and folders
  (headings, symbols, directory trees), paying only a few tokens to see what's
  there

Search finds candidates in a sea of text. Browse lets the agent look around
*between* those candidates without reading whole files. Two legs — neither
works as well on its own.

### 🔎 Search — find candidates in a sea of text

Flat, corpus-wide retrieval. `mfs search` is hybrid (dense + BM25 + RRF);
`mfs grep` is exact-match (Milvus BM25 for indexed files, falls back to the
system `grep` for everything else).

Both commands take a **positional `<path>` scope** — POSIX-style, like
`grep pattern path`. Pass `--all` to search every indexed folder; without a
`<path>` or `--all`, the command errors rather than silently defaulting to the
whole index.

```bash
mfs search "how do we handle token expiration" .   # hybrid, scope = cwd
mfs search "oauth flow" ./src/ --mode semantic     # dense only
mfs search "ERR_TOKEN" ./src/ --mode keyword       # BM25 only
mfs search "auth" --all --top-k 20                 # across all indexed folders
mfs search "auth" ./src/ --json                    # scoped + JSON for parsing

mfs grep "ERR_TOKEN_EXPIRED" .                     # Milvus BM25 / system grep
mfs grep -C 5 "OAuth" ./docs/                      # context lines
mfs grep "def.*token" ./src/                       # regex
```

### 📖 Browse — see what's there without reading everything

Files and directories come with natural structure — Markdown has headings,
source code has classes and functions, a directory has children and summaries.
MFS exposes that structure at three **density presets**, with the same mental
model on every file type:

| Preset    | Cost     | What an agent sees                             | Answers                           |
| --------- | -------- | ---------------------------------------------- | --------------------------------- |
| `--peek`  | cheapest | structure only — headings, symbols, file names | *what is this thing?*             |
| `--skim`  | medium   | structure + a short paragraph per node         | *what is each section about?*     |
| `--deep`  | highest  | full expansion down the outline                | *I'm about to edit this; show me* |

```bash
# directories
mfs tree --peek -L 2 ./docs/       # skeleton, two levels deep
mfs ls --skim ./docs/              # every file with a one-paragraph summary
mfs tree --deep ./docs/            # full expansion

# single files
mfs cat --peek ./docs/auth.md      # heading-only skeleton
mfs cat --skim ./docs/auth.md      # headings + first paragraph of each
mfs cat --deep ./docs/auth.md      # detailed expansion
mfs cat -n 40:60 ./docs/auth.md    # drill in to a specific line range
```

All three presets are driven by the same three knobs — `-W <chars>` (per-node
width), `-H <n>` (how many top-level nodes), `-D <n>` (depth). Custom budgets
work anywhere: `mfs cat -W 80 -H 5 -D 2 auth.md`.

The point of browse: an agent should *not* have to choose between "read the
whole file" (expensive) and "stare at a single search chunk" (no surrounding
context). Spending a few hundred tokens on `--peek` of a whole directory is
enough to know what lives there — and cheap enough to catch things search
might have missed.

### 🤝 Search × Browse — the two-leg workflow

Search is flat; browse is hierarchical. A typical agent pass alternates them:

```bash
# 1. orient — peek the whole repo into a few hundred tokens
mfs tree --peek -L 2 .

# 2. locate — flat hybrid search (scope = cwd, or --all for everything)
mfs search "how is session state stored" . --top-k 5

# 3. contextualize — skim the candidate file so the hit has surroundings
mfs cat --skim ./src/session/store.py

# 4. drill in — read the exact lines before editing
mfs cat -n 80:140 ./src/session/store.py
```

Browse doubles as a cheap safety net for search: a `--peek` over a
neighbouring directory often surfaces a relevant file that didn't match the
search query's wording.

### 📦 Structured output — the `--json` envelope

Every command (`search`, `grep`, `ls`, `tree`, `cat`) accepts `--json` and emits
the same **Hit envelope** — `{source, lines, content, score, metadata}`.
`metadata.kind` tells the caller which command produced the hit, so one parser
handles all five.

```bash
$ mfs search "oauth flow" --json
[
  {
    "source": "/repo/src/auth/oauth.py",
    "lines": [42, 98],
    "content": "class OAuthClient:\n    ...",
    "score": 0.87,
    "metadata": {
      "kind": "search",
      "content_type": "code",
      "is_dir": false,
      "chunk_index": 3,
      "language": "python",
      "symbol_name": "OAuthClient"
    }
  }
]
```

---

## Optional: LLM summaries & VLM image descriptions

Both are **opt-in and off by default** — MFS's chunking and embedding pipeline
never calls an LLM unless you ask it to. Flip them on when vague queries miss
the right files, or when you want image assets to be searchable.

### Summaries sharpen recall on vague queries

Text files (Markdown, code, PDFs converted to Markdown) can carry an
auto-generated LLM summary that's embedded **alongside** the body chunks in
the same collection. The summary participates in the same hybrid retrieval,
so a vague query like *"how does the new onboarding flow work"* can hit the
summary even when no single body chunk matches the wording. When a summary
wins, the result's header picks up a `[summary]` marker so the caller can
tell it apart from body chunks.

```bash
mfs add ./docs/ --summarize                                     # auto-generate via the configured [llm]
mfs add ./docs/note.md --summary "Three auth flows: …"          # inject an external summary
```

Summaries are **stale-tracked**: when a summarized file is re-indexed after
edits, its summary is marked stale but kept around until you regenerate it.
`mfs status --needs-summary` lists what still needs a fresh pass.

### Image descriptions make binary assets searchable

There is **no direct image embedding** (no CLIP-style multimodal encoder).
Instead the path is **image → VLM text description → text embedder**, so the
image shows up as a normal search hit with `content_type: vlm_description` in
the JSON envelope. Works for PNG / JPG / WEBP / GIF / BMP.

```bash
mfs add ./assets/ --describe                                    # auto-generate via a VLM-capable provider
mfs add ./assets/arch.png --description "System architecture diagram: …"   # inject
```

### Providers

| Role                | Providers implemented                                        |
| ------------------- | ------------------------------------------------------------ |
| Text summaries      | openai, anthropic, google, ollama, mistral                   |
| VLM image descriptions | openai (gpt-4o / gpt-4o-mini / gpt-4-turbo), anthropic, google |

Install with `uv sync --extra llm-<name>` and configure in `~/.mfs/config.toml`:

```toml
[llm]
provider = "openai"
model    = "gpt-4o-mini"     # must be a vision model if you use --describe
```

Pointing `--describe` at a text-only provider (ollama / mistral) exits with an
error rather than silently skipping the image.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                       mfs <cmd>                              │
│   add · search · grep · ls · tree · cat · status · remove   │
└────────────────┬───────────────────────────┬─────────────────┘
                 │                           │
        ┌────────┴────────┐         ┌────────┴────────┐
        │   Ingest        │         │   Retrieve      │
        │                 │         │                 │
        │  scan           │         │  hybrid search  │
        │   ↓             │         │  (dense + BM25  │
        │  chunk          │         │   + RRF)        │
        │  (tree-sitter,  │         │   ↓             │
        │   markdown,     │         │  density render │
        │   pymupdf4llm)  │         │  (peek/skim/    │
        │   ↓             │         │   deep)         │
        │  embed          │         │                 │
        └────────┬────────┘         └────────┬────────┘
                 │                           │
                 ▼                           ▲
     ┌────────────────────────────────────────────────┐
     │   Milvus   (Lite · Self-hosted · Zilliz Cloud) │
     │   dense vectors · BM25 sparse · metadata       │
     └────────────────────────────────────────────────┘
                           ▲
                           │ derived index
                           │
     ┌────────────────────────────────────────────────┐
     │   Your files  (source of truth; read-only)     │
     │   state → ~/.mfs/ only; project dir untouched  │
     └────────────────────────────────────────────────┘
```

Incremental sync is automatic: each `mfs add .` hashes files and only re-embeds
what changed. The Milvus collection is a rebuildable cache — delete `~/.mfs/`
and a fresh `mfs add .` reconstructs everything from the files on disk.

---

## Configuration

Config lives at `~/.mfs/config.toml`. Nothing is required — defaults pick OpenAI
embeddings and Milvus Lite. Minimal example:

```toml
[embedding]
provider = "openai"                          # openai | onnx | google | voyage | jina | mistral | ollama | local
model    = "text-embedding-3-small"

[llm]
provider = "openai"
model    = "gpt-4o-mini"

[milvus]
# uri = "~/.mfs/milvus.db"                   # default: Milvus Lite, embedded
# uri = "http://localhost:19530"             # self-hosted Milvus
# uri = "https://xxx.zillizcloud.com"        # Zilliz Cloud
# token = "..."
```

Use `mfs config show` to inspect effective config and `mfs config set <key>
<value>` to edit it from the CLI.

<details>
<summary><b>Milvus backends</b></summary>

| Backend            | URI                                  | Notes                                |
| ------------------ | ------------------------------------ | ------------------------------------ |
| Milvus Lite        | `~/.mfs/milvus.db`                   | Default. Zero config. Single writer. |
| Self-hosted Milvus | `http://localhost:19530`             | Concurrent writers, full BM25.       |
| Zilliz Cloud       | `https://*.zillizcloud.com` + token  | Managed. Full BM25.                  |

</details>

<details>
<summary><b>Embedding providers</b></summary>

| Provider | Example model                    | Dim    |
| -------- | -------------------------------- | ------ |
| openai   | `text-embedding-3-small`         | 1536   |
| onnx     | `gpahal/bge-m3-onnx-int8`        | 1024   |
| google   | `gemini-embedding-001`           | 768    |
| voyage   | `voyage-3-lite`                  | 512    |
| jina     | `jina-embeddings-v3`             | 1024   |
| ollama   | `bge-m3`, `nomic-embed-text`, …  | varies |

</details>

MFS reads `.gitignore` automatically and picks up a `.mfsignore` at the project
root (same syntax) — use either to exclude paths from indexing.

---

## Development

```bash
uv sync                          # install dev dependencies
uv run pytest tests/ -v          # run the test suite
uv run ruff check src/ tests/    # lint
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
