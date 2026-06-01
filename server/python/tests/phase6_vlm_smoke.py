"""Phase 6 VLM smoke — image -> description -> chunk -> search + cat. Needs
OPENAI_API_KEY (bash -ic). Lite + Zilliz.
"""

import asyncio
import os
import shutil
import tempfile

from PIL import Image, ImageDraw

from mfs_server.config import load_server_config
from mfs_server.engine.engine import Engine

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, cond):
    results.append((name, cond))
    print(f"  [{OK if cond else FAIL}] {name}")
    return cond


def seed() -> str:
    root = tempfile.mkdtemp(prefix="mfs_p6v_repo_")
    img = Image.new("RGB", (480, 140), "white")
    d = ImageDraw.Draw(img)
    d.text((20, 30), "INVOICE  #4471", fill="black")
    d.text((20, 60), "Total Amount Due: 5000 USD", fill="black")
    d.text((20, 90), "Vendor: Acme Corporation", fill="black")
    img.save(f"{root}/invoice.png")
    open(f"{root}/notes.md", "w").write("# Notes\n\nUnrelated text about hiking trails.\n")
    return root


async def run(label: str, uri: str, token: str, repo: str):
    print(f"== Phase 6 VLM [{label}] ==")
    base = f"/tmp/mfs_p6v_{label}_{os.getpid()}"
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
        o = await eng.meta.fetchone(
            "SELECT chunk_count, search_status FROM objects WHERE connector_id=? AND object_uri=?",
            (conn["id"], "/invoice.png"),
        )
        check(
            f"[{label}] invoice.png VLM-indexed",
            o and o["chunk_count"] == 1 and o["search_status"] == "indexed",
        )

        res = await eng.search(
            "invoice total amount due vendor", connector_uri=conn_uri, mode="hybrid", top_k=3
        )
        check(
            f"[{label}] search invoice -> invoice.png",
            any("invoice.png" in (e["source"] or "") for e in res[:2]),
        )
        if res:
            check(
                f"[{label}] hit chunk_kind=vlm_description",
                any(
                    e["metadata"]["chunk_kind"] == "vlm_description"
                    for e in res
                    if "invoice.png" in (e["source"] or "")
                ),
            )

        desc = await eng.cat(f"{repo}/invoice.png")
        check(
            f"[{label}] cat invoice.png returns VLM description",
            "invoice" in desc.lower() or "5000" in desc or "acme" in desc.lower(),
        )
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
        await run("Lite", f"/tmp/mfs_p6v_lite_{os.getpid()}.db", "", repo)
        if os.environ.get("ZILLIZ_URI") and os.environ.get("ZILLIZ_API_KEY"):
            await run("Zilliz", os.environ["ZILLIZ_URI"], os.environ["ZILLIZ_API_KEY"], repo)
        else:
            print("== Zilliz skipped ==")
    finally:
        shutil.rmtree(repo, ignore_errors=True)
        os.system(f"rm -rf /tmp/mfs_p6v_lite_{os.getpid()}.db*")

    passed = sum(1 for _, c in results if c)
    total = len(results)
    print(f"\n{'=' * 40}\nPhase 6 VLM: {passed}/{total} checks passed")
    if passed != total:
        print("FAILED:", [n for n, c in results if not c])
        raise SystemExit(1)
    print("ALL PASS")


if __name__ == "__main__":
    asyncio.run(main())
