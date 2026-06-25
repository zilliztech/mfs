from __future__ import annotations

import pytest

pytest.importorskip("slack_sdk")

from mfs_server.connectors.base import ConnectorContext
from mfs_server.connectors.slack.plugin import SlackPlugin


class _State:
    async def get(self, key):
        return None

    async def set(self, key, value):
        return None

    async def delete(self, key):
        return None

    async def checkpoint(self):
        return None


class _Client:
    def __init__(self, channels):
        self.channels = channels

    async def conversations_list(self, *, types, cursor, limit):
        return {"channels": self.channels, "response_metadata": {"next_cursor": ""}}


def _plugin(config):
    ctx = ConnectorContext(_State(), connector_id="connector", namespace_id="default")
    plugin = SlackPlugin(config, None, ctx=ctx)
    plugin._client = _Client(
        [
            {"id": "C1", "name": "general", "is_member": False},
            {"id": "C2", "name": "random", "is_member": True},
            {"id": "C3", "name": "ext-demo", "is_member": True},
        ]
    )
    return plugin


async def test_channel_allowlist_matches_ids_or_names_with_user_visibility():
    plugin = _plugin(
        {
            "include_unjoined": True,
            "channel_names": ["general"],
            "channel_ids": ["C3"],
        }
    )

    channels = await plugin._channels()

    assert [ch["id"] for ch in channels] == ["C1", "C3"]


async def test_channel_allowlist_still_respects_membership_by_default():
    plugin = _plugin({"channel_names": ["general", "random"]})

    channels = await plugin._channels()

    assert [ch["id"] for ch in channels] == ["C2"]
