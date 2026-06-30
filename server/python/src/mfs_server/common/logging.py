"""Central logging configuration for the mfs-server process.

``configure_logging()`` installs one process-wide stdlib-logging setup: a single
console handler on **stderr** (stdout is reserved for CLI / JSON output), a level
taken from ``$MFS_LOG_LEVEL`` (default ``INFO``), and a formatter that prefixes
each line with a timestamp, the level, and the emitting module.

Runtime / library code emits through ``logging.getLogger(__name__)``. Interactive
CLI wizards (``server/connector_wizard.py``, ``server/setup_wizard.py``,
``server/wizard_ui.py``) deliberately keep writing to the console directly — that
output is the program's user interface, not diagnostic logs, so it must not be
gated behind a log level.

uvicorn is started with ``log_config=None`` so its access / error loggers
propagate to this same root handler and share the format.
"""

from __future__ import annotations

import logging
import logging.config
import os

_configured = False

# Keep the historical "mfs-server" token so existing log readers still grep for it,
# now prefixed with a timestamp + level and suffixed with the emitting module.
_FORMAT = "%(asctime)s %(levelname)-7s mfs-server [%(name)s] %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

# Third-party loggers that are chatty at INFO/DEBUG. Pin them to WARNING so they
# don't drown out mfs-server's own lines even when the root level is DEBUG.
_NOISY = (
    "httpx",
    "httpcore",
    "urllib3",
    "pymilvus",
    "milvus_lite",
    "faiss",
    "asyncio",
    "botocore",
    "s3transfer",
)


def configure_logging(level: str | None = None) -> None:
    """Install the process-wide logging config. Idempotent — safe to call from
    every entrypoint (CLI run/worker, ``create_app``, tests).

    Level precedence: explicit ``level`` > ``$MFS_LOG_LEVEL`` > ``INFO``. An
    unknown value falls back to ``INFO`` rather than raising, so a typo in the
    env var never crashes startup.
    """
    global _configured
    requested = (level or os.environ.get("MFS_LOG_LEVEL") or "INFO").upper()
    if not isinstance(logging.getLevelName(requested), int):  # unknown name -> "Level X" (str)
        requested = "INFO"
    if _configured:
        # Already installed; only an explicit arg re-applies the root level.
        if level is not None:
            logging.getLogger().setLevel(requested)
        return
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {"default": {"format": _FORMAT, "datefmt": _DATEFMT}},
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stderr",
                    "formatter": "default",
                }
            },
            "root": {"level": requested, "handlers": ["console"]},
            "loggers": {name: {"level": "WARNING"} for name in _NOISY},
        }
    )
    _configured = True
