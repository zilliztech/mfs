"""Shared parser for human-friendly time bounds used by connectors (slack `oldest`,
feishu `--since`, and any future time-bounded sync). Connectors translate the friendly
value into whatever their upstream API wants, so a user never hand-computes a unix epoch.

Accepted forms:
  - an ISO date or datetime — ``2026-05-01`` / ``2026-05-01T12:00:00``
  - a relative offset from now — ``now-30d`` (also ``w`` weeks, ``h`` hours, ``m`` minutes)
  - a unix timestamp already (int / float / numeric string) — returned unchanged
"""

from __future__ import annotations

import datetime
import re
from typing import Optional

_REL = re.compile(r"^now-(\d+)([dwhm])$")
_UNIT_SECONDS = {"d": 86400, "w": 604800, "h": 3600, "m": 60}


def parse_time_bound(value: object, *, now: Optional[float] = None) -> Optional[float]:
    """Parse a human time bound into a unix timestamp (seconds, float).

    Returns ``None`` for ``None`` / empty. Raises ``ValueError`` on an unrecognized format
    so a bad value fails loudly with a clear message rather than being silently passed to
    the upstream API. ``now`` overrides the reference time for relative offsets (testing)."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None

    # already a unix timestamp
    try:
        return float(s)
    except ValueError:
        pass

    # relative offset: now-<N><unit>
    m = _REL.match(s)
    if m:
        base = now if now is not None else datetime.datetime.now(datetime.timezone.utc).timestamp()
        return base - int(m.group(1)) * _UNIT_SECONDS[m.group(2)]

    # ISO datetime, then ISO date (date-only is rejected by datetime.fromisoformat on 3.10)
    try:
        return datetime.datetime.fromisoformat(s).timestamp()
    except ValueError:
        pass
    try:
        d = datetime.date.fromisoformat(s)
        return datetime.datetime(d.year, d.month, d.day).timestamp()
    except ValueError:
        pass

    raise ValueError(
        f"unrecognized time value {value!r}: use an ISO date (2026-05-01), "
        f"a relative offset (now-30d), or a unix timestamp"
    )
