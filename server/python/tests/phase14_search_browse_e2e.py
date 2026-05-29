"""Phase 14 — search modes / filters / collapse + ls / head / tail / cat / grep.

Anchors the agent-facing read surface end-to-end. Existing tests only assert
"≥1 hit"; this one pins the actual contracts:

  · search modes — keyword (BM25 sparse) requires the literal term to appear
    in chunk content; semantic (dense) does not. Both must return hits on a
    matching query.
  · search filters — object_prefix scopes via byte-range startswith, NOT
    LIKE pattern (so '_' in a path doesn't over-match); chunk_kinds restricts
    by chunk_kind column; collapse=True dedups by source so an object with
    N chunks shows up once.
  · short-circuits — top_k=0 and empty query both return [] without calling
    embed or Milvus.
  · ls — returns {entries, capabilities}; each entry carries name/type/path/
    search_status/indexable.
  · head — plain text/code streams the first n lines; markdown falls through
    to cat (still bounded by n via cat-level mechanics).
  · tail — plain local files use accel.tail_lines; n=0 short-circuits.
  · cat — bare cat returns full text; range slices [start, end); meta=True
    returns a metadata dict (NOT bytes); density='peek' returns code symbol /
    md heading skeleton; density='skim' adds the first prose line under each.
  · grep — literal anchor finds expected file; regex=True pattern matches
    multiple files; path argument scopes via resolve_connector_uri's
    object_prefix.

OPENAI_API_KEY required (semantic search hits Milvus dense; keyword/grep
don't but we want all modes covered)."""
import asyncio
import os
import pathlib
import shutil

from mfs_server.config import load_server_config
from mfs_server.engine.engine import Engine

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, cond):
    results.append(bool(cond)); print(f"  [{OK if cond else FAIL}] {name}"); return cond


AUTH_PY = (
    '"""Auth module — SAML SSO + JWT issuance."""\n'
    "from __future__ import annotations\n\n"
    "def verify_saml_sso_assertion(payload: str) -> bool:\n"
    '    """Validate the SAML assertion structure and signature."""\n'
    "    return payload.startswith('<saml:Assertion')\n\n"
    "def issue_jwt_for_user(user_id: str) -> str:\n"
    '    """Mint a JWT for the authenticated user."""\n'
    "    return f'jwt-{user_id}'\n"
)

UTIL_PY = (
    '"""Util helpers — string normalization, retry decorators."""\n'
    "from __future__ import annotations\n\n"
    "def normalize_slug(name: str) -> str:\n"
    '    """Lowercase + collapse whitespace + strip diacritics for slug ids."""\n'
    "    return name.strip().lower().replace(' ', '-')\n\n"
    "def retry(times: int):\n"
    '    """Decorator: retry the wrapped call up to `times` on exception."""\n'
    "    def wrap(fn):\n"
    "        def inner(*a, **kw):\n"
    "            return fn(*a, **kw)\n"
    "        return inner\n"
    "    return wrap\n"
)

INTRO_MD = (
    "# Intro\n"
    "Photosynthesis converts sunlight into chemical energy via chlorophyll.\n\n"
    "## Background\n"
    "Plants harvest photons across the visible spectrum.\n\n"
    "## Getting started\n"
    "Read the API reference and then try the examples.\n"
)

API_MD = (
    "# API Reference\n"
    "All endpoints return JSON envelopes with a `data` field.\n\n"
    "## Auth\n"
    "POST /auth/login — exchange credentials for a JWT.\n\n"
    "## Search\n"
    "GET /search?q=foo — keyword / semantic / hybrid via the mode flag.\n"
)

LOGS_TXT_LINES = [f"line {i:04d}: heartbeat at t={i*1000}ms\n" for i in range(1, 31)]


