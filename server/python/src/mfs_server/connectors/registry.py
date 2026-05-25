"""Connector registry: URI scheme -> plugin class (design/07 §7)."""
from __future__ import annotations

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
    try:
        from . import web  # noqa: F401
    except ImportError:
        pass
    try:
        from . import github  # noqa: F401
    except ImportError:
        pass
    try:
        from . import postgres  # noqa: F401
    except ImportError:
        pass
    try:
        from . import mysql  # noqa: F401
    except ImportError:
        pass
    try:
        from . import mongo  # noqa: F401
    except ImportError:
        pass
