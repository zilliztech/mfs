"""Phase 13 — discord connector live e2e.

The user provisioned a test guild ("zc-test" bot) with a couple of channels,
including a text channel "常规" carrying a handful of seed messages. Tests
the full pipeline: register, enumerate, read, thread-aggregate, search, cat.

Env: DISCORD_BOT_TOKEN, DISCORD_GUILD_ID, OPENAI_API_KEY (bash -ic)."""
import asyncio
import os

from mfs_server.config import load_server_config
from mfs_server.engine.engine import Engine

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, cond):
    results.append(bool(cond)); print(f"  [{OK if cond else FAIL}] {name}"); return cond


async def main():
    for v in ("OPENAI_API_KEY", "DISCORD_BOT_TOKEN", "DISCORD_GUILD_ID"):
        if not os.environ.get(v):
            print(f"{v} not set — run via bash -ic"); raise SystemExit(2)

    base = f"/tmp/mfs_discord_{os.getpid()}"; os.system(f"rm -rf '{base}'*")
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_m.db"; cfg.milvus.uri = base + "_v.db"; cfg.milvus.token = ""
    cfg.object_store.root = base + "_c"; cfg.transformation_cache.db_path = base + "_t.db"
    eng = Engine(cfg)
    await eng.startup()

    conn_uri = "discord://e2e"
    cfg_obj = {
        "credential_ref": "env:DISCORD_BOT_TOKEN",
        "guild_id": os.environ["DISCORD_GUILD_ID"],
    }
    try:
        eng.milvus.drop_collection("default"); eng.milvus.ensure_collection("default")
        await eng.add(conn_uri, config=cfg_obj)

        # 1) connector registered
        crow = await eng.meta.fetchone("SELECT id FROM connectors WHERE type='discord'")
        check("discord connector registered", crow is not None)
        cid = crow["id"]

        # 2) >=1 text channel enumerated (the test guild has one text channel "常规")
        chan_objs = await eng.meta.fetchall(
            "SELECT object_uri, chunk_count, search_status FROM objects "
            "WHERE connector_id=? AND object_uri LIKE '/channels/%/messages.jsonl'", (cid,))
        check(f">=1 text channel indexed (got {len(chan_objs)})", len(chan_objs) >= 1)

        # 3) messages produced at least 1 thread_aggregate chunk
        non_empty = [c for c in chan_objs if (c["chunk_count"] or 0) > 0]
        check(f"channel has >=1 chunk (non-empty: {len(non_empty)} / {len(chan_objs)})",
              len(non_empty) >= 1)

        # 4) cat the channel's messages.jsonl with a small range returns content
        if non_empty:
            sample = non_empty[0]
            full_uri = conn_uri + sample["object_uri"]
            cat_out = await eng.cat(full_uri, range=(0, 4))
            check(f"cat --range 0:4 returns text ({len(cat_out) if isinstance(cat_out, str) else '?'} chars)",
                  isinstance(cat_out, str) and len(cat_out) > 0)

            # 5) search for content the user seeded ("hello" / "compute modes")
            for q in ["hello", "compute modes resource scheduling"]:
                res = await eng.search(q, connector_uri=conn_uri, mode="hybrid", top_k=3)
                on_us = [r for r in res if (r.get("source") or "").startswith(conn_uri)]
                check(f"search {q!r} returns >=1 hit (got {len(on_us)})", len(on_us) >= 1)
    finally:
        try: eng.milvus.drop_collection("default")
        except Exception: pass
        await eng.shutdown()
        os.system(f"rm -rf '{base}'*")

    passed = sum(results)
    print(f"\n{'='*46}\n  discord live: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
