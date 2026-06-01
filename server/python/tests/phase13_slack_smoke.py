"""Phase 13 — slack connector live e2e (BOT + USER token modes).

The slack plugin doesn't care whether the token is a Bot OAuth Token (xoxb-)
or a User OAuth Token (xoxp-) — only the scopes differ. We exercise both:

  - BOT mode: just verifies auth + that the connector registers cleanly. The
    user's bot has not been invited to any channel, so 0 channels is expected
    and we don't try to index anything.

  - USER mode: indexes a few public channels in the user's workspace BUT under
    a strict cap (max_read_rows=30) so we only touch a small slice of
    real-company data. We verify the pipeline (registered, >=1 channel object,
    >=1 thread chunk produced, search returns at least one hit on this
    connector) WITHOUT printing message content.

Both modes share the same plugin code path; this test mostly proves the
auth/token plumbing works against live Slack.

Env: SLACK_BOT_TOKEN, SLACK_USER_TOKEN, OPENAI_API_KEY (bash -ic)."""

import asyncio
import os

from mfs_server.config import load_server_config
from mfs_server.engine.engine import Engine

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, cond):
    results.append(bool(cond))
    print(f"  [{OK if cond else FAIL}] {name}")
    return cond


async def _drive(eng, conn_uri, cfg_obj, *, label, expect_chunks):
    """Common: register + sync + structural assertions. expect_chunks=True for
    user mode (data available), False for bot mode (no channels)."""
    await eng.add(conn_uri, config=cfg_obj)

    crow = await eng.meta.fetchone("SELECT id FROM connectors WHERE root_uri=?", (conn_uri,))
    check(f"[{label}] slack connector registered", crow is not None)
    if not crow:
        return
    cid = crow["id"]

    chan_objs = await eng.meta.fetchall(
        "SELECT object_uri, chunk_count FROM objects "
        "WHERE connector_id=? AND object_uri LIKE '/channels/%/messages.jsonl' "
        "ORDER BY object_uri",
        (cid,),
    )
    if expect_chunks:
        check(f"[{label}] >=1 channel enumerated (got {len(chan_objs)})", len(chan_objs) >= 1)
        with_chunks = [c for c in chan_objs if (c["chunk_count"] or 0) > 0]
        check(
            f"[{label}] at least one channel has >=1 thread_aggregate chunk "
            f"(got {len(with_chunks)} non-empty / {len(chan_objs)} total)",
            len(with_chunks) >= 1,
        )

        # search must return at least 1 hit on this connector (without inspecting content)
        res = await eng.search("project meeting", connector_uri=conn_uri, mode="hybrid", top_k=3)
        on_us = [r for r in res if (r.get("source") or "").startswith(conn_uri)]
        check(f"[{label}] search returns >=1 hit (got {len(on_us)})", len(on_us) >= 1)
    else:
        check(
            f"[{label}] bot in 0 channels (expected — bot not invited anywhere)",
            len(chan_objs) == 0,
        )


async def main():
    for v in ("OPENAI_API_KEY", "SLACK_BOT_TOKEN", "SLACK_USER_TOKEN"):
        if not os.environ.get(v):
            print(f"{v} not set — run via bash -ic")
            raise SystemExit(2)

    base = f"/tmp/mfs_slack_{os.getpid()}"
    os.system(f"rm -rf '{base}'*")
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_m.db"
    cfg.milvus.uri = base + "_v.db"
    cfg.milvus.token = ""
    cfg.object_store.root = base + "_c"
    cfg.transformation_cache.db_path = base + "_t.db"
    eng = Engine(cfg)
    await eng.startup()

    try:
        eng.milvus.drop_collection("default")
        eng.milvus.ensure_collection("default")

        # ----- BOT mode -----
        await _drive(
            eng,
            "slack://bot-e2e",
            {"credential_ref": "env:SLACK_BOT_TOKEN"},
            label="BOT",
            expect_chunks=False,
        )

        # ----- USER mode (strict cap: don't suck in the company workspace) -----
        await _drive(
            eng,
            "slack://user-e2e",
            {
                "credential_ref": "env:SLACK_USER_TOKEN",
                "channel_types": "public_channel",
                "max_read_rows": 30,
            },
            label="USER",
            expect_chunks=True,
        )
    finally:
        try:
            eng.milvus.drop_collection("default")
        except Exception:
            pass
        await eng.shutdown()
        os.system(f"rm -rf '{base}'*")

    passed = sum(results)
    print(f"\n{'=' * 46}\n  slack live: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
