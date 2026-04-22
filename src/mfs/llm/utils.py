"""Shared utilities for LLM/VLM providers."""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path


def encode_image_data_url(image_path: str | Path) -> str:
    """Read an image file and return it as a ``data:`` URL (base64 encoded).

    The MIME type is guessed from the extension; unknown extensions fall back
    to ``image/png`` so the URL is still well-formed.
    """
    path = Path(image_path)
    mime, _ = mimetypes.guess_type(str(path))
    if not mime or not mime.startswith("image/"):
        mime = "image/png"
    data = path.read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}"


def read_image_bytes(image_path: str | Path) -> tuple[bytes, str]:
    """Return ``(raw_bytes, mime_type)`` for an image file."""
    path = Path(image_path)
    mime, _ = mimetypes.guess_type(str(path))
    if not mime or not mime.startswith("image/"):
        mime = "image/png"
    return path.read_bytes(), mime
