# MFS server image (design/10 §4.2). One image, three roles via the entrypoint:
#   mfs-server-aio : `run`    — API + inline task processing (single container demo)
#   mfs-api        : `api`    — HTTP control plane
#   mfs-worker     : `worker` — queue worker (inline in `add` for v0.4; daemon Phase 5)
#
# Build context = repo root:  docker build -f deployments/docker/Dockerfile -t mfs-server:0.4.0 .
# The native Rust accelerator (server-rs) is OPTIONAL — omitted here so the image
# stays pure-Python; mfs_server.common.accel falls back transparently.
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MFS_HOME=/data \
    MFS_MILVUS_URI=/data/milvus.db

# build deps for any wheels that need compiling (kept minimal)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY server/python /app/server/python

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
