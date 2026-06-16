# Examples

Use this page when you need a runnable path rather than a full reference page.
Each scenario includes prerequisites, commands, expected output shape, and the
next page to open when you need the details.

!!! note "Values are examples"
    Job ids, scores, line ranges, object counts, chunk counts, fingerprints, and
    upload connector ids vary by server, source content, and connector output.
    Use the field names and command flow as the stable parts.

## Pick the right path mode

| Server can read the client path? | Typical placement | Add command | Search scope to use later |
|---|---|---|---|
| Yes | CLI and `mfs-server` run on the same host, or both see the same mounted path | `mfs add PATH` | The original path, or the `file://local...` URI from results |
| No | Docker container, remote VM, or different host | `mfs add --upload PATH` | The uploaded `file://CLIENT_ID...` connector URI shown by `mfs connector list` |

!!! warning "Do not mix path modes"
    Same-host shared-path indexing asks the server to read `PATH` directly.
    Upload mode sends bytes from the client and indexes a server-side staged
    copy. If search or browse misses after an upload, inspect the registered
    connector URI and scope commands to that URI.

## 1. Same-host local folder

Use this when the CLI and server run on the same machine and the server process
can read the path you pass to `mfs add`.

Prerequisites:

- `mfs-server run` is already listening on `127.0.0.1:13619`.
- The local CLI can authenticate through `$MFS_HOME/server.token` or
  `MFS_API_TOKEN`.

```bash
mkdir -p /tmp/mfs-examples/local/notes

cat > /tmp/mfs-examples/local/README.md <<'EOF'
# MFS local example

The same-host path mode lets mfs-server read a local folder directly.
The default API endpoint is 127.0.0.1:13619.
Use cat with a line range before relying on a search snippet.
EOF

cat > /tmp/mfs-examples/local/notes/search.md <<'EOF'
# Search note

Search returns candidates.
Cat reopens exact evidence by path, range, or locator.
EOF

mfs add /tmp/mfs-examples/local
mfs job show JOB_ID
mfs search "default API endpoint" /tmp/mfs-examples/local --top-k 5
mfs cat /tmp/mfs-examples/local/README.md --range 1:6
```

Expected shape:

```text
queued (job JOB_ID). Worker running in background -- run `mfs status` to check progress.
{"id":"JOB_ID","status":"succeeded",...}

file://local/tmp/mfs-examples/local/README.md  score=...
   The default API endpoint is 127.0.0.1:13619.

# MFS local example
...
```

Next: [Quickstart](getting-started.md), [CLI Reference](cli.md), and
[Search and Browse](search-and-browse.md).

## 2. Docker or remote upload mode

Use this when the server cannot read the client path directly. This is the
normal host-CLI to Docker-server path unless you mount a shared directory and
pass the server-visible path with `--no-upload`.

Prerequisites:

- The server is reachable from the client.
- `MFS_API_URL` points to that server.
- `MFS_API_TOKEN` is the bearer token accepted by that server.

For a Docker container named `mfs-server`, the token can usually be read from
the persistent `/data` volume:

```bash
export MFS_API_URL=http://127.0.0.1:13619
export MFS_API_TOKEN="$(docker exec mfs-server cat /data/server.token)"
```

For a remote server, set the token you configured on that server:

```bash
export MFS_API_URL=https://mfs.example.com
export MFS_API_TOKEN="replace-with-your-server-token"
```

Then upload and index a client-side folder:

```bash
mkdir -p /tmp/mfs-examples/upload

cat > /tmp/mfs-examples/upload/runbook.md <<'EOF'
# Upload mode runbook

Upload mode is for Docker or remote servers that cannot read the client path.
The CLI sends changed files and the server indexes the staged copy.
EOF

mfs add --upload /tmp/mfs-examples/upload
mfs job show JOB_ID
mfs connector list
```

Expected shape:

```text
uploaded 1 changed, 0 renamed, 0 deleted
queued (job JOB_ID). Worker running in background -- run `mfs status` to check progress.
{"id":"JOB_ID","status":"succeeded",...}

file       active   file://CLIENT_ID/tmp/mfs-examples/upload
```

Use the displayed connector URI for search and browse if the bare host path is
not resolved:

```bash
TARGET="file://CLIENT_ID/tmp/mfs-examples/upload"

mfs connector inspect "$TARGET"
mfs search "server indexes the staged copy" "$TARGET" --top-k 5
mfs cat "$TARGET/runbook.md" --range 1:6
```

Expected `inspect` shape:

```json
{
  "root_uri": "file://CLIENT_ID/tmp/mfs-examples/upload",
  "type": "file",
  "status": "active",
  "objects": {"indexed": 1},
  "object_count": 1,
  "chunk_count": 1,
  "jobs": {"succeeded": 1}
}
```

Next: [Deployment](deployment.md), [Configuration](configuration.md), and
[Troubleshooting](troubleshooting.md).

