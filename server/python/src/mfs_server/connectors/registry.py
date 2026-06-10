"""Connector registry: URI scheme -> plugin class."""

from __future__ import annotations

from importlib import import_module

from .base import ConnectorPlugin

_REGISTRY: dict[str, type[ConnectorPlugin]] = {}


def register(cls: type[ConnectorPlugin]) -> type[ConnectorPlugin]:
    if not cls.URI_SCHEME:
        raise ValueError(f"{cls.__name__} missing URI_SCHEME")
    _REGISTRY[cls.URI_SCHEME] = cls
    return cls


def get_plugin_cls(scheme: str) -> type[ConnectorPlugin] | None:
    return _REGISTRY.get(scheme)


def all_schemes() -> list[str]:
    return sorted(_REGISTRY)


def load_builtin() -> None:
    """Import built-in connectors so their @register runs. Import lazily to avoid
    pulling optional deps (aiohttp etc.) unless that connector is used."""
    from . import file  # noqa: F401  (file has no extra deps)

    # each optional connector pulls its own SDK; skip if that extra isn't installed
    for mod in (
        "web",
        "github",
        "postgres",
        "mysql",
        "mongo",
        "slack",
        "discord",
        "gmail",
        "notion",
        "jira",
        "linear",
        "zendesk",
        "hubspot",
        "bigquery",
        "snowflake",
        "s3",
        "gdrive",
        "feishu",
    ):
        try:
            import_module(f"{__package__}.{mod}")
        except ImportError:
            pass
