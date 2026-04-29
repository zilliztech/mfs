# Configuration

MFS stores user configuration at:

```bash
~/.mfs/config.toml
```

Generate or inspect it:

```bash
mfs config path
mfs config init
mfs config show
```

## Minimal config

```toml
[embedding]
provider = "openai"
model = "text-embedding-3-small"

[milvus]
uri = "~/.mfs/milvus.db"
collection_name = "mfs_chunks"
account_id = "default"
```

All keys have defaults. The generated config comments document the available
values.

## Embeddings

Supported providers:

| Provider | Typical model |
| --- | --- |
| `openai` | `text-embedding-3-small` |
| `onnx` | local BGE-M3 ONNX model |
| `google` | Gemini embeddings |
| `voyage` | Voyage embeddings |
| `jina` | Jina embeddings |
| `mistral` | Mistral embeddings |
| `ollama` | local Ollama embedding model |
| `local` | sentence-transformers |

Examples:

```bash
mfs config set embedding.provider onnx
mfs config set embedding.provider openai
mfs config set embedding.model text-embedding-3-large
```

Provider API keys can be stored in config, but environment variables are
usually better:

```bash
export OPENAI_API_KEY="sk-..."
export GOOGLE_API_KEY="..."
export VOYAGE_API_KEY="..."
export JINA_API_KEY="..."
export MISTRAL_API_KEY="..."
```

## Optional LLM and VLM enrichment

LLM config is only used when you request summaries or image descriptions.

```toml
[llm]
provider = "openai"
model = "gpt-4o-mini"
```

Text summaries are available through OpenAI, Anthropic, Google, Ollama, and
Mistral providers. Image descriptions require a vision-capable provider.

## Indexing filters

```toml
[indexing]
include_extensions = []
exclude_extensions = []
```

If `include_extensions` is non-empty, only those extensions are indexed.
`exclude_extensions` always wins.

MFS also reads `.gitignore` and `.mfsignore` patterns from the project root.

## Cache

```toml
[cache]
max_size_mb = 500
```

This controls the converted Markdown cache for PDF and DOCX files.

## Milvus

```toml
[milvus]
uri = "~/.mfs/milvus.db"
collection_name = "mfs_chunks"
account_id = "default"
token = ""
```

See [Milvus Backends](backends.md) for backend-specific examples.
