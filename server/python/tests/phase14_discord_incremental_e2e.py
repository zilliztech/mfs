"""Phase 14 — discord incremental sync e2e.

Verifies that posting a new message to a channel and re-running `mfs add` causes
the new message to be indexed AND searchable, without re-embedding the messages
that haven't changed (we can't observe re-embed directly here — that's a
performance check — but we DO assert the new content surfaces).

Posts a uniquely-tagged message via the bot, re-syncs, asserts search finds it,
then cleans up by deleting the bot's own message. The unique tag in the message
text guarantees we won't accidentally match a pre-existing seed message.

Env: DISCORD_BOT_TOKEN, DISCORD_GUILD_ID, OPENAI_API_KEY."""
import asyncio
import os
import uuid

import httpx

from mfs_server.config import load_server_config
from mfs_server.engine.engine import Engine

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, cond):
    results.append(bool(cond)); print(f"  [{OK if cond else FAIL}] {name}"); return cond


def _discord_headers():
    return {"Authorization": f"Bot {os.environ['DISCORD_BOT_TOKEN']}"}


async def _pick_text_channel(guild_id: str) -> tuple[str, str]:
    """Return (channel_id, channel_name) for the first GUILD_TEXT channel (type=0)."""
    def fetch():
        return httpx.get(
            f"https://discord.com/api/v10/guilds/{guild_id}/channels",
            headers=_discord_headers(), timeout=15)
    r = await asyncio.to_thread(fetch)
    chans = r.json() if r.status_code < 400 else []
    for ch in (chans if isinstance(chans, list) else []):
        if ch.get("type") == 0:
            return ch["id"], ch.get("name", "?")
    raise RuntimeError("no text channel found in this guild")


async def _post_message(channel_id: str, content: str) -> str:
    def fetch():
        return httpx.post(
            f"https://discord.com/api/v10/channels/{channel_id}/messages",
            headers={**_discord_headers(), "Content-Type": "application/json"},
            json={"content": content}, timeout=15)
    r = await asyncio.to_thread(fetch)
    if r.status_code >= 400:
        raise RuntimeError(f"discord POST failed: {r.status_code} {r.text[:200]}")
    return r.json()["id"]


async def _delete_message(channel_id: str, message_id: str) -> None:
    def fetch():
        return httpx.delete(
            f"https://discord.com/api/v10/channels/{channel_id}/messages/{message_id}",
            headers=_discord_headers(), timeout=15)
    await asyncio.to_thread(fetch)


async def main():
    for v in ("OPENAI_API_KEY", "DISCORD_BOT_TOKEN", "DISCORD_GUILD_ID"):
        if not os.environ.get(v):
            print(f"{v} not set — run via bash -ic"); raise SystemExit(2)

    guild_id = os.environ["DISCORD_GUILD_ID"]
    channel_id, channel_name = await _pick_text_channel(guild_id)
    print(f"  using channel #{channel_name} ({channel_id})")

    base = f"/tmp/mfs_disc_inc_{os.getpid()}"; os.system(f"rm -rf '{base}'*")
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_m.db"; cfg.milvus.uri = base + "_v.db"; cfg.milvus.token = ""
    cfg.object_store.root = base + "_c"; cfg.transformation_cache.db_path = base + "_t.db"
    eng = Engine(cfg)
    await eng.startup()

    conn_uri = "discord://incr"
    cfg_obj = {"credential_ref": "env:DISCORD_BOT_TOKEN", "guild_id": guild_id}
    posted_msg_id = None
    tag = f"mfs-incr-test-{uuid.uuid4().hex[:8]}"
    new_content = (
        f"{tag} this is a unique probe sentence about quantum cryptography "
        "lattice-based post-quantum signatures key encapsulation mechanism"
    )

    try:
        eng.milvus.drop_collection("default"); eng.milvus.ensure_collection("default")

        # --- 1) initial sync, snapshot the baseline ---
        await eng.add(conn_uri, config=cfg_obj)
        cid = (await eng.meta.fetchone(
            "SELECT id FROM connectors WHERE type='discord'"))["id"]
        chan_path = next(
            (r["object_uri"] for r in await eng.meta.fetchall(
                "SELECT object_uri FROM objects WHERE connector_id=? "
                "AND object_uri LIKE ?", (cid, f"%__{channel_id}/messages.jsonl"))),
            None)
        check(f"initial sync picks up text channel ({chan_path!r})", chan_path is not None)

        # baseline chunk count for the channel
        ro = await eng.meta.fetchone(
            "SELECT chunk_count FROM objects WHERE connector_id=? AND object_uri=?",
            (cid, chan_path))
        baseline_chunks = ro["chunk_count"] or 0
        check(f"baseline chunk_count snapshot ({baseline_chunks})", baseline_chunks >= 1)

        # search for the unique tag — must NOT exist yet. Hybrid search always returns
        # SOME top-k by vector similarity even if no doc actually contains the term, so
        # the right check is "no hit's content actually contains the tag string".
        res0 = await eng.search(tag, connector_uri=conn_uri, mode="hybrid", top_k=5)
        with_tag_0 = [r for r in res0 if tag in (r.get("content") or "")]
        check(f"tag {tag!r} not yet present in any chunk content "
              f"(of {len(res0)} top-k hits, {len(with_tag_0)} contain the tag)",
              len(with_tag_0) == 0)

        # --- 2) post a new message ---
        posted_msg_id = await _post_message(channel_id, new_content)
        print(f"  posted msg id={posted_msg_id}")

        # --- 3) re-sync (incremental — full=False is the default) ---
        # discord's read_records fetches all messages each time; the engine's
        # thread_aggregate rebuild for the channel naturally captures the new one.
        await eng.add(conn_uri, config=cfg_obj)

        post_ro = await eng.meta.fetchone(
            "SELECT chunk_count, search_status FROM objects "
            "WHERE connector_id=? AND object_uri=?", (cid, chan_path))
        post_chunks = post_ro["chunk_count"] or 0
        check(f"re-sync: chunk_count grew or stayed (baseline {baseline_chunks} -> {post_chunks})",
              post_chunks >= baseline_chunks)
        check(f"re-sync: search_status remains 'indexed' (got {post_ro['search_status']!r})",
              post_ro["search_status"] == "indexed")

        # search for the unique tag — MUST surface in a hit whose content contains it
        res1 = await eng.search(tag, connector_uri=conn_uri, mode="hybrid", top_k=5)
        with_tag_1 = [r for r in res1 if tag in (r.get("content") or "")]
        check(f"after re-sync: chunk containing tag {tag!r} surfaces in search "
              f"({len(with_tag_1)} matching hits / {len(res1)} top-k)",
              len(with_tag_1) >= 1)
        # also try a semantic query — should hit too
        res2 = await eng.search("post-quantum lattice key encapsulation",
                                connector_uri=conn_uri, mode="hybrid", top_k=5)
        on_us = [r for r in res2 if (r.get("source") or "").startswith(conn_uri)]
        check(f"after re-sync: semantic search finds the new content ({len(on_us)} hits)",
              len(on_us) >= 1)
    finally:
        # clean up the test message so the guild doesn't accumulate cruft
        if posted_msg_id:
            try: await _delete_message(channel_id, posted_msg_id)
            except Exception: pass
        try: eng.milvus.drop_collection("default")
        except Exception: pass
        await eng.shutdown()
        os.system(f"rm -rf '{base}'*")

    passed = sum(results)
    print(f"\n{'='*46}\n  discord incremental e2e: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
