"""Enumeration-range controls: gdrive/feishu honor --since (modifiedTime lower bound),
don't delete out-of-range objects, and feishu user mode enumerates the My Space root."""

from __future__ import annotations

import datetime

import pytest

from mfs_server.connectors.base import ConnectorContext, SyncOptions
from mfs_server.connectors.feishu.plugin import FeishuPlugin, _since_ts
from mfs_server.connectors.gdrive.plugin import GDrivePlugin, _rfc3339


class RecordState:
    def __init__(self):
        self.data: dict = {}

    async def get(self, k):
        return self.data.get(k)

    async def set(self, k, v):
        self.data[k] = v

    async def delete(self, k):
        self.data.pop(k, None)

    async def checkpoint(self):
        pass


def _gdrive(state):
    return GDrivePlugin({"token": {}}, None, ctx=ConnectorContext(state, "cid", "ns"))


def _feishu(cfg, state=None):
    return FeishuPlugin(cfg, None, ctx=ConnectorContext(state or RecordState(), "cid", "ns"))


def test_rfc3339_normalizes_date():
    assert _rfc3339("2026-01-01") == "2026-01-01T00:00:00"
    assert _rfc3339("2026-01-01T12:30:00") == "2026-01-01T12:30:00"


def test_since_ts_parses_date():
    assert _since_ts("2026-01-01") == datetime.datetime(2026, 1, 1).timestamp()


@pytest.mark.anyio
async def test_gdrive_sync_full_deletes_missing(monkeypatch):
    state = RecordState()
    state.data["files"] = {"/a.txt": {"fingerprint": "1"}, "/old.txt": {"fingerprint": "x"}}
    plug = _gdrive(state)

    async def fake_walk(since=None):
        assert since is None
        return {"/a.txt": {"fingerprint": "1"}, "/b.txt": {"fingerprint": "2"}}

    monkeypatch.setattr(plug, "_walk", fake_walk)
    changes = [(c.uri, c.kind) async for c in plug.sync(SyncOptions())]

    assert ("/b.txt", "added") in changes
    assert ("/old.txt", "deleted") in changes  # full scan deletes the vanished file
    assert plug.ctx.enumeration_mode == "full"
    assert set(state.data["files"]) == {"/a.txt", "/b.txt"}  # state replaced


@pytest.mark.anyio
async def test_gdrive_sync_since_no_delete_and_merge(monkeypatch):
    state = RecordState()
    state.data["files"] = {"/old.txt": {"fingerprint": "x"}}
    plug = _gdrive(state)

    async def fake_walk(since=None):
        assert since == "2026-01-01"
        return {"/new.txt": {"fingerprint": "2"}}

    monkeypatch.setattr(plug, "_walk", fake_walk)
    changes = [(c.uri, c.kind) async for c in plug.sync(SyncOptions(since="2026-01-01"))]

    assert ("/new.txt", "added") in changes
    assert not any(k == "deleted" for _, k in changes)  # since => no full-set deletion
    assert plug.ctx.enumeration_mode == "incremental"
    assert set(state.data["files"]) == {"/old.txt", "/new.txt"}  # merged, old retained


@pytest.mark.anyio
async def test_feishu_docs_user_mode_enumerates_root(monkeypatch):
    plug = _feishu({"auth": "user"})
    called = {}

    async def fake_list(ft):
        called["ft"] = ft
        return [{"token": "t1", "name": "A", "modified_time": "1000"}]

    monkeypatch.setattr(plug, "_list_folder_docx", fake_list)
    docs = await plug._docs()
    assert called["ft"] is None  # None == My Space root
    assert [d["token"] for d in docs] == ["t1"]


@pytest.mark.anyio
async def test_feishu_docs_since_filters_old(monkeypatch):
    plug = _feishu({"auth": "user"})

    async def fake_list(ft):
        return [
            {"token": "t1", "name": "A", "modified_time": "1000"},
            {"token": "t2", "name": "B", "modified_time": "5000"},
        ]

    monkeypatch.setattr(plug, "_list_folder_docx", fake_list)
    docs = await plug._docs(since_ts=3000)
    assert [d["token"] for d in docs] == ["t2"]  # only modified_time >= since kept


@pytest.mark.anyio
async def test_feishu_sync_since_no_delete_and_merge(monkeypatch):
    state = RecordState()
    state.data["objects"] = {"/docs/old.md": "100"}
    plug = _feishu({"auth": "user"}, state)

    async def fake_chats():
        return []

    async def fake_docs(since_ts=None):
        assert since_ts is not None
        return [{"token": "t1", "name": "New", "modified_time": "5000"}]

    monkeypatch.setattr(plug, "_chats", fake_chats)
    monkeypatch.setattr(plug, "_docs", fake_docs)
    changes = [(c.uri, c.kind) async for c in plug.sync(SyncOptions(since="2026-01-01"))]

    assert not any(k == "deleted" for _, k in changes)
    assert plug.ctx.enumeration_mode == "incremental"
    assert "/docs/old.md" in state.data["objects"]  # merged, old doc retained
