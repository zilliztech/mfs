# MFS server image. One image, three roles via the entrypoint:
#   mfs-server-aio : `run`    — API + inline task processing (single container demo)
#   mfs-api        : `api`    — HTTP control plane
#   mfs-worker     : `worker` — queue worker
#
# Build context = repo root:
#   docker build -f deployments/docker/Dockerfile \
#                --build-arg EXTRAS="[all-connectors]" \
#                -t mfs-server:0.4.0 .
#
# Layer caching strategy (matters a lot — without it every code edit re-pulls
# all of pymilvus / onnxruntime / markitdown / huggingface-hub / ... ~ minutes):
#
#   1. System deps                — apt build-essential (rare-change)
#   2. Dep manifest + README      — pyproject.toml only
#   3. Install deps over a STUB src so the wheel-builder succeeds without our
#      real code; this is the slow layer (~hundreds of MB downloaded) and
#      gets cached unless pyproject.toml itself changes
#   4. Copy real source           — invalidates on every commit; cheap
#   5. Reinstall the local package with --no-deps over the stub — fast (no
#      network, just rebuild the local wheel)
#
# Backends (DB / Milvus) are all RUNTIME — pass env at `docker run`:
#   MFS_METADATA_DSN=postgresql://...   # default = SQLite under /data
#   MILVUS_URI=https://...zillizcloud.com / MILVUS_TOKEN=...  # default = Lite under /data
#   MFS_API_TOKEN=...                   # default = auto-generated to /data/server.token
#   OPENAI_API_KEY=...                  # only if embedding.provider = openai
#
# The native Rust accelerator (server-rs) is OPTIONAL. By default the image stays
# pure-Python (mfs_server.common.accel falls back transparently). Build it in with:
#   docker build -f deployments/docker/Dockerfile --build-arg ACCEL=1 -t mfs-server .
# ACCEL=1 selects the `accel-build` stage below (Rust toolchain + maturin compile
# the PyO3 wheel); ACCEL=0 (default) skips that stage entirely, so the Rust
# toolchain is never pulled and the image is unchanged.

# --- optional native acceleration (server-rs), selected by ARG ACCEL ----------
ARG ACCEL=0

# Real builder: compile the mfs-server-rs PyO3 wheel. Only built when ACCEL=1
# (nothing in the default image depends on it).
FROM python:3.11-slim AS accel-build
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential curl \
    && rm -rf /var/lib/apt/lists/*
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal
ENV PATH="/root/.cargo/bin:${PATH}"
RUN pip install --no-cache-dir "maturin>=1.7,<2.0"
COPY server-rs /src/server-rs
RUN cd /src/server-rs && maturin build --release --out /wheels

# Empty alternative: provides an empty /wheels so the final COPY is a no-op when
# ACCEL=0, without dragging in the Rust toolchain.
FROM python:3.11-slim AS accel-0
RUN mkdir -p /wheels

FROM accel-build AS accel-1

# Resolves to accel-0 (empty) or accel-1 (built wheel) at build time.
FROM accel-${ACCEL} AS accel

FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MFS_HOME=/data

# Layer 1: system deps. Cached unless this RUN line itself changes.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Layer 2: dep manifest + the README pyproject.toml references via `readme = `.
# Copying ONLY these (not src/) means subsequent code edits don't invalidate
# the layer that does the slow `pip install`.
COPY server/python/pyproject.toml /app/server/python/pyproject.toml
COPY server/python/README.md /app/server/python/README.md

# Layer 3: install ALL third-party deps. Trick: hatchling needs the source tree
# to build a wheel; create a stub `src/mfs_server/__init__.py` so the local
# wheel can be assembled with no real code. `pip install` then resolves and
# downloads every dep declared in pyproject.toml.
#
# When pyproject.toml is unchanged across builds this layer is a perfect cache
# hit — even on a different machine pulling the same Dockerfile.
ARG EXTRAS=""
RUN mkdir -p /app/server/python/src/mfs_server \
    && printf '"""placeholder for layer caching; overridden by the real source in the next COPY"""\n' \
       > /app/server/python/src/mfs_server/__init__.py \
    && pip install "/app/server/python${EXTRAS}"

# Layer 4: real source. This is the layer that invalidates on every code
# change. With .dockerignore the COPY ships ~3 MB instead of ~800 MB of
# .venv / __pycache__ clutter.
COPY server/python/src /app/server/python/src

# Layer 5: reinstall the local package over the placeholder. --no-deps because
# every third-party dep is already pinned in the env from layer 3, --force-reinstall
# to swap the stub mfs_server out for the real one. No network calls here.
RUN pip install /app/server/python --no-deps --force-reinstall

# Layer 6 (optional): install the native acceleration wheel. /wheels is empty
# when ACCEL=0, so this is a no-op for the default pure-Python image.
COPY --from=accel /wheels /tmp/accel-wheels
RUN if ls /tmp/accel-wheels/*.whl >/dev/null 2>&1; then \
        pip install /tmp/accel-wheels/*.whl; \
    fi \
    && rm -rf /tmp/accel-wheels

# data volume: SQLite metadata + Milvus Lite + cache live here (mount to persist!)
RUN mkdir -p /data
VOLUME ["/data"]
EXPOSE 13619

# default = all-in-one; override CMD for api/worker split (see compose)
ENTRYPOINT ["mfs-server"]
CMD ["run", "--bind", "0.0.0.0:13619"]