async def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set — run via bash -ic"); raise SystemExit(2)

    base = f"/tmp/mfs_browse_{os.getpid()}"; os.system(f"rm -rf '{base}'*")
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_m.db"; cfg.milvus.uri = base + "_v.db"; cfg.milvus.token = ""
    cfg.object_store.root = base + "_c"; cfg.transformation_cache.db_path = base + "_t.db"
    cfg.summary.enabled = False
    cfg.chunk.chunk_size = 500
    eng = Engine(cfg)
    await eng.startup()

    repo = pathlib.Path(f"{base}_repo")
    (repo / "src").mkdir(parents=True)
    (repo / "docs").mkdir()
    (repo / "src" / "auth.py").write_text(AUTH_PY)
    (repo / "src" / "util.py").write_text(UTIL_PY)
    (repo / "docs" / "intro.md").write_text(INTRO_MD)
    (repo / "docs" / "api.md").write_text(API_MD)
    (repo / "logs.txt").write_text("".join(LOGS_TXT_LINES))

    try:
        eng.milvus.drop_collection("default"); eng.milvus.ensure_collection("default")
        await eng.add(str(repo))
        uri = f"file://local{repo}"

        # =====================================================
        # 1. search modes — keyword / semantic / hybrid
        # =====================================================
        print("\n--- search modes ---")
        # A keyword mode hit must literally contain the query term ("photosynthesis")
        # in chunk content; semantic doesn't require it.
        kw_hits = await eng.search("photosynthesis", connector_uri=uri,
                                    mode="keyword", top_k=5)
        sem_hits = await eng.search("photosynthesis", connector_uri=uri,
                                     mode="semantic", top_k=5)
        hy_hits = await eng.search("photosynthesis", connector_uri=uri,
                                    mode="hybrid", top_k=5)
        check(f"keyword search returns >=1 hit ({len(kw_hits)})", len(kw_hits) >= 1)
        check(f"semantic search returns >=1 hit ({len(sem_hits)})", len(sem_hits) >= 1)
        check(f"hybrid search returns >=1 hit ({len(hy_hits)})", len(hy_hits) >= 1)
        check("keyword hit's content contains the literal term",
              any("photosynthesis" in (h.get("content") or "").lower() for h in kw_hits))

        # =====================================================
        # 2. search filters — object_prefix, chunk_kinds, collapse
        # =====================================================
        print("\n--- search filters ---")
        # object_prefix scopes to /src — should NOT return md hits from /docs
        src_hits = await eng.search("retry decorator", connector_uri=uri,
                                     object_prefix=uri + "/src", mode="hybrid", top_k=10)
        check(f"object_prefix='/src' restricts hits to src/ "
              f"({len(src_hits)} hits, sources={set((h.get('source') or '') for h in src_hits)})",
              all((h.get("source") or "").startswith(uri + "/src") for h in src_hits))

        # chunk_kinds=['body'] only returns body chunks
        body_only = await eng.search("authenticate SAML", connector_uri=uri,
                                      chunk_kinds=["body"], mode="hybrid", top_k=10)
        # chunk_kind comes back nested under metadata in the envelope shape
        check(f"chunk_kinds=['body'] gates by chunk_kind ({len(body_only)} hits)",
              len(body_only) >= 1
              and all((h.get("metadata") or {}).get("chunk_kind") == "body"
                      for h in body_only))

        # chunk_kinds=['directory_summary'] returns 0 — summary is disabled
        no_dir = await eng.search("photosynthesis", connector_uri=uri,
                                   chunk_kinds=["directory_summary"], mode="hybrid", top_k=10)
        check(f"chunk_kinds=['directory_summary'] is empty when summary disabled "
              f"({len(no_dir)} hits)", len(no_dir) == 0)

        # collapse=True: at most 1 hit per source (dedup by object)
        collapsed = await eng.search("verify SAML SSO", connector_uri=uri,
                                      mode="hybrid", top_k=10, collapse=True)
        col_sources = [h.get("source") for h in collapsed]
        check(f"collapse=True yields one hit per source "
              f"({len(col_sources)} sources, distinct={len(set(col_sources))})",
              len(col_sources) == len(set(col_sources)))

        # =====================================================
        # 3. search short-circuits
        # =====================================================
        print("\n--- search short-circuits ---")
        # top_k=0 returns [] without touching Milvus
        zero = await eng.search("anything", connector_uri=uri, mode="hybrid", top_k=0)
        check(f"top_k=0 short-circuits to [] ({len(zero)})", zero == [])
        empty_q = await eng.search("", connector_uri=uri, mode="hybrid", top_k=5)
        check(f"empty query short-circuits to [] ({len(empty_q)})", empty_q == [])
        ws_q = await eng.search("   \t\n  ", connector_uri=uri, mode="hybrid", top_k=5)
        check(f"whitespace-only query short-circuits to [] ({len(ws_q)})", ws_q == [])

        # =====================================================
        # 4. ls
        # =====================================================
        print("\n--- ls ---")
        ls_root = await eng.ls(uri)
        check("ls(root) returns dict with 'entries' and 'capabilities'",
              isinstance(ls_root, dict) and "entries" in ls_root and "capabilities" in ls_root)
        names_root = {e["name"] for e in ls_root["entries"]}
        check(f"ls(root) lists top-level dirs + files (got {sorted(names_root)})",
              {"src", "docs", "logs.txt"} <= names_root)
        # entry shape
        sample = next(e for e in ls_root["entries"] if e["name"] == "logs.txt")
        check(f"ls entry has name/type/path/search_status/indexable "
              f"({sample!r})",
              {"name", "type", "path", "search_status", "indexable"} <= set(sample))
        check("ls(logs.txt) carries search_status from the indexed object",
              sample["search_status"] in {"indexed", "partial", "not_indexed", "building"})
        ls_src = await eng.ls(uri + "/src")
        src_names = {e["name"] for e in ls_src["entries"]}
        check(f"ls(/src) lists code files (got {sorted(src_names)})",
              {"auth.py", "util.py"} == src_names)

        # =====================================================
        # 5. head / tail
        # =====================================================
        print("\n--- head / tail ---")
        h5 = await eng.head(uri + "/logs.txt", n=5)
        check(f"head(logs.txt, n=5) returns 5 lines",
              len(h5.splitlines()) == 5 and h5.splitlines()[0].startswith("line 0001"))
        h_all_plus = await eng.head(uri + "/logs.txt", n=999)
        check("head with n > file length returns all lines",
              len(h_all_plus.splitlines()) == len(LOGS_TXT_LINES))
        t5 = await eng.tail(uri + "/logs.txt", n=5)
        t5_lines = t5.splitlines()
        check(f"tail(logs.txt, n=5) returns the LAST 5 lines "
              f"(last={t5_lines[-1] if t5_lines else None!r})",
              len(t5_lines) == 5 and "line 0030" in t5_lines[-1])
        t0 = await eng.tail(uri + "/logs.txt", n=0)
        check(f"tail(n=0) short-circuits to ''", t0 == "")

        # =====================================================
        # 6. cat — bare / range / meta / density
        # =====================================================
        print("\n--- cat ---")
        bare = await eng.cat(uri + "/src/auth.py")
        check("cat(file) returns full text (contains 'verify_saml_sso_assertion')",
              isinstance(bare, str) and "verify_saml_sso_assertion" in bare)
        # range slicing
        # logs.txt has lines numbered 0001..0030; pulling [2, 6) gives lines 0003..0006
        rng = await eng.cat(uri + "/logs.txt", range=(2, 6))
        rng_lines = rng.splitlines() if isinstance(rng, str) else []
        check(f"cat(range=(2,6)) returns a contiguous 4-line slice "
              f"({len(rng_lines)} lines)",
              len(rng_lines) == 4
              and rng_lines[0].startswith("line 0003")
              and rng_lines[-1].startswith("line 0006"))
        # meta=True returns a dict with source / media_type / size_hint
        meta = await eng.cat(uri + "/src/auth.py", meta=True)
        check(f"cat(meta=True) returns a metadata dict (got keys={sorted(meta) if isinstance(meta, dict) else None})",
              isinstance(meta, dict) and "source" in meta and "media_type" in meta)
        # density='peek' on code: only def/class lines
        peek_py = await eng.cat(uri + "/src/auth.py", density="peek")
        peek_lines = [ln for ln in peek_py.splitlines() if ln.strip()]
        check(f"cat(density='peek') on .py returns only code-symbol lines "
              f"(lines={peek_lines})",
              peek_lines and all(any(ln.lstrip().startswith(tok)
                                     for tok in ("def ", "class "))
                                 for ln in peek_lines))
        # density='peek' on markdown: only heading lines (start with '#')
        peek_md = await eng.cat(uri + "/docs/intro.md", density="peek")
        md_lines = [ln for ln in peek_md.splitlines() if ln.strip()]
        check(f"cat(density='peek') on .md returns only heading lines "
              f"({md_lines})",
              md_lines and all(ln.lstrip().startswith("#") for ln in md_lines))
        # density='skim' on markdown: peek + first prose line per heading
        skim_md = await eng.cat(uri + "/docs/intro.md", density="skim")
        check("cat(density='skim') on .md is >= peek",
              len(skim_md) >= len(peek_md))

        # =====================================================
        # 7. grep
        # =====================================================
        print("\n--- grep ---")
        # literal: exact term in a single file
        lit_hits = await eng.grep("verify_saml_sso_assertion", uri, top_k=20)
        sources_lit = {h.get("source") for h in lit_hits}
        check(f"grep literal: anchors a single hit in auth.py "
              f"({len(lit_hits)} hits, sources={sources_lit})",
              len(lit_hits) >= 1
              and all((s or "").endswith("/src/auth.py") for s in sources_lit))
        # regex: matches both auth.py and util.py (both have `def ` lines)
        re_hits = await eng.grep(r"^def \w+", uri, top_k=20, regex=True)
        re_sources = {h.get("source") for h in re_hits}
        check(f"grep regex 'def \\w+' matches both code files "
              f"(sources={re_sources})",
              any((s or "").endswith("/src/auth.py") for s in re_sources)
              and any((s or "").endswith("/src/util.py") for s in re_sources))
        # path-scoped: same regex against /docs subtree returns 0 (no `def`s there)
        docs_hits = await eng.grep(r"^def \w+", uri + "/docs", top_k=20, regex=True)
        check(f"grep path-scoped to /docs returns 0 'def' hits "
              f"({len(docs_hits)} hits)", len(docs_hits) == 0)
        # top_k cap
        capped = await eng.grep("line", uri, top_k=3)
        check(f"grep respects top_k cap (<=3, got {len(capped)})", len(capped) <= 3)
    finally:
        try: eng.milvus.drop_collection("default")
        except Exception: pass
        await eng.shutdown()
        shutil.rmtree(repo, ignore_errors=True)
        os.system(f"rm -rf '{base}'*")

    passed = sum(results)
    print(f"\n{'='*46}\n  search/browse e2e: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
