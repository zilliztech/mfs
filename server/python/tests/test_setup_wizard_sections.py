"""The setup wizard's `description` and `summary` sections must stay decoupled.

`[description]` is the per-image vision operation; `[summary]` is the
per-directory (and optional per-file) text operation. The engine already treats
them as two independent kill switches, so the wizard must write each section in
isolation — enabling one must never flip the other.
"""

from __future__ import annotations

from mfs_server.server.setup_wizard import SECTIONS, _apply, _summary_pairs


def test_optional_sections_come_last() -> None:
    assert SECTIONS[-2:] == ("description", "summary")
    # The non-optional base sections still lead.
    assert SECTIONS[0] == "embedding"


def test_description_section_writes_only_description() -> None:
    out = _apply(
        "description",
        {},
        {"enabled": True, "provider": "openai", "model": "gpt-4o-mini"},
    )
    assert out["description"] == {
        "enabled": True,
        "provider": "openai",
        "model": "gpt-4o-mini",
    }
    assert "summary" not in out  # never touches the summary subsystem


def test_description_off_leaves_summary_untouched() -> None:
    existing = {"summary": {"enabled": True, "provider": "openai", "model": "m"}}
    out = _apply("description", existing, {"enabled": False})
    assert "description" not in out
    assert out["summary"]["enabled"] is True  # independent kill switch


def test_summary_section_writes_only_summary_with_scope() -> None:
    out = _apply(
        "summary",
        {},
        {
            "enabled": True,
            "provider": "openai",
            "model": "gpt-4o-mini",
            "dir": True,
            "file": True,
            "include_image_description": True,
        },
    )
    assert out["summary"] == {
        "enabled": True,
        "provider": "openai",
        "model": "gpt-4o-mini",
        "dir": True,
        "file": True,
        "include_image_description": True,
    }
    assert "description" not in out


def test_summary_dir_only_omits_image_fold_when_not_offered() -> None:
    # When image description is off the wizard skips the fold prompt, so the key
    # is absent from the answers and must not appear in the written block.
    out = _apply(
        "summary",
        {},
        {
            "enabled": True,
            "provider": "openai",
            "model": "gpt-4o-mini",
            "dir": True,
            "file": False,
        },
    )
    assert out["summary"]["file"] is False
    assert "include_image_description" not in out["summary"]


def test_summary_review_shows_both_lines_with_precise_terms() -> None:
    out = {
        "description": {"enabled": True, "provider": "openai", "model": "gpt-4o-mini"},
        "summary": {"enabled": True, "provider": "openai", "model": "gpt-4o-mini", "file": True},
    }
    pairs = dict(_summary_pairs(out))
    assert pairs["Image description"] == "openai / gpt-4o-mini"
    assert pairs["Directory summaries"] == "openai / gpt-4o-mini (dir + file)"
    # The old conflated "VLM" label is gone.
    assert "VLM" not in pairs


def test_description_openai_compatible_writes_endpoint_fields() -> None:
    out = _apply(
        "description",
        {},
        {
            "enabled": True,
            "provider": "openai_compatible",
            "model": "deepseek-chat",
            "base_url": "https://api.deepseek.com/v1",
            "api_key": "env:DEEPSEEK_API_KEY",
        },
    )
    assert out["description"]["base_url"] == "https://api.deepseek.com/v1"
    assert out["description"]["api_key"] == "env:DEEPSEEK_API_KEY"


def test_summary_openai_compatible_writes_endpoint_fields() -> None:
    out = _apply(
        "summary",
        {},
        {
            "enabled": True,
            "provider": "openai_compatible",
            "model": "deepseek-chat",
            "dir": True,
            "file": False,
            "base_url": "https://api.deepseek.com/v1",
            "api_key": "env:DEEPSEEK_API_KEY",
        },
    )
    assert out["summary"]["base_url"] == "https://api.deepseek.com/v1"
    assert out["summary"]["api_key"] == "env:DEEPSEEK_API_KEY"


def test_summary_cloud_provider_omits_endpoint_fields() -> None:
    # Cloud openai answers carry no base_url/api_key; the written block must
    # not synthesize empty ones.
    out = _apply(
        "summary",
        {},
        {
            "enabled": True,
            "provider": "openai",
            "model": "gpt-4o-mini",
            "dir": True,
            "file": False,
        },
    )
    assert "base_url" not in out["summary"]
    assert "api_key" not in out["summary"]