## 3. Connector probe, add, and readback

This example uses the `web` connector because it has no local filesystem
assumption. Replace the target and TOML with another connector from
[Connectors](connectors.md) when you need Slack, Postgres, S3, GitHub, or another
source.

Prerequisites:

- The server has the connector dependencies for the scheme you use.
- The server process can reach the source and resolve any `env:` or `file:`
  credential references in the TOML.

```bash
cat > /tmp/mfs-examples-web.toml <<'EOF'
start_urls = ["https://example.com/"]
allowed_domains = ["example.com"]
max_pages = 1
EOF

mfs connector probe web://example --config /tmp/mfs-examples-web.toml
JOB_ID="$(mfs connector add web://example --config /tmp/mfs-examples-web.toml | sed -n 's/^job: //p')"

while :; do
  JOB_JSON="$(mfs job show "$JOB_ID")"
  printf '%s\n' "$JOB_JSON"
  STATUS="$(printf '%s\n' "$JOB_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])')"
  case "$STATUS" in
    succeeded) break ;;
    failed|cancelled) exit 1 ;;
  esac
  sleep 1
done

mfs connector inspect web://example
```

Expected shapes:

```text
web  ok=true  ...
```

```json
{
  "id": "JOB_ID",
  "status": "succeeded",
  "op_kind": "sync",
  "total_objects": 1,
  "succeeded_objects": 1,
  "failed_objects": 0
}
```

Read back through search plus locator. The exact source path and locator depend
on the connector output, so capture them from JSON:

```bash
HIT_JSON="$(mfs --json search "Example Domain" web://example --top-k 1)"
SOURCE="$(
  printf '%s\n' "$HIT_JSON" |
    python3 -c 'import json,sys; print(json.load(sys.stdin)["results"][0]["source"])'
)"
LOCATOR="$(
  printf '%s\n' "$HIT_JSON" |
    python3 -c 'import json,sys; hit=json.load(sys.stdin)["results"][0]; loc=hit.get("locator"); print(json.dumps(loc) if loc is not None else "")'
)"

if [ -n "$LOCATOR" ]; then
  mfs cat "$SOURCE" --locator "$LOCATOR"
else
  mfs cat "$SOURCE" --range 1:80
fi
```

Expected search shape:

```json
{
  "results": [
    {
      "source": "web://example/...",
      "content": "Example Domain...",
      "score": 0.8,
      "locator": {"lines": [1, 20]},
      "metadata": {
        "kind": "search",
        "chunk_kind": "body",
        "fields": {}
      }
    }
  ]
}
```

Next: [Connectors](connectors.md) and [Troubleshooting](troubleshooting.md).

## 4. Direct HTTP add, poll, search, and read

Use this when you are integrating without shelling out to `mfs`. The example
uses the same-host path mode; for true client/server upload protocol details,
see [HTTP API](api.md).

Prerequisites:

- The API URL is reachable.
- The server can read `/tmp/mfs-examples/http`.
- A worker can drain queued jobs. Source, Docker, and Compose SQLite all-in-one
  runs start an in-process worker; API/worker deployments need a worker process.
- `MFS_TOKEN` is the bearer token for `/v1` requests. `GET /healthz` does not
  require the token, but `/v1` endpoints do when auth is enabled.

```bash
mkdir -p /tmp/mfs-examples/http

cat > /tmp/mfs-examples/http/api.md <<'EOF'
# HTTP API example

POST /v1/add returns a job_id.
GET /v1/jobs/{job_id} returns job status and object counts.
GET /v1/search returns result envelopes with source, content, score, locator, and metadata.
GET /v1/cat reopens exact content.
EOF

export MFS_URL="${MFS_API_URL:-http://127.0.0.1:13619}"
export MFS_TOKEN="${MFS_API_TOKEN:-$(cat ${MFS_HOME:-$HOME/.mfs}/server.token 2>/dev/null || true)}"

JOB_ID="$(
  curl -sS -H "Authorization: Bearer $MFS_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"target":"/tmp/mfs-examples/http","process":false}' \
    "$MFS_URL/v1/add" |
    python3 -c 'import json,sys; print(json.load(sys.stdin)["job_id"])'
)"

while :; do
  JOB_JSON="$(
    curl -sS -H "Authorization: Bearer $MFS_TOKEN" \
      "$MFS_URL/v1/jobs/$JOB_ID"
  )"
  printf '%s\n' "$JOB_JSON" | python3 -m json.tool
  STATUS="$(printf '%s\n' "$JOB_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])')"
  case "$STATUS" in
    succeeded) break ;;
    failed|cancelled) exit 1 ;;
  esac
  sleep 1
done
```

After `status` is `succeeded`, search and reopen the first hit:

