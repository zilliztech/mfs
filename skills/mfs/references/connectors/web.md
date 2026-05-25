# web connector (`web://`)

Crawled pages converted to markdown under `pages/<host>/<url-path>.md`. `cat`
returns the page markdown. `search` runs over page body chunks; `lines` locate
within the page.

Crawl is bounded by `start_urls` / `allowed_domains` / `max_pages` / `crawl_depth`.
Revisit uses HTTP **ETag/304** to skip unchanged pages (cheap re-sync). URLs are
canonicalized (drop fragment, sort/whitelist query params, lowercase host) so
different URLs don't collide to one path. Static HTML→md is done inline by the
connector (not the framework converter).
