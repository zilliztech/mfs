# web connector — ingest

URI: `web://<alias>`.

Crawls HTTP(S) pages starting from one or more URLs, converts each page
to markdown, indexes the markdown.

## Required toml fields

| key | what |
|---|---|
| `start_urls` | one or more starting URLs (`["https://docs.example.com/"]`) |

## Optional

| key | default | meaning |
|---|---|---|
| `allowed_domains` | _start_urls' domains_ | restrict crawl scope (e.g. `["docs.example.com"]`) |
| `max_pages` | 100 | hard cap on pages crawled |

## URI tree

```
web://<alias>/
├── <start-url-path>.md
└── ...                                ← one .md per crawled page
```

Page filenames derive from the URL path (slashes → hyphens, sanitized).

## env: example

```toml
start_urls = [
  "https://docs.example.com/getting-started",
  "https://docs.example.com/api-reference",
]
allowed_domains = ["docs.example.com"]
max_pages = 500
```

```bash
mfs add web://example-docs --config /tmp/mfs-web.toml
```

## Pitfalls

- **No JS rendering**: the crawler is HTTP-only. SPAs (React,
  Next.js) that render client-side will index empty / skeleton pages.
  Point at the SSR'd / static version of the site if it exists.
- **`max_pages` is exact**: hit the cap, the rest are silently
  skipped. Re-run with a higher cap to extend.
- **Authentication**: not supported. Public web only.
- **robots.txt**: respected by default.
- **rate limit**: built-in 1 req/sec per domain; raise via
  `--config` if you own the site.
- **Re-crawl is full**: this connector has no per-page modification
  detection; `mfs add` re-crawls everything inside `max_pages`.
