# Connectors

Connectors let MFS expose non-local sources through file-like paths. The
current connector family includes local files, web content, databases, object
stores, issue trackers, CRMs, and chat or document systems.

The server owns connector schemas and validation. The CLI can drive setup
through the server-side wizard:

```bash
mfs-server connector add postgres://prod
mfs-server connector add slack://workspace
```

Connector docs are currently split between two agent skills:

| Skill | Purpose |
|---|---|
| `skills/mfs-ingest/` | Adding, configuring, and troubleshooting sources. |
| `skills/mfs-find/` | Searching, browsing, and reading registered sources. |

The next documentation pass should turn the connector skill references into
human-facing connector pages.
