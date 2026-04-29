"""Shared test fixtures."""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _silence_pkg_resources_warning():
    warnings.filterwarnings("ignore", message=".*pkg_resources.*")


@pytest.fixture
def mfs_home(tmp_path, monkeypatch) -> Path:
    home = tmp_path / ".mfs"
    home.mkdir()
    monkeypatch.setenv("MFS_HOME", str(home))
    # Force modules that cache MFS_HOME to re-read it
    import importlib

    import mfs.config as cfg_mod
    importlib.reload(cfg_mod)
    import mfs.ingest.worker as worker_mod
    importlib.reload(worker_mod)
    return home


@pytest.fixture
def sample_project(tmp_path) -> Path:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "README.md").write_text(
        "# My Project\n\nA sample project.\n\n## Features\n\nSome features.\n",
        encoding="utf-8",
    )
    (docs / "auth.md").write_text(
        "# Auth\n\n## OAuth2\n\nOAuth2 flow.\n\n## JWT\n\nJWT tokens for service-to-service auth.\n",
        encoding="utf-8",
    )
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.py").write_text("def hello():\n    print('hello')\n", encoding="utf-8")
    (tmp_path / "config.json").write_text('{"key": "value"}', encoding="utf-8")
    return tmp_path
