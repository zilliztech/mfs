# Result envelope & reopening a hit

`mfs search` / `mfs grep` (with `--json`) return a stable envelope. Outer shape is
fixed across connectors; `locator` and `metadata.fields` are per-connector but
documented.

```json
{
  "source": "postgres://prod/public/tickets/rows.jsonl",
  "lines": null,
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
| `lines` | `[start,end]` for text/code → `cat --range start:end`; null for structured |
| `content` | snippet to read |
| `score` | ranking; very low (<0.5) often unreliable |
| `locator` | structured unit key (pk / number / thread_ts) → `cat --locator '{...}'` |
| `metadata.chunk_kind` | body / row_text / thread_aggregate / record_aggregate / vlm_description / summary / ... |
| `metadata.fields` | business fields you get without opening the object |

## Reopen rule: locator first, lines second

- `locator` non-null → reopen the exact unit by passing the locator back **verbatim**
  (it's flat — keyed by the connector's `locator_fields`):
  ```bash
  mfs cat <source> --locator '{"id":12}'
  ```
- only `lines` non-null → read the range:
  ```bash
  mfs cat <source> --range <start>:<end>
  ```
- both present (e.g. slack thread) → prefer `locator` (authoritative), `lines` is a hint.

Don't hardcode a connector's locator shape — it's documented per connector in
`references/connectors/<scheme>.md` and in design 06 §3.
