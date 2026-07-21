"""ReadService: search/ls/cat/head/tail/grep/export/resolve_connector_uri.

Extracted from the Engine god-class (engine-redesign §4.6 stage 3). Engine
delegates to self.reads; the public read methods become thin forwards. The
locator pair (open_path / match_connector) is a ReadService public method that
connects directly to ConnectorFactory + ObjectRepository - no reverse reference
to Engine (D1: direct connect + public method, not delayed-lambda).
"""

from .read_service import ReadService

__all__ = ["ReadService"]
