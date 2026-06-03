# CLI Reference

The Rust CLI is distributed as the `mfs` binary. It talks to the HTTP API and
renders human-readable or structured output.

Common commands:

| Command | Purpose |
|---|---|
| `mfs status` | Check server health and registered sources. |
| `mfs add` | Register or upload a source for indexing. |
| `mfs search` | Search indexed sources. |
| `mfs grep` | Search for literal terms. |
| `mfs ls` | List a source path. |
| `mfs cat` | Read a source object or range. |
| `mfs connector` | Inspect connector catalog and configured sources. |

Examples:

```bash
mfs status
mfs add --wait ./repo
mfs add --upload --wait ./repo
mfs search "release installer script" ./repo --top-k 5
mfs cat ./repo/README.md --range 1:80
```

This page is intentionally only a scaffold for now. The next pass should
generate or verify option tables from `mfs --help` so the docs do not drift.
