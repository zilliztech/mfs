# web connector (`web://`)

## What this is

A bounded web crawler that fetches public HTML pages, converts each to
markdown (via `markitdown`), and exposes the markdown as a filesystem tree
under `pages/<host>/<url-path>.md`. Static HTML only — no JS rendering, no
authenticated sites.

**When MFS helps**: an external documentation site / blog / wiki that has no
download bundle and isn't already in your repo. You want it searchable
locally without scraping by hand.

**When MFS doesn't help**: client-side-rendered SPAs (the connector sees the
HTML shell, not the rendered content); authenticated portals; sites that
gate by user-agent / bot detection.

## URI shape

```
web://docs-acme/                                       connector root
web://docs-acme/pages/docs.acme.com/                   host
web://docs-acme/pages/docs.acme.com/getting-started.md  one page (as markdown)
web://docs-acme/pages/docs.acme.com/api/auth.md
```

URL canonicalization happens before storing:
- fragment (`#section`) dropped
- query params sorted; non-whitelisted ones dropped
- host lowercased
- trailing `/` normalized

So `https://docs.acme.com/intro?lang=en&utm_source=x` and
`https://docs.acme.com/intro?utm_source=y&lang=en` become the same page,
not duplicates.

## Auth

None — the connector fetches public HTTP/HTTPS pages with no authentication.
It honours `robots.txt` (mostly — the implementation is best-effort).

If you need authenticated crawls, the right tool is to mirror the site
locally (`wget --mirror` / a vendor-provided ZIP) and use the **file**
connector instead.

## Connector config TOML

```toml
# ─── crawl bounds (required) ───
start_urls      = ["https://docs.acme.com/"]   # one or more seed URLs
allowed_domains = ["docs.acme.com"]            # never follow links outside these hosts

# ─── crawl scope (optional) ───
# max_pages    = 500                  # hard cap; default 1000
# crawl_depth  = 3                    # how many link-hops from start_urls; default 3
# request_delay_ms = 250              # politeness delay between requests; default 100
# user_agent   = "MFS-bot/1.0 (+contact@example.com)"
# query_params_whitelist = ["lang", "version"]   # query keys to KEEP after canonicalisation
```

No `[[objects]]` — pages are indexed as plain text/markdown via the document
chunking pipeline (chonkie RecursiveChunker).

## What each command does

| Command | Behaviour |
|---|---|
| `mfs ls web://<alias>/pages/` | lists crawled hosts. |
| `mfs ls web://<alias>/pages/<host>/` | lists crawled pages (as `.md` files). |
| `mfs tree web://<alias>` | full tree, depth-bounded. |
| `mfs cat web://<alias>/pages/<host>/<path>.md` | returns the converted markdown. |
| `mfs cat ... --range A:B` | line-range slice of the markdown. |
| `mfs head -n N` / `tail -n N` | first/last N lines of markdown. |
| `mfs grep "PATTERN" <path>` | linear grep over the markdown body (Rust accel). |
| `mfs search "QUERY"` | Milvus hybrid; hits return `{path, locator:{"lines":[start,end]}}` — reopen with `cat --range`. |

## Typical workflow

```bash
# 1. Register a doc site.
cat > docs-acme.toml <<'EOF'
start_urls = ["https://docs.acme.com/"]
allowed_domains = ["docs.acme.com"]
max_pages = 500
crawl_depth = 3
EOF
mfs add web://docs-acme --config docs-acme.toml

# 2. Wait for the crawl to finish (it's slower than a DB sync).
mfs status

# 3. Search.
mfs search "rotate the signing key" --connector-uri web://docs-acme --top-k 5
# Hit: web://docs-acme/pages/docs.acme.com/security/key-rotation.md  lines [88, 119]

# 4. Read.
mfs cat web://docs-acme/pages/docs.acme.com/security/key-rotation.md --range 88:119

# 5. Re-crawl periodically (only changed pages are re-fetched).
mfs add web://docs-acme --no-full
```

## Incremental sync (ETag / 304)

For every page, the connector stores the response `ETag` (or
`Last-Modified`). On re-sync, the request adds `If-None-Match` / `If-Modified-Since`;
the server responds `304 Not Modified` for unchanged pages — no body
download, no re-conversion, no re-embed. Only pages that legitimately
changed cost work. For a well-behaved doc site, re-syncs are cheap.

## Gotchas

1. **JS-rendered sites** (SPAs, framework-rendered docs) — the connector
   sees the unrendered HTML shell and gets nothing useful. If `cat` returns
   a near-empty markdown, that's why. Use a `wget --mirror` snapshot via
   the **file** connector instead.
2. **Login walls / paywalls** — same; the connector has no session support.
3. **`max_pages` is a hard cap** — when hit, sync stops. Re-sync doesn't
   resume from where it left off; it restarts BFS from `start_urls`. Tune
   `max_pages` / `crawl_depth` / `allowed_domains` to keep the frontier
   bounded.
4. **robots.txt** is honoured best-effort but not strictly. Don't crawl
   sites you don't have permission to.
5. **Conversion fidelity** — `markitdown` is good but not perfect; code
   blocks, tables, and footnotes usually survive; complex layouts (multi-
   column, side panels) may not. `cat` shows you exactly what the embedding
   sees.
6. **Politeness delay** — default 100 ms between requests. For external
   sites bump `request_delay_ms` to 500-1000 ms to avoid getting rate-
   limited or banned.
