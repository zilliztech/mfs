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

# run with local ONNX embedding + Milvus Lite (zero API keys needed)
docker run -d -p 13619:13619 -v mfs-data:/data \
  mfs-server:0.4.0-beta.1

# or override embedding to OpenAI
docker run -d -p 13619:13619 -v mfs-data:/data \
  -e OPENAI_API_KEY=$OPENAI_API_KEY \
  mfs-server:0.4.0-beta.1
# (then: docker exec -it <id> mfs-server setup --section embedding   to flip provider)

# or override Milvus to Zilliz Cloud
docker run -d -p 13619:13619 -v mfs-data:/data \
  -e MFS_MILVUS_URI=$ZILLIZ_URI -e MFS_MILVUS_TOKEN=$ZILLIZ_TOKEN \
  mfs-server:0.4.0-beta.1
```

**First-launch tip**: the default ONNX embedding model (~600 MB, BGE-M3 int8)
is fetched on the first `mfs add` call and cached under `/data/onnx-cache/`.
Mount `/data` to a volume so the model survives container restarts.

Connector SDKs are baked at build time via `--build-arg EXTRAS="[postgres,slack,s3]"`
— the default `[all-connectors]` covers every scheme. The base config
(embedding / vlm / milvus / metadata / object_store / auth) is set up
interactively inside the container:

```bash
docker exec -it <container> mfs-server setup            # walk all sections
docker exec -it <container> mfs-server setup --section embedding   # just one
```

Compose wrapper (same image): `cd deployments/compose && docker compose up`.

### Backend selection (all runtime, no rebuild)

| Backend | Env | Default |
|---|---|---|
| Embedding | (via `mfs-server setup --section embedding`) | local ONNX `gpahal/bge-m3-onnx-int8` (multilingual, 1024-dim) |
| VLM / image summary | (via `mfs-server setup --section vlm`) | OFF (opt-in) |
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
