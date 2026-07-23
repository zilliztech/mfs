"""ReadService: search/ls/cat/head/tail/grep/export/resolve_connector_uri.

Engine forwards to self.reads. The locator pair (open_path / match_connector)
connects directly to ConnectorFactory + ObjectRepository with no reverse
reference to Engine.
"""

from .read_service import ReadService

__all__ = ["ReadService"]
