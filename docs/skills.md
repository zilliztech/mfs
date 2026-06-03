# Agent Skills

MFS ships agent-facing skills that teach an agent how to use the CLI safely.
The v0.4 structure separates ingest workflows from find/read workflows.

| Path | Use it for |
|---|---|
| `skills/mfs-ingest/` | Registering new data sources, connector config, re-ingest guidance. |
| `skills/mfs-find/` | Searching, grepping, listing, and reading existing MFS sources. |

This split matters because adding a source can involve credentials and side
effects, while finding and reading are normally read-only.

For Codex-style skill environments, install or expose these directories as
skills, then ask the agent to use the appropriate one:

```text
Use mfs-find to locate the connector code for Slack pagination.
Use mfs-ingest to configure a new S3 source.
```
