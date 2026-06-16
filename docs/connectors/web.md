# Web (`web`)

The `web` connector crawls HTTP(S) pages, converts each one to markdown, and
indexes the result. Use it to pull a documentation site, a blog, or any public
web content into the same search surface as your code and docs.

It fetches HTTP responses only — it does **not** run client-side JavaScript, so
pages rendered entirely in the browser come back thin. For static or
server-rendered docs it works well.

## How MFS sees it

Each crawled page lands under `pages/`, keyed by host and URL path, as a markdown
document:

```text
web://docs/
└── pages/
    └── docs.example.com/
        ├── index.md
        ├── install.md
        └── guides/quickstart.md
```

Pages are `document` objects: converted to markdown, embedded, and fully
searchable.

## Configuration

```toml
start_urls = ["https://docs.example.com/"]
allowed_domains = ["docs.example.com"]
max_pages = 100
```

| Field | Meaning |
|---|---|
| `start_urls` | Where the crawl begins (one or more). |
| `allowed_domains` | Traversal boundary. Links outside these domains appear in the markdown but aren't crawled or indexed. |
| `max_pages` | A hard crawl cap. Raise it and re-sync if pages are missing. |

No credentials are involved — the connector fetches public pages. Authenticated
crawling is not modeled.

## Sync and freshness

The connector tracks each page's `etag` as its cursor, so a re-sync only
re-converts pages whose content actually changed. Deletions are
`explicit` — a page that disappears upstream isn't automatically removed; re-add
to re-crawl from the start URLs.

## Search and browse

```bash
mfs connector probe web://docs --config ./web.toml
mfs add web://docs --config ./web.toml

mfs search "installation" web://docs
mfs ls web://docs/pages/docs.example.com
mfs cat web://docs/pages/docs.example.com/index.md --range 1:80
```

## Pitfalls

- JavaScript-rendered pages come back mostly empty — the connector sees the raw
  HTTP response, not the hydrated DOM.
- `allowed_domains` is the crawl fence; widen it to follow links into other hosts.
- `max_pages` silently stops the crawl when hit; missing pages usually mean the
  cap was too low.
