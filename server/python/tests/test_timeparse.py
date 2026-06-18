"""Shared human time-bound parser (slack `oldest`, feishu `--since`)."""

from __future__ import annotations

import datetime

import pytest

from mfs_server.common.timeparse import parse_time_bound


def test_none_and_empty():
    assert parse_time_bound(None) is None
    assert parse_time_bound("") is None
    assert parse_time_bound("   ") is None


def test_unix_timestamp_passthrough():
    assert parse_time_bound("1700000000") == 1700000000.0
    assert parse_time_bound(1700000000) == 1700000000.0
    assert parse_time_bound("1700000000.5") == 1700000000.5


def test_relative_offsets():
    now = 1_000_000.0
    assert parse_time_bound("now-1d", now=now) == now - 86400
    assert parse_time_bound("now-2w", now=now) == now - 2 * 604800
    assert parse_time_bound("now-3h", now=now) == now - 3 * 3600
    assert parse_time_bound("now-30m", now=now) == now - 30 * 60


def test_iso_date_and_datetime():
    assert parse_time_bound("2026-05-01") == datetime.datetime(2026, 5, 1).timestamp()
    assert (
        parse_time_bound("2026-05-01T12:00:00")
        == datetime.datetime(2026, 5, 1, 12, 0, 0).timestamp()
    )


def test_unrecognized_raises():
    with pytest.raises(ValueError):
        parse_time_bound("yesterday")
    with pytest.raises(ValueError):
        parse_time_bound("now-30y")  # unsupported unit
