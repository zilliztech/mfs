"""Phase 1 storage smoke test — run directly with `uv run python tests/phase1_storage_smoke.py`.

Verifies: config load, metadata DDL (all tables), object_store, transformation_cache,
chunk_id determinism, and Milvus ensure/upsert/search/count/drop on BOTH Milvus Lite
and Zilliz Cloud. Uses /tmp paths and random vectors (no embedding API needed here).
"""

import asyncio
import os
import random
import time

from mfs_server.config import load_server_config
from mfs_server.storage.ids import cache_key, chunk_id, sha1_hex
from mfs_server.storage.metadata import MetadataStore
from mfs_server.storage.milvus import MilvusStore
from mfs_server.storage.object_store import LocalObjectStore
from mfs_server.storage.transformation_cache import TransformationCache

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, cond):
    results.append((name, cond))
    print(f"  [{OK if cond else FAIL}] {name}")
    return cond


async def test_metadata(cfg):
    print("== metadata DB ==")
    cfg.metadata.path = "/tmp/mfs_test_meta.db"
    if os.path.exists(cfg.metadata.path):
        os.remove(cfg.metadata.path)
    m = MetadataStore(cfg)
    await m.connect()
    await m.init_schema()
    tables = await m.fetchall("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    names = {t["name"] for t in tables}
    expected = {
        "connectors",
        "objects",
        "artifact_cache",
        "connector_jobs",
        "object_tasks",
        "connector_state",
        "watch_grants",
        "file_state",
        "schema_version",
    }
    check("all tables created", expected <= names)
    # objects has new index-status columns
    cols = await m.fetchall("PRAGMA table_info(objects)")
    colnames = {c["name"] for c in cols}
    check(
        "objects has search_status/chunk_count/index_error/indexed_at",
        {"search_status", "chunk_count", "index_error", "indexed_at"} <= colnames,
    )
    # insert + read back a connector
    await m.execute(
        "INSERT INTO connectors (id, root_uri, type, status) VALUES (?,?,?,?)",
        ("c1", "file://client/repo", "file", "active"),
    )
    row = await m.fetchone("SELECT * FROM connectors WHERE id='c1'")
    check("connector insert/read", row is not None and row["type"] == "file")
    await m.close()


async def test_object_store(cfg):
    print("== object store ==")
    cfg.object_store.root = "/tmp/mfs_test_cache"
    os.system(f"rm -rf {cfg.object_store.root}")
    s = LocalObjectStore(cfg)
    p = s.put_artifact("default", "file://c/manual.pdf", "converted_md", b"# Title\nbody")
    check("put_artifact returns path", os.path.exists(p))
    data = s.get_artifact("default", "file://c/manual.pdf", "converted_md")
    check("get_artifact roundtrip", data == b"# Title\nbody")
    # move (rename support)
    s.move_artifacts("default", "file://c/manual.pdf", "file://c/renamed.pdf")
    check(
        "move_artifacts",
        s.get_artifact("default", "file://c/renamed.pdf", "converted_md") == b"# Title\nbody",
    )


async def test_tx_cache(cfg):
    print("== transformation cache ==")
    cfg.transformation_cache.db_path = "/tmp/mfs_test_txcache.db"
    if os.path.exists(cfg.transformation_cache.db_path):
        os.remove(cfg.transformation_cache.db_path)
    c = TransformationCache(cfg)
    await c.connect()
    k = cache_key(sha1_hex(b"hello"), "embedding", "openai", "text-embedding-3-small", "v1")
    await c.batch_put(
        [
            {
                "cache_key": k,
                "kind": "embedding",
                "input_hash": sha1_hex(b"hello"),
                "provider": "openai",
                "model": "text-embedding-3-small",
                "model_version": "v1",
                "output_bytes": b"\x01\x02\x03",
                "output_size": 3,
            }
        ]
    )
    got = await c.batch_get([k, "nonexistent"])
    check("tx cache hit", got[k] == b"\x01\x02\x03")
    check("tx cache miss is None", got["nonexistent"] is None)
    st = await c.stats()
    check("tx cache stats", st["entry_count"] == 1)
    await c.close()


def test_ids():
    print("== chunk_id / cache_key ==")
    a = chunk_id("default", "file://c/repo", "file://c/repo/a.py", "body", None, [1, 50])
    a2 = chunk_id("default", "file://c/repo", "file://c/repo/a.py", "body", None, [1, 50])
    b = chunk_id("default", "file://c/repo", "file://c/repo/a.py", "body", None, [51, 99])
    check("chunk_id deterministic", a == a2)
    check("chunk_id differs by lines", a != b)
    # null-locator/null-lines once-per-object kinds don't collide across kinds
    s1 = chunk_id("default", "x", "x/o", "summary", None, None)
    s2 = chunk_id("default", "x", "x/o", "vlm_description", None, None)
    check("chunk_id differs by kind for null loc/lines", s1 != s2)
    # locator order-independent
    l1 = chunk_id("default", "x", "x/o", "row_text", {"a": 1, "b": 2}, None)
    l2 = chunk_id("default", "x", "x/o", "row_text", {"b": 2, "a": 1}, None)
    check("chunk_id locator key-order stable", l1 == l2)


def test_milvus(cfg, label):
    print(f"== Milvus [{label}] ==")
    store = MilvusStore(cfg)
    try:
        store.connect()
        check(f"[{label}] connect", store.client is not None)
        # clean slate
        store.drop_collection(cfg.namespace)
        name = store.ensure_collection(cfg.namespace)
        check(f"[{label}] ensure_collection -> {name}", store.client.has_collection(name))
        rows = []
        for i in range(5):
            vec = [random.random() for _ in range(cfg.embedding.dim)]
            rows.append(
                {
                    "chunk_id": chunk_id(
                        cfg.namespace,
                        "file://c/repo",
                        f"file://c/repo/f{i}.md",
                        "body",
                        None,
                        [1, 10],
                    ),
                    "namespace_id": cfg.namespace,
                    "connector_uri": "file://c/repo",
                    "object_uri": f"file://c/repo/f{i}.md",
                    "locator": None,
                    "lines": [1, 10],
                    "content": f"document number {i} about milvus vector search and bm25",
                    "dense_vec": vec,
                    "chunk_kind": "body",
                    "metadata": {"i": i},
                    "indexed_at": int(time.time() * 1000),
                }
            )
        store.upsert(cfg.namespace, rows)
        time.sleep(2)
        cnt = store.count(cfg.namespace)
        check(f"[{label}] upsert+count==5", cnt == 5)
        hits = store.search_dense(cfg.namespace, rows[0]["dense_vec"], limit=3)
        check(f"[{label}] dense search returns hits", len(hits) >= 1)
        store.delete_by_object(cfg.namespace, "file://c/repo", "file://c/repo/f0.md")
        time.sleep(2)
        check(f"[{label}] delete_by_object reduces count", store.count(cfg.namespace) == 4)
    finally:
        try:
            store.drop_collection(cfg.namespace)
            check(
                f"[{label}] drop_collection (cleanup)",
                not store.client.has_collection(store.resolve_collection(cfg.namespace)),
            )
        except Exception as e:
            print(f"  cleanup error: {e}")


async def main():
    cfg = load_server_config()
    await test_metadata(cfg)
    await test_object_store(cfg)
    await test_tx_cache(cfg)
    test_ids()

    # Milvus Lite
    lite_cfg = load_server_config(apply_env=False)
    lite_cfg.milvus.uri = "/tmp/mfs_test_milvus_lite.db"
    lite_cfg.milvus.token = ""
    os.system(f"rm -rf '{lite_cfg.milvus.uri}'*")
    test_milvus(lite_cfg, "Lite")

    # Zilliz Cloud (from env)
    if os.environ.get("ZILLIZ_URI") and os.environ.get("ZILLIZ_API_KEY"):
        z_cfg = load_server_config()  # env applied -> milvus.uri/token from ZILLIZ_*
        test_milvus(z_cfg, "Zilliz")
    else:
        print("== Milvus [Zilliz] skipped (no ZILLIZ_URI/ZILLIZ_API_KEY) ==")

    passed = sum(1 for _, c in results if c)
    total = len(results)
    print(f"\n{'=' * 40}\nPhase 1 storage: {passed}/{total} checks passed")
    if passed != total:
        print("FAILED:", [n for n, c in results if not c])
        raise SystemExit(1)
    print("ALL PASS")


if __name__ == "__main__":
    asyncio.run(main())
