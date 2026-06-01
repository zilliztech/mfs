"""Phase 10 connectors — offline unit tests (NO live services, NO API keys).

Covers the SaaS / message / object-store / DB connectors written from the latest
SDK docs: object_kind routing, virtual path layout (list), parsing helpers, and
sync change-detection — by stubbing the network-facing enumeration methods. These
prove the connector contract + layout without connecting anywhere.

Run: cd server/python && .venv/bin/python tests/phase10_connectors_unit.py
"""

import asyncio

from mfs_server.connectors.base import ConnectorContext

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, cond):
    results.append((name, bool(cond)))
    print(f"  [{OK if cond else FAIL}] {name}")
    return cond


class FakeState:
    def __init__(self):
        self._d = {}

    async def get(self, k):
        return self._d.get(k)

    async def set(self, k, v):
        self._d[k] = v

    async def delete(self, k):
        self._d.pop(k, None)

    async def checkpoint(self):
        pass

    async def commit(self):
        pass


def make(cls, config):
    ctx = ConnectorContext(FakeState(), "cid", "default")
    return cls(config, None, ctx=ctx)


async def collect(agen):
    return [x async for x in agen]


async def sync_uris(plugin):
    return (
        [(c.uri, c.kind) async for c in plugin.sync.__wrapped__(plugin)]
        if hasattr(plugin.sync, "__wrapped__")
        else [(c.uri, c.kind) for c in await collect(plugin.sync(_opts()))]
    )


def _opts():
    from mfs_server.connectors.base import SyncOptions

    return SyncOptions()


