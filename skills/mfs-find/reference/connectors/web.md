# web connector — search & browse

## URI tree

```
web://<alias>/
├── <url-path>.md                     ← one .md per crawled page
└── ...
```

Page filenames derive from the URL path (sanitized). The HTTP→markdown
conversion is via markitdown.

## Chunk kind

`chunk_body` — recursive markdown chunker (headings as natural splits).

## Locator

`{"lines": [s, e]}` — chunk position within the converted markdown.

## Search strategy

| Intent | Use |
|---|---|
| Find a page about X | `mfs search "X" web://<alias>` |
| Section in a known page | `mfs cat web://<alias>/<path>.md --range A:B` after a search hit |
| Whole site outline | `mfs ls web://<alias>` |

## Pitfalls

- **No JS execution**: SPAs that render client-side will index empty
  or skeleton pages. The site's SSR / static version, if it exists,
  works.
- **`max_pages` is a hard cap**: hit it and the rest are silently
  skipped. Bump and re-sync to extend.
- **Re-sync is full**: no per-page modification detection in v1. Every
  `mfs add` re-crawls the configured `max_pages`.
- **External links**: only the `allowed_domains` are crawled. Links
  outside that scope appear in the markdown as link refs but the
  target pages aren't indexed.
