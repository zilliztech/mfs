# Update flow — changing an existing connector

When a user wants to modify a connector that's already registered. Most
edits are cheap (auth rotate, raise a cap); some force re-embedding all
or part of the data.

## Locating the existing toml

```bash
ls -la $MFS_HOME/connectors/<alias>.toml
# OR if MFS_HOME unset:
ls -la ~/.mfs/connectors/<alias>.toml
```

If the toml is missing but the connector shows up in
`mfs connector ls`, it was registered via a hand-rolled
`mfs add --config /one/off/path.toml` and the spec lives wherever the
user kept it. Ask the user for the file path, or have them re-create
the toml from the current state:

```bash
mfs connector ls --json | jq '.[] | select(.uri == "<uri>") | .config'
```

That dumps the live config dict — copy it into a new toml file.

## What kinds of edits exist

### 1. Auth rotate

User got a new token / DSN. Update only that field; everything else
stays.

```toml
# before
token = "xoxb-OLD"

# after
token = "xoxb-NEW"
# or better:
token = "env:SLACK_BOT_TOKEN"   # then `export SLACK_BOT_TOKEN=xoxb-NEW`
```

```bash
mfs add slack://acme --config $MFS_HOME/connectors/acme.toml --update
```

Server re-validates auth on the next sync; no re-embedding.

### 2. Scope change (channels / projects / labels)

User wants to add or remove some objects from the indexed set.

```toml
# before
channel_types = ["public_channel"]

# after
channel_types = ["public_channel", "private_channel"]
```

```bash
mfs add slack://acme --config $MFS_HOME/connectors/acme.toml --update
```

Effect: newly-in-scope objects get indexed; previously-in-scope-now-out
objects stay indexed until the next full delete-detection pass. For
strict cleanup, run `--full` instead.

### 3. Raise / lower caps

`max_read_rows`, `chunk_max`, `max_file_bytes`. Raising = potentially
more chunks (more embedding spend); lowering = future syncs cap
earlier.

```toml
max_read_rows = 50000    # was 10000
```

```bash
mfs add <uri> --config <toml> --update
```

Already-indexed objects beyond the OLD cap don't auto-re-index — they
were "partial" before and stay partial until that object is re-synced.
To force them to pick up the new cap:

```bash
mfs add <uri> --update --since 1970-01-01    # treat all as changed
# OR
mfs add <uri> --full                          # nuke + re-ingest everything
```

### 4. `text_fields` change (structured / SaaS)

User wants different columns to become the embedded content. e.g. a
postgres `tickets` table previously embedded only `description`, now
should embed `title + description + tags`.

```toml
[[objects]]
match = "ticketing.public.tickets"
text_fields = ["title", "description", "tags"]   # was ["description"]
locator_fields = ["id"]
```

```bash
mfs add postgres://prod --config <toml> --update
```

**This DOES force re-embedding** of every row in that table, because
the chunk content shape changed. ASK the user to confirm:

> "Changing `text_fields` on `<uri>::<object>` re-embeds all ~<N> rows
> (estimated <X> chunks). At the configured embedding rate, that's
> roughly $<Y>. Proceed?"

`mfs add --estimate` gives the chunk count cheaply (no embedding).

### 5. `indexable = false` on a specific object

User wants to stop indexing one big / sensitive / noisy object without
removing the whole connector.

```toml
[[objects]]
match = "logs.app_logs"
indexable = false
```

```bash
mfs add <uri> --config <toml> --update
```

Effect: that object's existing chunks are removed from Milvus on next
sync; future syncs skip it.

### 6. Switch to a different embedding model (server-wide)

This is in `$MFS_HOME/server.toml` `[embedding]`, NOT in any connector
toml. It affects every connector. Warn the user:

> "Changing `[embedding].model` re-builds the Milvus collection (the
> dimension may differ; `mfs_chunks__v1_d1024` vs `d1536` vs `d3072` are
> separate collections). All connectors will need a `--full` re-sync.
> This is a major operation — ASK the user to confirm before touching
> server.toml's embedding section."

After changing:
```bash
mfs-server reload    # picks up the new server.toml
# then re-ingest each connector:
mfs connector ls --json | jq -r '.[].uri' | while read uri; do
  mfs add "$uri" --full
done
```

## The `--update` vs no-flag distinction

| Flag | Behaviour |
|---|---|
| `mfs add <uri>` | re-runs sync against the existing connector; respects fingerprint / incremental cursors |
| `mfs add <uri> --update` | also applies a new toml's config diff to the stored config_json |
| `mfs add <uri> --since <date>` | only re-process objects with mtime > date |
| `mfs add <uri> --full` | ignore caches/fingerprints; re-fetch + re-embed every object |
| `mfs add <uri> --config <new.toml> --update` | combine: new config + re-run sync |

If only the toml changed (not the data), `--update` is sufficient. If
the data changed, plain `mfs add <uri>` does an incremental pull.

## After any update

Tail the job:

```bash
mfs job ls --tail <job_id>
```

Smoke-check with one search the user knows should work:

```bash
mfs search "<known-recent-content>" <uri> --top-k 3
```

If empty / wrong, see `reference/troubleshooting.md`.
