"""Phase 14 — pin the text_fields-multi-column -> Milvus.content contract.

Milvus' chunk schema has exactly ONE `content` VARCHAR(65535) field. When a
user configures `text_fields = ["subject", "body"]` (or
`["payload.body", "comments[].body"]`), the framework joins those columns
into a single string via `engine._render_record` BEFORE writing to Milvus.

This test nails down the exact rendering contract:

  A · _render_record direct calls — single-column, multi-column, nested
      JSONPath, missing field skipped, multi-valued array (`[].body`)
      flattened to a bulleted list.
  B · _resolve_path direct calls — scalar nested access vs missing path.
  C · end-to-end via mysql — sync a row with two text_fields and check
      that the actual Milvus row's `content` field equals what
      _render_record produced from the source dict, byte-for-byte. Also
      confirm `locator` and `metadata` stayed in their JSON columns
      (NOT merged into content).
  D · semantic invariant — the dense_vec written to Milvus equals the
      embedding of the rendered content string (same hash, since
      transformation_cache is content-keyed).

Needs OPENAI_API_KEY + mysql at 127.0.0.1:3306 (mfs/mfs/mfstest)."""

import asyncio
import os

from mfs_server.config import load_server_config
from mfs_server.engine.engine import Engine, _render_record, _resolve_path
from mfs_server.storage.ids import cache_key, sha1_hex

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, cond):
    results.append(bool(cond))
    print(f"  [{OK if cond else FAIL}] {name}")
    return cond