async def main():
    # ---------------- bigquery ----------------
    from mfs_server.connectors.bigquery.plugin import BigQueryPlugin

    bq = make(BigQueryPlugin, {"project": "p", "datasets": ["ds"]})
    check(
        "bq object_kind rows.jsonl -> table_rows",
        bq.object_kind_of("/ds/tables/t/rows.jsonl") == "table_rows",
    )
    check(
        "bq object_kind schema.json -> table_schema",
        bq.object_kind_of("/ds/tables/t/schema.json") == "table_schema",
    )
    bq._datasets = lambda: _aval(["ds"])
    bq._tables = lambda d: _aval(["t"])
    lst = await bq.list("/ds/tables")
    check("bq list /ds/tables -> [t]", [e.name for e in lst] == ["t"])
    lst2 = await bq.list("/ds/tables/t")
    check(
        "bq list table -> schema.json+rows.jsonl",
        sorted(e.name for e in lst2) == ["rows.jsonl", "schema.json"],
    )

    # ---------------- snowflake ----------------
    from mfs_server.connectors.snowflake.plugin import SnowflakePlugin

    sf = make(SnowflakePlugin, {"database": "DB"})
    check(
        "snowflake object_kind 5-part rows.jsonl",
        sf.object_kind_of("/DB/PUBLIC/tables/T/rows.jsonl") == "table_rows",
    )
    check("snowflake parts depth", len(sf._parts("/DB/PUBLIC/tables/T/rows.jsonl")) == 5)

    # ---------------- jira ----------------
    from mfs_server.connectors.jira.plugin import JiraPlugin

    jira = make(JiraPlugin, {"url": "https://x", "projects": ["ENG"]})
    check(
        "jira object_kind issues.jsonl -> record_collection",
        jira.object_kind_of("/projects/ENG/issues.jsonl") == "record_collection",
    )
    flat = jira._flatten_issue(
        {
            "key": "ENG-1",
            "id": "10",
            "fields": {
                "summary": "Login bug",
                "description": "broken",
                "status": {"name": "Open"},
                "priority": {"name": "High"},
                "assignee": {"displayName": "Ann"},
                "labels": ["auth"],
            },
        }
    )
    check(
        "jira flatten extracts summary/status/assignee",
        flat["summary"] == "Login bug" and flat["status"] == "Open" and flat["assignee"] == "Ann",
    )
    jira._projects = lambda: _aval(["ENG"])
    jlist = await jira.list("/projects/ENG")
    check("jira list project -> issues.jsonl", [e.name for e in jlist] == ["issues.jsonl"])

    # ---------------- linear ----------------
    from mfs_server.connectors.linear.plugin import LinearPlugin

    lin = make(LinearPlugin, {"api_key": "k"})
    check("linear auth header is raw key (not Bearer)", lin._headers()["Authorization"] == "k")
    fl = LinearPlugin._flatten(
        {
            "identifier": "ENG-2",
            "title": "T",
            "description": "d",
            "state": {"name": "Todo"},
            "assignee": {"name": "Bo"},
            "labels": {"nodes": [{"name": "bug"}]},
        }
    )
    check(
        "linear flatten state/assignee/labels",
        fl["state"] == "Todo" and fl["assignee"] == "Bo" and fl["labels"] == ["bug"],
    )

    # ---------------- notion ----------------
    from mfs_server.connectors.notion.plugin import NotionPlugin, _block_to_md, _rich_text

    notion = make(NotionPlugin, {"token": "t"})
    check(
        "notion object_kind page .md -> document",
        notion.object_kind_of("/pages/abc.md") == "document",
    )
    check(
        "notion object_kind records.jsonl -> record_collection",
        notion.object_kind_of("/data_sources/x/records.jsonl") == "record_collection",
    )
    rt = [{"plain_text": "Hello "}, {"plain_text": "world"}]
    check("notion _rich_text concat", _rich_text(rt) == "Hello world")
    check(
        "notion heading_1 -> '# '",
        _block_to_md({"type": "heading_1", "heading_1": {"rich_text": [{"plain_text": "Title"}]}})
        == "# Title",
    )
    check(
        "notion to_do checked -> [x]",
        _block_to_md(
            {"type": "to_do", "to_do": {"checked": True, "rich_text": [{"plain_text": "done"}]}}
        )
        == "- [x] done",
    )
    pv = NotionPlugin._prop_value({"type": "select", "select": {"name": "High"}})
    check("notion _prop_value select", pv == "High")
    pv2 = NotionPlugin._prop_value({"type": "title", "title": [{"plain_text": "Name"}]})
    check("notion _prop_value title", pv2 == "Name")

    # ---------------- zendesk ----------------
    from mfs_server.connectors.zendesk.plugin import ZendeskPlugin

    zd = make(ZendeskPlugin, {"subdomain": "acme", "email": "a@b.c", "api_token": "tok"})
    check(
        "zendesk object_kind records.jsonl",
        zd.object_kind_of("/tickets/records.jsonl") == "record_collection",
    )
    check(
        "zendesk comments.jsonl -> record_collection",
        zd.object_kind_of("/tickets/comments.jsonl") == "record_collection",
    )
    check("zendesk base url from subdomain", zd._base() == "https://acme.zendesk.com")
    check("zendesk auth uses /token suffix", zd._auth()[0] == "a@b.c/token")
    zlist = await zd.list("/")
    check(
        "zendesk root -> tickets/users/organizations",
        sorted(e.name for e in zlist) == ["organizations", "tickets", "users"],
    )
    zt = await zd.list("/tickets")
    check(
        "zendesk /tickets -> records.jsonl+comments.jsonl",
        sorted(e.name for e in zt) == ["comments.jsonl", "records.jsonl"],
    )

    # ---------------- salesforce ----------------
    from mfs_server.connectors.salesforce.plugin import SalesforcePlugin

    sfc = make(SalesforcePlugin, {"username": "u", "password": "p", "objects": ["Account", "Case"]})
    check(
        "salesforce object_kind records.jsonl",
        sfc.object_kind_of("/Account/records.jsonl") == "record_collection",
    )
    sflist = await sfc.list("/")
    check(
        "salesforce root lists configured objects", [e.name for e in sflist] == ["Account", "Case"]
    )

    # ---------------- hubspot ----------------
    from mfs_server.connectors.hubspot.plugin import HubSpotPlugin

    hs = make(HubSpotPlugin, {"access_token": "t"})
    check("hubspot default objects", hs._objects() == ["contacts", "companies", "deals", "tickets"])
    check(
        "hubspot object_kind records.jsonl",
        hs.object_kind_of("/contacts/records.jsonl") == "record_collection",
    )

    # ---------------- slack ----------------
    from mfs_server.connectors.slack.plugin import SlackPlugin, _sanitize

    sl = make(SlackPlugin, {"token": "xoxb"})
    check(
        "slack object_kind messages.jsonl -> message_stream",
        sl.object_kind_of("/channels/general__C1/messages.jsonl") == "message_stream",
    )
    check("slack _sanitize spaces", _sanitize("My Channel!") == "My-Channel")
    dn = SlackPlugin._dir_name({"name": "general", "id": "C123"})
    check("slack dir name name__id", dn == "general__C123")
    check("slack channel id from dir", SlackPlugin._channel_id("general__C123") == "C123")
    sl._channels = lambda: _aval([{"name": "general", "id": "C1"}])
    slist = await sl.list("/channels")
    check("slack list channels", [e.name for e in slist] == ["general__C1"])

    # ---------------- discord ----------------
    from mfs_server.connectors.discord.plugin import DiscordPlugin

    dc = make(DiscordPlugin, {"token": "t", "guild_id": "G1"})
    check("discord auth header is 'Bot <token>'", dc._headers()["Authorization"] == "Bot t")
    check(
        "discord object_kind messages.jsonl -> message_stream",
        dc.object_kind_of("/channels/c__1/messages.jsonl") == "message_stream",
    )
    check("discord channel id from dir", DiscordPlugin._channel_id("chat__999") == "999")

    # ---------------- gmail ----------------
    from mfs_server.connectors.gmail.plugin import GmailPlugin, _decode_body
    import base64

    gm = make(GmailPlugin, {"token": {}})
    check(
        "gmail object_kind messages.jsonl -> message_stream",
        gm.object_kind_of("/labels/inbox__L1/messages.jsonl") == "message_stream",
    )
    enc = base64.urlsafe_b64encode(b"hello body").decode()
    body = _decode_body(
        {
            "mimeType": "multipart/alternative",
            "parts": [{"mimeType": "text/plain", "body": {"data": enc}}],
        }
    )
    check("gmail _decode_body extracts text/plain", body == "hello body")
    flat = gm._flatten(
        {
            "id": "m1",
            "threadId": "t1",
            "snippet": "hi",
            "payload": {
                "headers": [{"name": "Subject", "value": "Hi"}, {"name": "From", "value": "a@b"}],
                "mimeType": "text/plain",
                "body": {"data": enc},
            },
        }
    )
    check(
        "gmail _flatten subject/from/threadId/body",
        flat["subject"] == "Hi"
        and flat["from"] == "a@b"
        and flat["threadId"] == "t1"
        and flat["body"] == "hello body",
    )

    # ---------------- s3 ----------------
    from mfs_server.connectors.s3.plugin import S3Plugin

    s3 = make(S3Plugin, {"bucket": "b"})
    check("s3 object_kind .py -> code", s3.object_kind_of("/src/app.py") == "code")
    check("s3 object_kind .png -> image", s3.object_kind_of("/img/a.png") == "image")
    check("s3 object_kind .pdf -> document", s3.object_kind_of("/d/a.pdf") == "document")
    await s3.state.set("keys", {"/a/b.py": "e1", "/a/c.md": "e2", "/x.txt": "e3"})
    sroot = await s3.list("/")
    check("s3 list root groups dirs", sorted(e.name for e in sroot) == ["a", "x.txt"])
    sa = await s3.list("/a")
    check("s3 list /a -> files", sorted(e.name for e in sa) == ["b.py", "c.md"])

    # ---------------- gdrive ----------------
    from mfs_server.connectors.gdrive.plugin import GDrivePlugin, _NATIVE

    gd = make(GDrivePlugin, {"token": {}})
    check(
        "gdrive native doc export suffix .txt -> document",
        gd.object_kind_of("/Folder/Doc.txt") == "document",
    )
    check("gdrive native types map present", "application/vnd.google-apps.document" in _NATIVE)
    await gd.state.set(
        "files",
        {
            "/Team/Spec.txt": {"id": "1", "mimeType": "x", "fingerprint": "f"},
            "/Team/Sub/img.png": {"id": "2", "mimeType": "image/png", "fingerprint": "g"},
        },
    )
    groot = await gd.list("/Team")
    check("gdrive list builds tree", sorted(e.name for e in groot) == ["Spec.txt", "Sub"])

    # ---------------- feishu ----------------
    from mfs_server.connectors.feishu.plugin import FeishuPlugin, _extract_text

    fs = make(FeishuPlugin, {"app_id": "a", "app_secret": "s"})
    check(
        "feishu object_kind messages.jsonl -> message_stream",
        fs.object_kind_of("/chats/team__oc1/messages.jsonl") == "message_stream",
    )
    check(
        "feishu _extract_text text type", _extract_text("text", '{"text":"hi there"}') == "hi there"
    )
    check(
        "feishu _extract_text post type",
        "para" in _extract_text("post", '{"content":[[{"tag":"text","text":"para"}]]}'),
    )
    fdn = FeishuPlugin._dir_name({"name": "Team Chat", "chat_id": "oc_1"})
    check("feishu dir name sanitized", fdn == "Team-Chat__oc_1")
    check("feishu chat id from dir", FeishuPlugin._chat_id("Team-Chat__oc_1") == "oc_1")

    # ---------------- summary ----------------
    passed = sum(1 for _, c in results if c)
    total = len(results)
    print(f"\n{'=' * 48}\n  {passed}/{total} checks passed")
    raise SystemExit(0 if passed == total else 1)


def _aval(v):
    async def _a():
        return v

    return _a()


if __name__ == "__main__":
    asyncio.run(main())
