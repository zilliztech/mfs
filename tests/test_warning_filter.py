"""Regression test: importing mfs must install the pkg_resources warning filter.

Milvus Lite triggers a `UserWarning: pkg_resources is deprecated as an API`
at import time. Every mfs command used to leak that noise to stderr. The fix
is to register the filter in ``src/mfs/__init__.py`` so it's active before
any downstream module imports ``pymilvus`` / ``milvus_lite``.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import warnings
from pathlib import Path


def test_pkg_resources_message_filter_installed():
    """Importing mfs should register a filter for the pkg_resources message."""
    import mfs  # noqa: F401  (triggers __init__.py which installs filters)

    # Look for either of the filter styles we register.
    expected_by_message = any(
        f[0] == "ignore"
        and f[1] is not None
        and "pkg_resources" in f[1].pattern
        for f in warnings.filters
    )
    expected_by_module = any(
        f[0] == "ignore"
        and f[2] is UserWarning
        and f[3] is not None
        and "milvus_lite" in f[3].pattern
        for f in warnings.filters
    )
    assert expected_by_message or expected_by_module, (
        "mfs.__init__ did not install a pkg_resources warning filter; "
        f"current filters: {warnings.filters!r}"
    )


def test_pymilvus_logger_level_raised_after_import():
    """mfs.__init__ must raise the pymilvus logger level above WARNING.

    Otherwise pymilvus's query_iterator emits a benign "failed to get mvccTs
    from milvus server" warning on every Milvus Lite call, which leaks to
    stderr on ordinary commands like `mfs ls`.
    """
    import mfs  # noqa: F401

    level = logging.getLogger("pymilvus").getEffectiveLevel()
    assert level >= logging.ERROR, (
        f"pymilvus logger effective level is {level}; expected >= ERROR (40). "
        "If this fails, the mvccTs warning will leak on every mfs command."
    )


def test_mfs_ls_does_not_emit_mvccts_warning(tmp_path):
    """End-to-end: `mfs ls` on a fresh MFS_HOME must not leak mvccTs lines.

    This is the exact failure the fix is targeting. Running the CLI as a
    subprocess is what the user actually sees — pytest's own logging
    capture masks the bug otherwise.
    """
    mfs_home = tmp_path / "mfs_home"
    mfs_home.mkdir()
    sample_dir = tmp_path / "sample"
    sample_dir.mkdir()
    (sample_dir / "a.md").write_text("# Hi\n", encoding="utf-8")

    env = {**os.environ, "MFS_HOME": str(mfs_home)}
    result = subprocess.run(
        [sys.executable, "-m", "mfs", "ls", str(sample_dir) + "/"],
        env=env,
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parent.parent),
        timeout=60,
    )

    combined = result.stdout + "\n" + result.stderr
    assert "mvccTs" not in combined, (
        "pymilvus 'mvccTs' warning leaked into mfs ls output:\n"
        f"---stdout---\n{result.stdout}\n---stderr---\n{result.stderr}"
    )
