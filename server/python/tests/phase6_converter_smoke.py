"""Phase 6 converter smoke — pdf/html -> markdown -> chunk -> search + cat. Needs
OPENAI_API_KEY (bash -ic). Lite + Zilliz.
"""
import asyncio
import os
import shutil
import tempfile

from fpdf import FPDF

from mfs_server.config import load_server_config
from mfs_server.engine.engine import Engine

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, cond):
    results.append((name, cond))
    print(f"  [{OK if cond else FAIL}] {name}")
    return cond


def seed() -> str:
    root = tempfile.mkdtemp(prefix="mfs_p6_repo_")
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("helvetica", size=12)
    pdf.multi_cell(0, 8, "Quarterly Financial Report. Revenue grew by 30 percent this fiscal year, "
                          "driven by enterprise subscription expansion and reduced churn.")
    pdf.output(f"{root}/report.pdf")
    open(f"{root}/api.html", "w").write(
        "<html><body><h1>Authentication</h1><p>The API authenticates requests using OAuth 2.0 "
        "bearer tokens passed in the Authorization header.</p></body></html>")
    open(f"{root}/notes.md", "w").write("# Notes\n\nPlain markdown about gardening tomatoes.\n")
    return root


async def run(label: str, uri: str, token: str, repo: str):
    print(f"== Phase 6 converter [{label}] ==")
    base = f"/tmp/mfs_p6_{label}_{os.getpid()}"
    os.system(f"rm -rf '{base}'*")
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_meta.db"
    cfg.milvus.uri = uri
    cfg.milvus.token = token
    cfg.object_store.root = base + "_cache"
    cfg.transformation_cache.db_path = base + "_tx.db"
    eng = Engine(cfg)
    await eng.startup()
    try:
        eng.milvus.drop_collection(cfg.namespace)
        eng.milvus.ensure_collection(cfg.namespace)
        conn_uri = f"file://local{repo}"
        await eng.add(repo)

        conn = await eng.meta.fetchone("SELECT id FROM connectors WHERE type='file'")
        objs = {o["object_uri"]: o for o in await eng.meta.fetchall(
            "SELECT object_uri, chunk_count, search_status FROM objects WHERE connector_id=?", (conn["id"],))}
        check(f"[{label}] report.pdf converted+indexed",
              objs.get("/report.pdf", {}).get("chunk_count", 0) > 0 and objs["/report.pdf"]["search_status"] == "indexed")
        check(f"[{label}] api.html converted+indexed",
              objs.get("/api.html", {}).get("chunk_count", 0) > 0)

        rev = await eng.search("revenue growth enterprise", connector_uri=conn_uri, mode="hybrid", top_k=3)
        check(f"[{label}] search 'revenue' -> report.pdf",
              any("report.pdf" in (e["source"] or "") for e in rev[:2]))

        au = await eng.search("oauth bearer token authentication", connector_uri=conn_uri, mode="hybrid", top_k=3)
        check(f"[{label}] search 'oauth' -> api.html",
              any("api.html" in (e["source"] or "") for e in au[:2]))

        md = await eng.cat(f"{repo}/report.pdf")
        check(f"[{label}] cat report.pdf returns converted markdown", "Revenue" in md or "revenue" in md)
    finally:
        try:
            eng.milvus.drop_collection(cfg.namespace)
        except Exception:
            pass
        await eng.shutdown()
        os.system(f"rm -rf '{base}'*")


async def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set — run via bash -ic")
        raise SystemExit(2)
    repo = seed()
    try:
        await run("Lite", f"/tmp/mfs_p6_lite_{os.getpid()}.db", "", repo)
        if os.environ.get("ZILLIZ_URI") and os.environ.get("ZILLIZ_API_KEY"):
            await run("Zilliz", os.environ["ZILLIZ_URI"], os.environ["ZILLIZ_API_KEY"], repo)
        else:
            print("== Zilliz skipped ==")
    finally:
        shutil.rmtree(repo, ignore_errors=True)
        os.system(f"rm -rf /tmp/mfs_p6_lite_{os.getpid()}.db*")

    passed = sum(1 for _, c in results if c)
    total = len(results)
    print(f"\n{'='*40}\nPhase 6 converter: {passed}/{total} checks passed")
    if passed != total:
        print("FAILED:", [n for n, c in results if not c])
        raise SystemExit(1)
    print("ALL PASS")


if __name__ == "__main__":
    asyncio.run(main())
