"""Phase 13 — directory summary x images (matrix H4 / R4.4).

An image-only subdirectory yields a directory_summary ONLY when include_image_desc is on
(its VLM description is fed into the directory input); off -> empty input -> no summary.
Needs OPENAI_API_KEY (embedding + VLM). Lite.
"""
import asyncio
import os
import shutil
import tempfile

from mfs_server.config import load_server_config
from mfs_server.engine.engine import Engine

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, cond):
    results.append(bool(cond)); print(f"  [{OK if cond else FAIL}] {name}"); return cond


def _make_tree():
    from PIL import Image, ImageDraw
    root = tempfile.mkdtemp(prefix="mfs_imgsum_")
    os.makedirs(f"{root}/pics", exist_ok=True)
    open(f"{root}/top.md", "w").write("# Project\n\nTop-level readme.\n")
    img = Image.new("RGB", (128, 96), "white")
    d = ImageDraw.Draw(img)
    d.rectangle([10, 10, 118, 86], outline="black", width=3)
    d.line([10, 86, 118, 10], fill="red", width=5)
    d.text((20, 40), "REVENUE CHART", fill="blue")
    img.save(f"{root}/pics/chart.png")
    return root


async def _has_dirsum(eng, conn_uri, rel):
    rows = await asyncio.to_thread(eng.milvus.get_chunks_by_object, "default", conn_uri, conn_uri + rel)
    return any(r.get("chunk_kind") == "directory_summary" for r in rows)


async def run_case(label, include_image_desc, expect_pics_summary):
    root = _make_tree()
    base = f"/tmp/mfs_imgsum_{os.getpid()}_{label}"; os.system(f"rm -rf '{base}'*")
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_m.db"; cfg.milvus.uri = base + "_v.db"; cfg.milvus.token = ""
    cfg.object_store.root = base + "_c"; cfg.transformation_cache.db_path = base + "_t.db"
    cfg.summary.enabled = True; cfg.summary.include_image_desc = include_image_desc
    eng = Engine(cfg); await eng.startup()
    conn_uri = f"file://local{root}"
    try:
        eng.milvus.drop_collection("default"); eng.milvus.ensure_collection("default")
        await eng.add(root)
        has = await _has_dirsum(eng, conn_uri, "/pics")
        check(f"{label}: image-only /pics directory_summary == {expect_pics_summary}", has == expect_pics_summary)
    finally:
        try: eng.milvus.drop_collection("default")
        except Exception: pass
        await eng.shutdown(); shutil.rmtree(root, ignore_errors=True); os.system(f"rm -rf '{base}'*")


async def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set — run via bash -ic"); raise SystemExit(2)
    await run_case("off", include_image_desc=False, expect_pics_summary=False)
    await run_case("on", include_image_desc=True, expect_pics_summary=True)
    passed = sum(results)
    print(f"\n{'='*46}\n  image x directory summary: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
