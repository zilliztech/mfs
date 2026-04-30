"""Tests for the `mfs config` subcommand group and first-run auto-init."""

from __future__ import annotations

import importlib

import pytest
from click.testing import CliRunner


@pytest.fixture
def cli_main(mfs_home, monkeypatch):
    """Reload the CLI so it picks up the test's MFS_HOME via env var."""
    # Avoid hitting the real OPENAI_API_KEY when load_config runs in tests.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    import mfs.cli_config as cc_mod
    importlib.reload(cc_mod)
    import mfs.cli as cli_mod
    importlib.reload(cli_mod)
    return cli_mod.main


# ---------------------------------------------------------------------- init


def test_config_init_creates_template(cli_main, mfs_home):
    cfg = mfs_home / "config.toml"
    # The fixture's load triggers ensure_mfs_home which auto-writes the file.
    # Remove it so we exercise `config init` from scratch.
    if cfg.exists():
        cfg.unlink()
    runner = CliRunner()
    result = runner.invoke(cli_main, ["config", "init"])
    assert result.exit_code == 0, result.output
    assert cfg.exists()
    body = cfg.read_text()
    assert "[embedding]" in body
    assert "[llm]" in body
    assert "[milvus]" in body
    assert "# provider" in body  # commented defaults present


def test_config_init_refuses_overwrite(cli_main, mfs_home):
    cfg = mfs_home / "config.toml"
    cfg.write_text('[embedding]\nprovider = "openai"\n', encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(cli_main, ["config", "init"])
    assert result.exit_code != 0
    # File preserved.
    assert 'provider = "openai"' in cfg.read_text()


def test_config_init_force_overwrites(cli_main, mfs_home):
    cfg = mfs_home / "config.toml"
    cfg.write_text("garbage\n", encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(cli_main, ["config", "init", "--force"])
    assert result.exit_code == 0, result.output
    assert "[embedding]" in cfg.read_text()


# ---------------------------------------------------------------------- get


def test_config_get_default_provider(cli_main, mfs_home):
    runner = CliRunner()
    result = runner.invoke(cli_main, ["config", "get", "embedding.provider"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "openai"


def test_config_get_unknown_key_errors(cli_main, mfs_home):
    runner = CliRunner()
    result = runner.invoke(cli_main, ["config", "get", "nope.nope"])
    assert result.exit_code == 2


# ---------------------------------------------------------------------- set


def test_config_set_modifies_file(cli_main, mfs_home):
    runner = CliRunner()
    result = runner.invoke(
        cli_main, ["config", "set", "embedding.model", "text-embedding-3-large"]
    )
    assert result.exit_code == 0, result.output
    # Verify by reading back via config get
    result = runner.invoke(cli_main, ["config", "get", "embedding.model"])
    assert result.stdout.strip() == "text-embedding-3-large"


def test_config_set_int_coerces(cli_main, mfs_home):
    runner = CliRunner()
    result = runner.invoke(cli_main, ["config", "set", "embedding.batch_size", "64"])
    assert result.exit_code == 0, result.output
    result = runner.invoke(cli_main, ["config", "get", "embedding.batch_size"])
    assert result.stdout.strip() == "64"


def test_config_set_unknown_key_errors(cli_main, mfs_home):
    runner = CliRunner()
    result = runner.invoke(cli_main, ["config", "set", "made.up", "x"])
    assert result.exit_code == 2


# ---------------------------------------------------------------------- show


def test_config_show_lists_all_sections(cli_main, mfs_home):
    runner = CliRunner()
    result = runner.invoke(cli_main, ["config", "show"])
    assert result.exit_code == 0, result.output
    out = result.output
    for section in ("[embedding]", "[llm]", "[indexing]", "[cache]", "[milvus]"):
        assert section in out
    # default annotation is present for at least one untouched key
    assert "[default]" in out


def test_config_show_marks_env_overlay(cli_main, mfs_home, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-XYZ")
    runner = CliRunner()
    result = runner.invoke(cli_main, ["config", "show"])
    assert result.exit_code == 0, result.output
    assert "OPENAI_API_KEY" in result.output
    # Secret value must not be printed.
    assert "sk-test-XYZ" not in result.output


def test_config_show_json_redacts_secrets(cli_main, mfs_home, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-JSON")
    runner = CliRunner()
    result = runner.invoke(cli_main, ["config", "show", "--json"])
    assert result.exit_code == 0, result.output
    assert "sk-test-JSON" not in result.output
    assert '"api_key": "<set>"' in result.output


def test_config_show_marks_file_override(cli_main, mfs_home):
    runner = CliRunner()
    runner.invoke(cli_main, ["config", "set", "embedding.batch_size", "64"])
    result = runner.invoke(cli_main, ["config", "show"])
    assert "[config.toml]" in result.output


# ---------------------------------------------------------------------- path


def test_config_path_prints_absolute_path(cli_main, mfs_home):
    runner = CliRunner()
    result = runner.invoke(cli_main, ["config", "path"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == str(mfs_home / "config.toml")


# ---------------------------------------------------------------------- auto-init


def test_first_run_auto_init_writes_default_config(tmp_path, monkeypatch, capsys):
    """ensure_mfs_home should drop a commented config.toml on first run."""
    home = tmp_path / "fresh-home"
    monkeypatch.setenv("MFS_HOME", str(home))
    import mfs.config as cfg_mod
    importlib.reload(cfg_mod)

    assert not (home / "config.toml").exists()
    cfg_mod.ensure_mfs_home()
    assert (home / "config.toml").exists()
    body = (home / "config.toml").read_text()
    assert "[embedding]" in body
    captured = capsys.readouterr()
    assert "created default config" in captured.err


def test_auto_init_is_idempotent(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home2"
    monkeypatch.setenv("MFS_HOME", str(home))
    import mfs.config as cfg_mod
    importlib.reload(cfg_mod)

    cfg_mod.ensure_mfs_home()
    capsys.readouterr()  # discard first notice
    cfg_mod.ensure_mfs_home()
    captured = capsys.readouterr()
    assert "created default config" not in captured.err