```bash
SEARCH_JSON="$(
  curl -G -sS -H "Authorization: Bearer $MFS_TOKEN" \
    "$MFS_URL/v1/search" \
    --data-urlencode "q=result envelopes with source" \
    --data-urlencode "path=/tmp/mfs-examples/http" \
    --data-urlencode "top_k=1"
)"

printf '%s\n' "$SEARCH_JSON" | python3 -m json.tool

SOURCE="$(
  printf '%s\n' "$SEARCH_JSON" |
    python3 -c 'import json,sys; print(json.load(sys.stdin)["results"][0]["source"])'
)"
LOCATOR="$(
  printf '%s\n' "$SEARCH_JSON" |
    python3 -c 'import json,sys; hit=json.load(sys.stdin)["results"][0]; loc=hit.get("locator"); print(json.dumps(loc) if loc is not None else "")'
)"

if [ -n "$LOCATOR" ]; then
  curl -G -sS -H "Authorization: Bearer $MFS_TOKEN" \
    "$MFS_URL/v1/cat" \
    --data-urlencode "path=$SOURCE" \
    --data-urlencode "locator=$LOCATOR" |
    python3 -m json.tool
else
  curl -G -sS -H "Authorization: Bearer $MFS_TOKEN" \
    "$MFS_URL/v1/cat" \
    --data-urlencode "path=$SOURCE" |
    python3 -m json.tool
fi
```

Expected shapes:

```json
{"job_id": "JOB_ID"}
```

```json
{
  "id": "JOB_ID",
  "status": "succeeded",
  "op_kind": "sync",
  "error": null,
  "total_objects": 1,
  "succeeded_objects": 1,
  "failed_objects": 0
}
```

```json
{
  "results": [
    {
      "source": "file://local/tmp/mfs-examples/http/api.md",
      "content": "GET /v1/search returns result envelopes...",
      "score": 0.82,
      "locator": {"lines": [1, 6]},
      "metadata": {"kind": "search", "chunk_kind": "body", "fields": {}}
    }
  ]
}
```

```json
{
  "source": "file://local/tmp/mfs-examples/http/api.md",
  "content": "# HTTP API example\n..."
}
```

Next: [HTTP API](api.md), [SDKs](sdks.md), and
[Troubleshooting](troubleshooting.md).

## 5. Daily search to exact evidence

Use this when a source is already indexed and you need to turn a candidate into
exact content without manually copying line numbers.

Prerequisites:

- The source has already been added and indexed.
- You know a path or URI scope, or you intentionally use `--all`.

```bash
HIT_JSON="$(mfs --json search "cat reopens exact evidence" /tmp/mfs-examples/local --top-k 1)"

SOURCE="$(
  printf '%s\n' "$HIT_JSON" |
    python3 -c 'import json,sys; print(json.load(sys.stdin)["results"][0]["source"])'
)"
LOCATOR="$(
  printf '%s\n' "$HIT_JSON" |
    python3 -c 'import json,sys; hit=json.load(sys.stdin)["results"][0]; loc=hit.get("locator"); print(json.dumps(loc) if loc is not None else "")'
)"

if [ -n "$LOCATOR" ]; then
  mfs cat "$SOURCE" --locator "$LOCATOR"
else
  mfs cat "$SOURCE" --range 1:80
fi

mfs cat "$SOURCE" --meta
```

If search is weak, use exact or browse-first commands:

```bash
mfs grep "cat reopens exact evidence" /tmp/mfs-examples/local
mfs ls /tmp/mfs-examples/local --json
mfs tree /tmp/mfs-examples/local -L 2
mfs head /tmp/mfs-examples/local/README.md -n 5
```

Expected shapes:

```json
{
  "source": "file://local/tmp/mfs-examples/local/notes/search.md",
  "content": "Cat reopens exact evidence..."
}
```

```json
{
  "source": "file://local/tmp/mfs-examples/local/notes/search.md",
  "media_type": "text/markdown",
  "size_hint": 123,
  "fingerprint": "..."
}
```

Next: [Search and Browse](search-and-browse.md), [CLI Reference](cli.md), and
[Troubleshooting](troubleshooting.md).

## Related references

| Page | Use it for |
|---|---|
| [Quickstart](getting-started.md) | First source run and local defaults. |
| [CLI Reference](cli.md) | Exact command names, flags, profiles, jobs, and connector commands. |
| [Search and Browse](search-and-browse.md) | Search, grep, locator replay, range reads, and weak-result recovery. |
| [Connectors](connectors.md) | Built-in connector catalog, TOML config, credentials, probe, add, update, and remove. |
| [Deployment](deployment.md) | Source, Docker, Compose, upload mode, tokens, and topology boundaries. |
| [HTTP API](api.md) | Direct `/v1` endpoints, auth, schemas, errors, and upload protocol. |
| [Configuration](configuration.md) | Server config lookup, environment overrides, auth, and CLI endpoint precedence. |
| [Troubleshooting](troubleshooting.md) | Endpoint, auth, upload, indexing, connector, search, and read failures. |
