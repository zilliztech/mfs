"""Config tests for the V0.4 TOML rename (§7): new schema loads, old names fail loudly,
[summary].dir/file toggles reach the ReduceCoordinator."""

from __future__ import annotations

import pytest

from mfs_server.config import ServerConfig, load_server_config
from mfs_server.engine.reduce import ReduceCoordinator


def _write(tmp_path, text):
    p = tmp_path / "server.toml"
    p.write_text(text)
    return str(p)


def test_defaults_match_v0_4_schema():
    cfg = ServerConfig()
    # §7.2 defaults
    assert cfg.chunks_producer.concurrency == 8
    assert cfg.object_task.max_retries == 3
    assert cfg.object_task.consecutive_fatal_threshold == 5
    assert cfg.embedding.batch_size == 100
    assert cfg.description.enabled is False
    assert cfg.description.provider == "openai"
    assert cfg.description.concurrency == 10
    assert cfg.summary.concurrency == 20
    assert cfg.summary.dir is True
    assert cfg.summary.file is False
    assert cfg.summary.include_image_description is False
    assert cfg.summary.max_input_kb == 64 and cfg.summary.per_file_max_kb == 16
    assert cfg.conversion.default == "markitdown"
    assert cfg.server.in_process_jobrunner is True
    assert cfg.chunking.chunk_size == 2048
    # dead keys are gone
    assert not hasattr(cfg.embedding, "batch_max_wait_ms")
    assert not hasattr(cfg.summary, "batch_size")
    assert not hasattr(cfg.description, "batch_size")


def test_new_schema_toml_loads(tmp_path):
    path = _write(
        tmp_path,
        """
        [chunks_producer]
        concurrency = 4

        [object_task]
        max_retries = 7

        [chunking]
        chunk_size = 512

        [description]
        enabled = true
        concurrency = 3

        [summary]
        enabled = true
        concurrency = 6
        dir = true
        file = true

        [conversion]
        default = "markitdown"

        [server]
        in_process_jobrunner = false
        """,
    )
    cfg = load_server_config(path, apply_env=False)
    assert cfg.chunks_producer.concurrency == 4
    assert cfg.object_task.max_retries == 7
    assert cfg.chunking.chunk_size == 512
    assert cfg.description.enabled is True and cfg.description.concurrency == 3
    assert cfg.summary.enabled is True and cfg.summary.file is True and cfg.summary.concurrency == 6
    assert cfg.server.in_process_jobrunner is False


def test_unknown_top_level_config_section_fails(tmp_path):
    path = _write(
        tmp_path,
        """
        [databse]
        backend = "sqlite"
        """,
    )

    with pytest.raises(ValueError, match="server.toml has invalid config field"):
        load_server_config(path, apply_env=False)

    try:
        load_server_config(path, apply_env=False)
    except ValueError as e:
        assert "databse" in str(e)
        assert "Extra inputs are not permitted" in str(e)


def test_unknown_nested_config_key_fails(tmp_path):
    path = _write(
        tmp_path,
        """
        [database]
        backend = "sqlite"
        unknown_key = true
        """,
    )

    with pytest.raises(ValueError, match="server.toml has invalid config field"):
        load_server_config(path, apply_env=False)

    try:
        load_server_config(path, apply_env=False)
    except ValueError as e:
        assert "database.unknown_key" in str(e)
        assert "Extra inputs are not permitted" in str(e)


@pytest.mark.parametrize(
    "block,needle",
    [
        ("[vlm]\nenabled = true\n", "[description]"),
        ("[worker]\nconcurrency = 4\n", "[chunks_producer]"),
        ('[converter]\ndefault = "markitdown"\n', "[conversion]"),
        ("[chunk]\nchunk_size = 2048\n", "[chunking]"),
    ],
)
def test_renamed_sections_fail_loudly(tmp_path, block, needle):
    path = _write(tmp_path, block)
    with pytest.raises(ValueError, match="renamed config section"):
        load_server_config(path, apply_env=False)
    # the error points at the new name
    try:
        load_server_config(path, apply_env=False)
    except ValueError as e:
        assert needle in str(e)


@pytest.mark.parametrize(
    "block",
    [
        "[summary]\nbatch_size = 20\n",
        "[summary]\ndir_recursive = true\n",
        "[summary]\ninclude_image_desc = true\n",
        "[embedding]\nbatch_max_wait_ms = 100\n",
    ],
)
def test_removed_keys_fail_loudly(tmp_path, block):
    path = _write(tmp_path, block)
    with pytest.raises(ValueError, match="removed/renamed key"):
        load_server_config(path, apply_env=False)


def _coord(cfg):
    return ReduceCoordinator(
        cfg, tx_cache=None, summary=None, vlm=None, converter=None, chunks_q=None
    )


def test_summary_dir_file_toggles_reach_coordinator():
    # dir=False, file=True: no directory tree accumulation, file candidates collected instead
    cfg = ServerConfig()
    cfg.summary.enabled = True
    cfg.summary.dir = False
    cfg.summary.file = True
    cfg.summary.concurrency = 7
    coord = _coord(cfg)
    assert coord.do_dir is False and coord.do_file is True
    assert coord._worker_count() == 7  # [summary].concurrency sizes the SummaryWorker pool

    coord.register_job("j", "c", None)
    coord.on_yield_object_change("j", "/a/b.md", "document")
    assert coord.builders["j"].tree == {}  # do_dir off -> dir tree NOT built
    assert coord._file_summary_candidates["j"] == [("/a/b.md", "document")]  # file collected


def test_summary_dir_default_builds_tree():
    cfg = ServerConfig()
    cfg.summary.enabled = True  # dir defaults True, file defaults False
    coord = _coord(cfg)
    assert coord.do_dir is True and coord.do_file is False
    coord.register_job("j", "c", None)
    coord.on_yield_object_change("j", "/a/b.md", "document")
    assert "/a" in coord.builders["j"].tree  # directory tree built
    assert "j" not in coord._file_summary_candidates  # no per-file summaries collected
