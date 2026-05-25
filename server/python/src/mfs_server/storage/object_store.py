"""Object store (design/02 §10.2): artifact cache bytes + upload staging.

Layout (sliced by namespace_id):
  <root>/artifacts/<ns>/<sha1(object_uri)>/<artifact_kind>
  <root>/uploads/<ns>/<connector_id>/<request_id>.zip
  <root>/files/<ns>/<connector_id>/...        (CS upload flow extracted tree)

Local fs backend; S3/R2/MinIO backends added later. Synchronous; callers wrap in
asyncio.to_thread.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..config import ServerConfig
from .ids import sha1_hex


class LocalObjectStore:
    def __init__(self, cfg: ServerConfig):
        self.root = Path(cfg.object_store.root)
        self.backend = cfg.object_store.backend

    def _artifact_dir(self, namespace_id: str, object_uri: str) -> Path:
        return self.root / "artifacts" / namespace_id / sha1_hex(object_uri.encode())

    def artifact_path(self, namespace_id: str, object_uri: str, artifact_kind: str) -> Path:
        return self._artifact_dir(namespace_id, object_uri) / artifact_kind

    def put_artifact(self, namespace_id: str, object_uri: str, artifact_kind: str,
                     data: bytes) -> str:
        p = self.artifact_path(namespace_id, object_uri, artifact_kind)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(p)            # atomic
        return str(p)

    def get_artifact(self, namespace_id: str, object_uri: str, artifact_kind: str) -> Optional[bytes]:
        p = self.artifact_path(namespace_id, object_uri, artifact_kind)
        return p.read_bytes() if p.exists() else None

    def delete_artifact(self, namespace_id: str, object_uri: str, artifact_kind: str) -> None:
        p = self.artifact_path(namespace_id, object_uri, artifact_kind)
        if p.exists():
            p.unlink()

    def move_artifacts(self, namespace_id: str, old_uri: str, new_uri: str) -> None:
        """Rename support: physically mv the per-object artifact dir (design/04 §5.7.3)."""
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
