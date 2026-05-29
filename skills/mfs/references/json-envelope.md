# Result envelope & reopening a hit

`mfs search` / `mfs grep` (with `--json`) return a stable envelope. Outer shape
is fixed across connectors; `locator` and `metadata.fields` are per-connector
but documented.

```json
{
  "source": "postgres://prod/public/tickets/rows.jsonl",
  "content": "subject: Login broken after SSO migration\n\ndescription: ...",
  "score": 0.842,
  "locator": {"id": 12},
  "metadata": {"kind": "search", "chunk_kind": "row_text",
               "connector_type": "postgres", "media_type": "application/x-ndjson",
               "fields": {"status": "open", "priority": "high"}}
}
```

| field | use |
|---|---|
| `source` | the object URI — feed to `cat`/`head`/`export` |
| `content` | snippet to read |
| `score` | ranking; very low (<0.5) often unreliable |
| `locator` | per-chunk identity (one unified field, see shapes below) → feed back to `cat --locator` |
| `metadata.chunk_kind` | body / row_text / thread_aggregate / record_aggregate / vlm_description / summary / ... |
| `metadata.fields` | business fields you get without opening the object |

## `locator` shapes — three forms

| chunk_kind | locator | how to reopen |
|---|---|---|
| `body` / code / document | `{"lines": [start, end]}` | `mfs cat <source> --range start:end` (or `cat --locator '{"lines":[s,e]}'`) |
| `row_text` (DB row, issue, ...) | connector PK dict (e.g. `{"id": 12}`, `{"number": 42}`) | `mfs cat <source> --locator '{...}'` |
| `thread_aggregate` (slack / feishu chat) | `{"thread_ts": "...", "chunk_index": 0, "msg_range": [s,e]}` | `mfs cat <source> --locator '{"thread_ts":"..."}'` |
| `directory_summary` / `schema_summary` / `vlm_description` | `null` | `mfs cat <source>` (object-wide) |

The framework reserves the key name `lines` inside `locator` for the body /
code / document path — your connector's `locator_fields` cannot claim it
(startup will reject it).

## Reopen rule

Look inside `locator`:

- key `lines` is present → text/code range; either:
  ```bash
  mfs cat <source> --range <start>:<end>
  # or
  mfs cat <source> --locator '{"lines":[<start>,<end>]}'
  ```
- any other keys → structured record; pass the whole dict back **verbatim**
  (it's keyed by the connector's `locator_fields`):
  ```bash
  mfs cat <source> --locator '{"id":12}'
  ```
- `null` → the chunk is once-per-object; `mfs cat <source>` is enough.

Don't hardcode a connector's locator shape — it's documented per connector in
`references/connectors/<scheme>.md` and in design 06 §3.