async def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set — run via bash -ic")
        raise SystemExit(2)

    # =====================================================
    # A · _render_record contract (direct calls)
    # =====================================================
    print("\n--- A · _render_record contract ---")

    # A.1: single column, scalar value -> "field: value"
    rec = {"subject": "saml sso login loop", "body": "irrelevant"}
    got = _render_record(rec, ["subject"])
    check(
        f"A.1 single column renders as 'field: value' ({got!r})",
        got == "subject: saml sso login loop",
    )

    # A.2: two columns joined with blank line + 'field: value' for each
    got = _render_record(rec, ["subject", "body"])
    check(
        f"A.2 two columns join with '\\n\\n' between them ({got!r})",
        got == "subject: saml sso login loop\n\nbody: irrelevant",
    )

    # A.3: order follows the text_fields list, not the dict iteration order
    got = _render_record(rec, ["body", "subject"])
    check(
        f"A.3 column order follows text_fields list, not rec iteration ({got!r})",
        got == "body: irrelevant\n\nsubject: saml sso login loop",
    )

    # A.4: missing field is silently skipped (no '<empty>' filler)
    got = _render_record({"subject": "only this"}, ["subject", "body", "tag"])
    check(f"A.4 missing field skipped silently ({got!r})", got == "subject: only this")

    # A.5: empty text_fields -> empty string
    got = _render_record(rec, [])
    check(f"A.5 empty text_fields -> '' ({got!r})", got == "")

    # A.6: nested dotted path resolved
    nested = {"payload": {"title": "alpha", "body": "cytochrome oxidase activity"}}
    got = _render_record(nested, ["payload.body"])
    check(
        f"A.6 nested 'payload.body' renders the inner string ({got!r})",
        got == "payload.body: cytochrome oxidase activity",
    )

    # A.7: multi-valued array path -> bulleted list under the field header
    arr = {"comments": [{"body": "first reply"}, {"body": "second reply"}, {"body": "third reply"}]}
    got = _render_record(arr, ["comments[].body"])
    check(
        f"A.7 array path 'comments[].body' flattens to bulleted list ({got!r})",
        got == "comments[].body:\n- first reply\n- second reply\n- third reply",
    )

    # A.8: mix scalar + array
    mixed = {
        "subject": "deploy rollback",
        "comments": [{"body": "started rollback"}, {"body": "rollback complete"}],
    }
    got = _render_record(mixed, ["subject", "comments[].body"])
    check(
        f"A.8 mixed scalar + array preserves order + bullets the array ({got!r})",
        got
        == "subject: deploy rollback\n\ncomments[].body:\n- started rollback\n- rollback complete",
    )

    # =====================================================
    # B · _resolve_path edges
    # =====================================================
    print("\n--- B · _resolve_path edges ---")
    check("B.1 missing key returns None", _resolve_path({"a": 1}, "missing") is None)
    check("B.2 nested missing returns None", _resolve_path({"a": {"b": 1}}, "a.c") is None)
    check("B.3 scalar nested path", _resolve_path({"a": {"b": "x"}}, "a.b") == "x")
    check("B.4 array index path", _resolve_path({"a": [{"b": 1}, {"b": 2}]}, "a[0].b") == 1)
    check(
        "B.5 array wildcard returns list",
        _resolve_path({"a": [{"b": 1}, {"b": 2}]}, "a[*].b") == [1, 2],
    )
    check("B.6 None vs empty-string distinguishable (None -> None)", _resolve_path({}, "a") is None)

    # =====================================================
    # C · end-to-end against mysql: rendered string == Milvus content
    # =====================================================
    print("\n--- C · sync -> Milvus row content matches _render_record ---")
    import aiomysql

    try:
        conn = await aiomysql.connect(
            autocommit=True, host="127.0.0.1", port=3306, user="mfs", password="mfs", db="mfstest"
        )
    except Exception as e:  # noqa: BLE001
        print(f"mysql not reachable: {e}")
        raise SystemExit(2)

    tbl = f"render_t14_{os.getpid()}"
    cur = await conn.cursor()
    await cur.execute(f"DROP TABLE IF EXISTS `{tbl}`")
    await cur.execute(
        f"CREATE TABLE `{tbl}` (id INT PRIMARY KEY, subject TEXT, body TEXT, priority VARCHAR(20))"
    )
    src_row = (
        1,
        "T14 multi-column rendering",
        "We pin the contract that text_fields joins into the single "
        "Milvus content field with a blank-line separator.",
        "high",
    )
    await cur.execute(
        f"INSERT INTO `{tbl}` (id, subject, body, priority) VALUES (%s, %s, %s, %s)", src_row
    )

    base = f"/tmp/mfs_tfrender_{os.getpid()}"
    os.system(f"rm -rf '{base}'*")
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_m.db"
    cfg.milvus.uri = base + "_v.db"
    cfg.milvus.token = ""
    cfg.object_store.root = base + "_c"
    cfg.transformation_cache.db_path = base + "_t.db"
    cfg.summary.enabled = False
    eng = Engine(cfg)
    await eng.startup()

    os.environ["MFS_TEST_MYSQL_PW"] = "mfs"
    cfg_obj = {
        "host": "127.0.0.1",
        "port": 3306,
        "user": "mfs",
        "database": "mfstest",
        "credential_ref": "env:MFS_TEST_MYSQL_PW",
        "objects": [
            {
                "match": f"/{tbl}/rows.jsonl",
                "text_fields": ["subject", "body"],
                "locator_fields": ["id"],
                "metadata_fields": ["priority"],
            }
        ],
    }
    conn_uri = "mysql://render"
    try:
        eng.milvus.drop_collection("default")
        eng.milvus.ensure_collection("default")
        await eng.add(conn_uri, config=cfg_obj)

        full_uri = conn_uri + f"/{tbl}/rows.jsonl"
        chunks = await asyncio.to_thread(
            eng.milvus.get_chunks_by_object, "default", conn_uri, full_uri
        )
        check(f"C.1 exactly one Milvus chunk for the row ({len(chunks)})", len(chunks) == 1)
        chunk = chunks[0]

        # Reconstruct the dict the framework saw at sync time
        src_dict = {"id": 1, "subject": src_row[1], "body": src_row[2], "priority": src_row[3]}
        expected_content = _render_record(src_dict, ["subject", "body"])
        check(
            f"C.2 Milvus.content is the _render_record output (rendered={expected_content!r})",
            chunk["content"] == expected_content,
        )

        # The chunk content explicitly is NOT just the body, NOT just the subject —
        # it carries BOTH columns prefixed with their field names.
        check(
            "C.3 rendered content carries both 'subject:' and 'body:' headers",
            "subject:" in chunk["content"]
            and "body:" in chunk["content"]
            and "\n\n" in chunk["content"],
        )
        check(
            "C.4 priority did NOT bleed into content (it's metadata, not text)",
            "priority" not in chunk["content"],
        )

        # locator + metadata land in their own JSON columns, not concatenated into content
        loc = chunk.get("locator") or {}
        meta = chunk.get("metadata") or {}
        check(f"C.5 locator JSON column = {{'id': 1}} (got {loc})", loc == {"id": 1})
        check(
            f"C.6 metadata JSON column = {{'priority': 'high'}} (got {meta})",
            meta == {"priority": "high"},
        )

        # =====================================================
        # D · the dense_vec is keyed by the rendered string
        # =====================================================
        print("\n--- D · tx_cache key derives from the rendered string ---")
        rendered_key = cache_key(
            sha1_hex(expected_content.encode()),
            "embedding",
            eng.embed.provider,
            eng.embed.model,
            eng.embed.version,
        )
        cached = await eng.tx_cache.batch_get([rendered_key])
        check(
            f"D.1 transformation_cache has an entry under "
            f"cache_key(sha1(rendered_content), 'embedding', ...)",
            cached.get(rendered_key) is not None,
        )

        # If we re-embed the rendered string via the public API, api_calls
        # stays at zero — proving the cache key matches what sync stored.
        eng.embed.api_calls = 0
        await eng.embed.batch_embed([expected_content])
        check(
            f"D.2 re-embedding the rendered string is a cache hit "
            f"(api delta={eng.embed.api_calls})",
            eng.embed.api_calls == 0,
        )

        # And re-embedding JUST one of the constituent columns is a MISS
        # — proving the embedding was definitely on the joined string, not
        # the individual fields.
        eng.embed.api_calls = 0
        await eng.embed.batch_embed([src_row[2]])  # just the body text alone
        check(
            f"D.3 embedding just one column is a fresh miss (api delta={eng.embed.api_calls})",
            eng.embed.api_calls >= 1,
        )
    finally:
        try:
            eng.milvus.drop_collection("default")
        except Exception:
            pass
        await eng.shutdown()
        await cur.execute(f"DROP TABLE IF EXISTS `{tbl}`")
        await cur.close()
        conn.close()
        os.system(f"rm -rf '{base}'*")

    passed = sum(results)
    print(f"\n{'=' * 46}\n  text_fields render e2e: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
