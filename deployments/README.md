# MFS deployments

Three forms, matching design/10 §4.

## Single container (all-in-one) — supported today

API + inline task processing, SQLite metadata + cache + Milvus Lite on a mounted
volume. This is the runnable v0.4 topology.

```bash
# build
docker build -f deployments/docker/Dockerfile -t mfs-server:0.4.0 .
# run (Lite; mount a volume to persist)
docker run -d -p 8765:8765 -v mfs-data:/data \
  -e OPENAI_API_KEY=$OPENAI_API_KEY \
  mfs-server:0.4.0
# or with Zilliz Cloud
docker run -d -p 8765:8765 -v mfs-data:/data \
  -e MFS_MILVUS_URI=$ZILLIZ_URI -e MFS_MILVUS_TOKEN=$ZILLIZ_TOKEN \
  -e OPENAI_API_KEY=$OPENAI_API_KEY \
  mfs-server:0.4.0
curl localhost:8765/v1/server/info
```

The server boots without `OPENAI_API_KEY` (browse: ls/cat/grep/status work); the
key is only needed for `add`/`search` (embedding). Add connector SDKs at build time:
`--build-arg EXTRAS="[postgres,slack,s3]"`.

Compose wrapper (same image): `cd deployments/compose && docker compose up`.

## Team / client-server (compose §4.3) and Kubernetes (helm §4.4)

`deployments/compose/docker-compose.yml` (bottom, disabled) and
`deployments/helm/mfs` render the scalable api/worker split with Postgres metadata,
MinIO object store, and Milvus via Zilliz Cloud. These target the post-0.4 backends
(standalone worker daemon + Postgres metadata backend); see the chart's NOTES.txt.

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
