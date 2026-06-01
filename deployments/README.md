# MFS deployments

Three forms. Single container (compose) is the supported v0.4 topology; helm
chart is sketched for the post-v0.4 api/worker split.

## Single container (all-in-one) — supported today

API + inline task processing, SQLite metadata + cache + Milvus Lite on a mounted
volume. This is the runnable v0.4 topology.

```bash
# build (include all connector SDKs)
docker build -f deployments/docker/Dockerfile \
             --build-arg EXTRAS="[all-connectors]" \
             -t mfs-server:0.4.0-beta.1 .

# run with Milvus Lite (default; mount a volume to persist)
docker run -d -p 8765:8765 -v mfs-data:/data \
  -e OPENAI_API_KEY=$OPENAI_API_KEY \
  mfs-server:0.4.0-beta.1

# or with Zilliz Cloud
docker run -d -p 8765:8765 -v mfs-data:/data \
  -e MFS_MILVUS_URI=$ZILLIZ_URI -e MFS_MILVUS_TOKEN=$ZILLIZ_TOKEN \
  -e OPENAI_API_KEY=$OPENAI_API_KEY \
  mfs-server:0.4.0-beta.1

# pulled image (after CI publishes to GHCR)
docker run -d -p 8765:8765 -v mfs-data:/data \
  -e OPENAI_API_KEY=$OPENAI_API_KEY \
  ghcr.io/zilliztech/mfs:0.4.0-beta.1
```

The server boots without `OPENAI_API_KEY` (browse: ls/cat/grep/status work); the
key is only needed for `add`/`search` (embedding). Connector SDKs are baked at
build time via `--build-arg EXTRAS="[postgres,slack,s3]"` — the default
`[all-connectors]` covers every scheme.

Compose wrapper (same image): `cd deployments/compose && docker compose up`.

### Backend selection (all runtime, no rebuild)

| Backend | Env | Default |
|---|---|---|
| Metadata DB | `MFS_METADATA_DSN=postgresql://...` | SQLite under `/data` |
| Milvus | `MFS_MILVUS_URI=...` + `MFS_MILVUS_TOKEN=...` | Lite at `/data/milvus.db` |
| Object store | `MFS_OBJECT_STORE_*` | Local fs under `/data` |
| API token | `MFS_API_TOKEN=...` | Auto-generated to `/data/server.token` |

## Team / client-server (compose) and Kubernetes (helm)

`deployments/compose/docker-compose.yml` (bottom, disabled) and
`deployments/helm/mfs` render the scalable api/worker split with Postgres
metadata, MinIO object store, and Milvus via Zilliz Cloud. The Postgres
metadata backend is already wired (`MFS_METADATA_DSN`); the standalone worker
daemon lands post-v0.4. See the chart's NOTES.txt.

```bash
helm lint deployments/helm/mfs
helm template mfs deployments/helm/mfs --set search.uri=https://xxx.zillizcloud.com
```

## Verified

- image builds; AIO container serves `/v1/server/info`, `/v1/status`, and a full
  `add → search → ls` cycle (Lite, with OpenAI key)
- boots browse-only WITHOUT `OPENAI_API_KEY` (lazy embedding client)
- `docker compose config` valid; stack comes up healthy
- `helm lint` clean; `helm template` renders api+worker+service
