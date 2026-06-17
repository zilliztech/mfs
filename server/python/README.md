# mfs-server

The server side of **MFS — Multi-source File-like Search**: a context engine
that turns code, docs, messages, databases, and object stores into one
file-like, searchable namespace for AI agents and developers.

`mfs-server` is the heavy half of MFS. It owns the connectors, the ingest
pipeline, the vector index, and the storage — and exposes everything over an
HTTP `/v1` control plane. The thin [`mfs` CLI](https://crates.io/crates/mfs-cli)
and the generated SDKs are just clients of this server.

- **Project & docs:** https://zilliztech.github.io/mfs/
- **Source:** https://github.com/zilliztech/mfs

## Install

```bash
uv tool install mfs-server                     # core + local ONNX embeddings, Milvus Lite, SQLite
uv tool install "mfs-server[all-connectors]"   # add every connector's SDK
uv tool install "mfs-server[pg,s3,slack]"      # or just the connectors you need
```

`uv tool install` puts `mfs-server` on your PATH in an isolated environment. The
defaults run fully offline: local ONNX (BGE) embeddings, Milvus Lite, and
SQLite, with no cloud account or API key.

## Run

```bash
mfs-server setup     # optional: write $MFS_HOME/server.toml (defaults to ~/.mfs)
mfs-server run       # all-in-one: API + inline task processing on 127.0.0.1:13619
```

For a horizontally scaled deployment, split the roles:

```bash
mfs-server api                    # HTTP control plane only
mfs-server worker --concurrency auto   # queue worker(s)
```

Point the `mfs` CLI (or an SDK) at the server and you're ready to
`mfs add` a source and `mfs search` it. See the
[Quickstart](https://zilliztech.github.io/mfs/getting-started/) for the full
first run.

## Optional native acceleration

A few hot paths — the gitignore directory walk, parallel content hashing,
linear grep, and `tail` — have an optional PyO3 extension (`mfs-server-rs`). The
server falls back to a pure-Python implementation when it isn't installed, so it
is never required; it only makes large inputs faster. See
[Deployment](https://zilliztech.github.io/mfs/deployment/) for how to build it
in.

## License

Apache-2.0.
