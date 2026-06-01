"""Phase 14 — web connector against a local HTTP fixture covering real-world variants.

Spins up an aiohttp server on a random localhost port and points the `web://`
connector at it. The pages are interlinked so the BFS crawl has to:

  - traverse multiple hops (index -> about / docs -> docs/sub1)
  - cap at `max_pages` instead of crawling everything
  - honour `allowed_domains` (a link to a foreign host must NOT be followed)
  - skip 404 / 500 hops without aborting the run
  - canonicalise URL -> /pages/<host>/<path>.md
  - run markitdown HTML->md so semantic search hits page CONTENT, not raw HTML
  - second sync with same ETag -> 304 path: no chunk_count regression, the unique
    "v1" content stays cached + searchable.
  - mutating the seed (start_url) bumps its etag and the new content surfaces.
  - mutating a DEEP page (not the seed) — covered by the 304-link-discovery fix:
    the plugin persists each fetched page's child links into state, so when the
    seed returns 304 we still re-enqueue its known children and discover the
    deep mutation. Previously the BFS would short-circuit on the seed's 304
    and the deep page would never be re-fetched in that run.

Self-contained — needs OPENAI_API_KEY for embeddings (bash -ic), aiohttp + bs4 +
markitdown which the project already vendors."""

import asyncio
import os

from aiohttp import web

from mfs_server.config import load_server_config
from mfs_server.engine.engine import Engine

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, cond):
    results.append(bool(cond))
    print(f"  [{OK if cond else FAIL}] {name}")
    return cond


# --- Local site fixture --------------------------------------------------------
# state holds page bodies + etag versions so we can mutate between syncs.
PAGES: dict[str, dict] = {}


def _page(body_inner: str, etag: str) -> str:
    return (
        f"<!doctype html><html><head><title>fixture</title></head><body>{body_inner}</body></html>"
    )


def _seed_pages(host: str, foreign_host: str) -> None:
    PAGES.clear()
    PAGES["/"] = {
        "html": _page(
            "<h1>Fixture index</h1>"
            "<p>Welcome to the local crawler fixture.</p>"
            f"<a href='/about'>About</a> "
            f"<a href='/docs'>Docs</a> "
            f"<a href='/missing'>Dead</a> "  # -> 404
            f"<a href='/broken'>Broken</a> "  # -> 500
            f"<a href='http://{foreign_host}/leaked'>External</a>",
            etag="v1-idx",
        ),
        "etag": '"v1-idx"',
    }
    PAGES["/about"] = {
        "html": _page(
            "<h1>About</h1>"
            "<p>Photosynthesis converts sunlight into chemical energy via chlorophyll.</p>",
            etag="v1-about",
        ),
        "etag": '"v1-about"',
    }
    PAGES["/docs"] = {
        "html": _page(
            "<h1>Docs</h1>"
            "<p>Kubernetes orchestrates containers across a cluster of worker nodes.</p>"
            f"<a href='/docs/sub1'>Subdoc</a>",
            etag="v1-docs",
        ),
        "etag": '"v1-docs"',
    }
    PAGES["/docs/sub1"] = {
        "html": _page(
            "<h1>Sub1</h1>"
            "<p>BGP route reflectors reduce iBGP full-mesh requirements in large ASes.</p>",
            etag="v1-sub1",
        ),
        "etag": '"v1-sub1"',
    }
    # /missing -> 404, /broken -> 500 (handled in handler, no PAGES entry needed)


async def handler(request: web.Request) -> web.Response:
    path = request.path
    if path == "/missing":
        return web.Response(status=404, text="not found")
    if path == "/broken":
        return web.Response(status=500, text="boom")
    pg = PAGES.get(path)
    if not pg:
        return web.Response(status=404, text="no page")
    # ETag/304 path: respect If-None-Match for incremental check.
    inm = request.headers.get("If-None-Match")
    if inm and inm == pg["etag"]:
        return web.Response(status=304)
    return web.Response(text=pg["html"], content_type="text/html", headers={"ETag": pg["etag"]})


async def _start_server() -> tuple[web.AppRunner, str, int]:
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # noqa: SLF001 — aiohttp doesn't expose this
    return runner, "127.0.0.1", port


