# Quickstart

## Install

```bash
git clone https://github.com/zilliztech/mfs.git
cd mfs
uv sync
uv run mfs --help
```

The default install uses OpenAI embeddings and Milvus Lite.

```bash
export OPENAI_API_KEY="sk-..."
```

For local embeddings:

```bash
uv sync --extra onnx
mfs config set embedding.provider onnx
```

## Build your first index

```bash
mfs add .
```

By default, `mfs add` scans files, writes work into `~/.mfs/queue.json`, starts a
detached worker, and returns quickly.

To wait for embedding in the foreground:

```bash
mfs add . --sync
```

Check progress:

```bash
mfs status
```

## Search

Search needs an explicit path scope or `--all`.

```bash
mfs search "where do we configure database retries" .
mfs search "oauth callback flow" ./docs --top-k 5
mfs search "ERR_TOKEN" ./src --mode keyword
mfs search "session storage" --all
```

## Browse

```bash
mfs tree --peek -L 2 .
mfs ls --skim ./docs
mfs cat --skim ./docs/auth.md
mfs cat -n 40:90 ./docs/auth.md
```

## Exact search

```bash
mfs grep "ERR_TOKEN_EXPIRED" .
mfs grep -C 5 "OAuth" ./docs
mfs grep -i "refresh token" --all
```

Indexed files use Milvus-backed keyword filtering. Non-indexed text-like files
can still be searched through the system grep fallback.
