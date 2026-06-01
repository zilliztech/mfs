# MFS server image. One image, three roles via the entrypoint:
#   mfs-server-aio : `run`    — API + inline task processing (single container demo)
#   mfs-api        : `api`    — HTTP control plane
#   mfs-worker     : `worker` — queue worker
#
# Build context = repo root:
#   docker build -f deployments/docker/Dockerfile \
#                --build-arg EXTRAS="[all-connectors]" \
#                -t mfs-server:0.4.0-beta.1 .
#
# Backends (DB / Milvus / object store) are all RUNTIME — pass env at `docker run`:
#   MFS_METADATA_DSN=postgresql://...   # default = SQLite under /data
#   MFS_MILVUS_URI=https://...zillizcloud.com / MFS_MILVUS_TOKEN=...  # default = Lite under /data
#   MFS_API_TOKEN=...                   # default = auto-generated to /data/server.token
#   OPENAI_API_KEY=...                  # required for embedding (`add` / `search`)
#
# The native Rust accelerator (server-rs) is OPTIONAL — omitted here so the image
# stays pure-Python; mfs_server.common.accel falls back transparently.
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MFS_HOME=/data

# build deps for any wheels that need compiling (kept minimal)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY server/python /app/server/python
# server/python/pyproject.toml references ../../README.md as the long_description.
# Pull the repo-root README into the layout that resolves so the build doesn't break.
COPY README.md /app/README.md

# install the server. Pass extras at build time, e.g.:
#   --build-arg EXTRAS="[postgres,slack,s3]"
ARG EXTRAS=""
RUN pip install "/app/server/python${EXTRAS}"

# data volume: SQLite metadata + Milvus Lite + cache live here (mount to persist!)
RUN mkdir -p /data
VOLUME ["/data"]
EXPOSE 8765

# default = all-in-one; override CMD for api/worker split (see compose)
ENTRYPOINT ["mfs-server"]
CMD ["run", "--bind", "0.0.0.0:8765"]