async def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set — run via bash -ic")
        raise SystemExit(2)

    runner, host, port = await _start_server()
    base = f"/tmp/mfs_web_{os.getpid()}"
    os.system(f"rm -rf '{base}'*")
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_m.db"
    cfg.milvus.uri = base + "_v.db"
    cfg.milvus.token = ""
    cfg.object_store.root = base + "_c"
    cfg.transformation_cache.db_path = base + "_t.db"
    cfg.summary.enabled = False
    cfg.chunk.chunk_size = 800
    eng = Engine(cfg)
    await eng.startup()

    site_origin = f"http://{host}:{port}"
    # We use a fake netloc as the "external" host so allowed_domains has something
    # concrete to exclude. Crawler won't actually reach it (it's blocked by allowed_domains
    # _before_ any HTTP call), so the address doesn't need to resolve.
    foreign_host = f"foreign-{port}.example"
    _seed_pages(host, foreign_host)

    conn_uri = "web://crawl"
    cfg_web = {
        "start_urls": [f"{site_origin}/"],
        "allowed_domains": [f"{host}:{port}"],  # explicit — foreign_host stays out
        "max_pages": 3,  # cap below total reachable pages (4)
    }

    try:
        eng.milvus.drop_collection("default")
        eng.milvus.ensure_collection("default")

        # --- 1) initial crawl --------------------------------------------------
        await eng.add(conn_uri, config=cfg_web)
        cid = (await eng.meta.fetchone("SELECT id FROM connectors WHERE root_uri=?", (conn_uri,)))[
            "id"
        ]
        objs = await eng.meta.fetchall(
            "SELECT object_uri, search_status, chunk_count FROM objects WHERE connector_id=?",
            (cid,),
        )
        paths = {o["object_uri"] for o in objs}
        host_seg = f"{host}_{port}".replace(
            ":", "_"
        )  # url_to_path lowercases netloc; we'll just substring-check

        # url_to_path canonicalisation: every landed URI starts with /pages/<host>:<port>/...
        # and ends in .md (the connector writes markdown to that virtual path).
        prefix = f"/pages/{host}:{port}/"
        check(
            f"all crawled objects sit under /pages/<host>:<port>/ ({sorted(paths)})",
            all(p.startswith(prefix) and p.endswith(".md") for p in paths),
        )

        # max_pages=3 caps the crawl strictly (4 pages reachable, cap below).
        check(f"max_pages=3 caps the crawl (got {len(paths)} objects)", len(paths) == 3)

        # allowed_domains filter: foreign-host link must NOT appear.
        check(
            f"allowed_domains blocks the external host ({foreign_host})",
            not any(foreign_host in p for p in paths),
        )

        # 404 + 500 paths must NOT be indexed (handler returns non-200, plugin skips).
        check("404 /missing not indexed", not any(p.endswith("/missing.md") for p in paths))
        check("500 /broken not indexed", not any(p.endswith("/broken.md") for p in paths))

        # Index page reached + indexed (search_status='indexed')
        idx_obj = next((o for o in objs if o["object_uri"].endswith("/index.md")), None)
        check(
            "/index.md present and search_status='indexed'",
            idx_obj is not None
            and idx_obj["search_status"] == "indexed"
            and (idx_obj["chunk_count"] or 0) >= 1,
        )

        # --- 2) HTML->md conversion is observable through semantic search ------
        # Each page carries a unique topical sentence; search must hit it on the
        # right URI. This proves markitdown converted the HTML *body* (not stored
        # raw HTML), and embeddings landed.
        async def _hits_for(query: str) -> list[dict]:
            return await eng.search(query, connector_uri=conn_uri, mode="hybrid", top_k=5)

        about_hits = await _hits_for("photosynthesis chlorophyll sunlight")
        about_on = [r for r in about_hits if (r.get("source") or "").endswith("/about.md")]
        docs_hits = await _hits_for("kubernetes containers cluster orchestration")
        docs_on = [r for r in docs_hits if (r.get("source") or "").endswith("/docs.md")]
        # /about and /docs are both reachable in 1 hop from /, so within max_pages=3
        # the BFS visits / + at least these two. Assert at least ONE is searchable
        # under its converted markdown.
        either_reachable = (len(about_on) + len(docs_on)) >= 1
        check(
            f"semantic search hits converted-md page content "
            f"(about={len(about_on)}, docs={len(docs_on)})",
            either_reachable,
        )

        # --- 3) cat on a page returns the markdown (not the raw HTML) ----------
        # Pick the page that actually landed, prefer about or docs.
        cat_target = None
        for o in objs:
            if o["object_uri"].endswith("/about.md") or o["object_uri"].endswith("/docs.md"):
                cat_target = o["object_uri"]
                break
        if cat_target:
            cat_res = await eng.cat(conn_uri + cat_target)
            # `cat` on a regular (non-structured, non-meta) page returns plain str
            body = cat_res if isinstance(cat_res, str) else (cat_res or {}).get("content") or ""
            check(
                f"cat({cat_target}) returns markdown (no <html> tag, has text content)",
                ("<html>" not in body) and len(body.strip()) >= 10,
            )

        # --- 4) ETag/304 incremental: re-sync with no mutation -----------------
        # Snapshot chunk_count by path, re-sync, every existing path's count stays.
        before = {o["object_uri"]: o["chunk_count"] for o in objs}
        await eng.add(conn_uri, config=cfg_web)
        objs2 = await eng.meta.fetchall(
            "SELECT object_uri, chunk_count FROM objects WHERE connector_id=?", (cid,)
        )
        after = {o["object_uri"]: o["chunk_count"] for o in objs2}
        check(
            f"re-sync (304 path): same path set ({len(before)} -> {len(after)})",
            set(before) == set(after),
        )
        check(
            "re-sync (304 path): no chunk_count regression on cached pages",
            all(after.get(k, 0) >= (v or 0) for k, v in before.items()),
        )

        # --- 5) mutate the seed page (new etag) -> next sync picks up the new content
        # The seed ('/') is re-fetched on every sync; a 200 there indexes the new
        # content. This is the simple path — works regardless of the deep-page
        # 304 behaviour.
        seed_marker = "syzygy-orbital-mechanics-marker-12345"
        PAGES["/"] = {
            "html": _page(
                "<h1>Fixture index v2</h1>"
                f"<p>Updated index references {seed_marker} as a fresh topic.</p>"
                "<a href='/about'>About</a> <a href='/docs'>Docs</a>",
                etag="v2-idx",
            ),
            "etag": '"v2-idx"',
        }
        await eng.add(conn_uri, config=cfg_web)
        seed_hits = await eng.search(seed_marker, connector_uri=conn_uri, mode="hybrid", top_k=5)
        seed_with_marker = [r for r in seed_hits if seed_marker in (r.get("content") or "")]
        check(
            f"after etag bump on seed /: new content surfaces in search "
            f"({len(seed_with_marker)} hits contain marker)",
            len(seed_with_marker) >= 1,
        )

        # --- 6) mutate a DEEP page while the seed is now stable -> still surfaces
        # The seed's etag matches what we stored in step 5, so it returns 304 this
        # time. The 304-link-discovery fix means we still re-enqueue the seed's
        # known children, so /about (or /docs) gets re-fetched and the mutation
        # lands. Pick whichever child actually got indexed earlier.
        deep_marker = "boustrophedon-hydraulic-marker-67890"
        deep_path = None
        for cand in ("/about", "/docs"):
            if any(p.endswith(cand + ".md") for p in paths):
                deep_path = cand
                break
        if deep_path:
            PAGES[deep_path] = {
                "html": _page(
                    f"<h1>{deep_path[1:]} v2</h1>"
                    f"<p>This page now references {deep_marker} as a new topic.</p>",
                    etag=f"v2-{deep_path[1:]}",
                ),
                "etag": f'"v2-{deep_path[1:]}"',
            }
            await eng.add(conn_uri, config=cfg_web)
            deep_hits = await eng.search(
                deep_marker, connector_uri=conn_uri, mode="hybrid", top_k=5
            )
            deep_with = [r for r in deep_hits if deep_marker in (r.get("content") or "")]
            check(
                f"after etag bump on deep {deep_path} (seed is 304): "
                f"deep mutation surfaces in search ({len(deep_with)} hits contain marker)",
                len(deep_with) >= 1,
            )
    finally:
        try:
            eng.milvus.drop_collection("default")
        except Exception:
            pass
        await eng.shutdown()
        await runner.cleanup()
        os.system(f"rm -rf '{base}'*")

    passed = sum(results)
    print(f"\n{'=' * 46}\n  web crawler e2e: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
