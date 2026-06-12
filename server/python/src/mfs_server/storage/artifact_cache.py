"""Artifact-cache storage: derived blobs (PDF→md, VLM image text, …) per object.

This is the "artifact" half of the outward Cache concept: per-object derived
bytes that let cat/head/tail not round-trip back to the source connector. The
"transformation" half (small KV lookups for embeddings/summaries) lives next
door in `storage/transformation_cache/`.

Layout (sliced by namespace_id):
  <root>/artifacts/<ns>/<sha1(object_uri)>/<artifact_kind>
  <root>/uploads/<ns>/<connector_id>/<request_id>.zip
  <root>/files/<ns>/<connector_id>/...        (CS upload flow extracted tree)

Local-filesystem store: it is a regenerable cache, so it lives on disk
(mount a volume at `root` to persist across container restarts) rather than
needing object storage. Synchronous; callers wrap in asyncio.to_thread.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..config import ServerConfig
from .ids import sha1_hex


def make_artifact_cache(cfg: ServerConfig):
    """Build the local-filesystem artifact cache."""
    return LocalArtifactCache(cfg)


class LocalArtifactCache:
    def __init__(self, cfg: ServerConfig):
        self.root = Path(cfg.artifact_cache.root)

    def _artifact_dir(self, namespace_id: str, object_uri: str) -> Path:
        return self.root / "artifacts" / namespace_id / sha1_hex(object_uri.encode())

    def artifact_path(self, namespace_id: str, object_uri: str, artifact_kind: str) -> Path:
        return self._artifact_dir(namespace_id, object_uri) / artifact_kind

    def put_artifact(
        self, namespace_id: str, object_uri: str, artifact_kind: str, data: bytes
    ) -> str:
        p = self.artifact_path(namespace_id, object_uri, artifact_kind)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(p)  # atomic
        return str(p)

    def get_artifact(
        self, namespace_id: str, object_uri: str, artifact_kind: str
    ) -> Optional[bytes]:
        p = self.artifact_path(namespace_id, object_uri, artifact_kind)
        return p.read_bytes() if p.exists() else None

    def delete_artifact(self, namespace_id: str, object_uri: str, artifact_kind: str) -> None:
        p = self.artifact_path(namespace_id, object_uri, artifact_kind)
        if p.exists():
            p.unlink()

    def move_artifacts(self, namespace_id: str, old_uri: str, new_uri: str) -> None:
        """Rename support: physically mv the per-object artifact dir."""
        old_dir = self._artifact_dir(namespace_id, old_uri)
        new_dir = self._artifact_dir(namespace_id, new_uri)
        if old_dir.exists():
            new_dir.parent.mkdir(parents=True, exist_ok=True)
            old_dir.replace(new_dir)

    def files_root(self, namespace_id: str, connector_id: str) -> Path:
        p = self.root / "files" / namespace_id / connector_id
        p.mkdir(parents=True, exist_ok=True)
        return p

    def uploads_dir(self, namespace_id: str, connector_id: str) -> Path:
        p = self.root / "uploads" / namespace_id / connector_id
        p.mkdir(parents=True, exist_ok=True)
        return p
