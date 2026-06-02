"""Artifact-cache storage: derived blobs (PDF→md, VLM image text, …) per object.

This is the "artifact" half of the outward Cache concept (design doc §2 +
§10.4): per-object derived bytes that let cat/head/tail not round-trip back
to the source connector. The "transformation" half (small KV lookups for
embeddings/summaries) lives next door in `storage/transformation_cache/`.

Layout (sliced by namespace_id):
  <root>/artifacts/<ns>/<sha1(object_uri)>/<artifact_kind>
  <root>/uploads/<ns>/<connector_id>/<request_id>.zip
  <root>/files/<ns>/<connector_id>/...        (CS upload flow extracted tree)

Two backends with the same interface: LocalArtifactCache (fs) and
S3ArtifactCache (S3 / R2 / GCS / MinIO via boto3 + endpoint_url).
`make_artifact_cache(cfg)` picks one. Upload staging (files_root /
uploads_dir) is always local fs (ephemeral scratch the worker scans);
only the artifact bytes themselves go to S3. Synchronous; callers wrap in
asyncio.to_thread.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..config import ServerConfig
from .ids import sha1_hex


def make_artifact_cache(cfg: ServerConfig):
    """Factory: local fs or S3-compatible store per cfg.artifact_cache.backend."""
    if cfg.artifact_cache.backend == "s3":
        return S3ArtifactCache(cfg)
    return LocalArtifactCache(cfg)


class LocalArtifactCache:
    def __init__(self, cfg: ServerConfig):
        self.root = Path(cfg.artifact_cache.root)
        self.backend = cfg.artifact_cache.backend

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


class S3ArtifactCache:
    """Artifact bytes on an S3-compatible store (AWS S3 / R2 / GCS / MinIO). Same
    interface as LocalArtifactCache. Staging dirs (files_root/uploads_dir) stay local."""

    def __init__(self, cfg: ServerConfig):
        import boto3

        self.backend = "s3"
        ac = cfg.artifact_cache
        self.bucket = ac.bucket
        self.prefix = (ac.prefix or "").strip("/")
        self.root = Path(cfg.artifact_cache.root or "/tmp/mfs-staging")  # local staging scratch
        kw = {"region_name": ac.region}
        if ac.endpoint_url:
            kw["endpoint_url"] = ac.endpoint_url
        if ac.access_key_id:
            kw["aws_access_key_id"] = ac.access_key_id
            kw["aws_secret_access_key"] = ac.secret_access_key
        self._s3 = boto3.client("s3", **kw)
        self._ensure_bucket()

    def _ensure_bucket(self) -> None:
        from botocore.exceptions import ClientError

        try:
            self._s3.head_bucket(Bucket=self.bucket)
        except ClientError:
            try:
                self._s3.create_bucket(Bucket=self.bucket)
            except ClientError:
                pass

    def _key(self, namespace_id: str, object_uri: str, artifact_kind: str) -> str:
        h = sha1_hex(object_uri.encode())
        parts = [p for p in (self.prefix, "artifacts", namespace_id, h, artifact_kind) if p]
        return "/".join(parts)

    def _obj_prefix(self, namespace_id: str, object_uri: str) -> str:
        h = sha1_hex(object_uri.encode())
        parts = [p for p in (self.prefix, "artifacts", namespace_id, h) if p]
        return "/".join(parts) + "/"

    def artifact_path(self, namespace_id: str, object_uri: str, artifact_kind: str) -> str:
        return f"s3://{self.bucket}/{self._key(namespace_id, object_uri, artifact_kind)}"

    def put_artifact(
        self, namespace_id: str, object_uri: str, artifact_kind: str, data: bytes
    ) -> str:
        key = self._key(namespace_id, object_uri, artifact_kind)
        self._s3.put_object(Bucket=self.bucket, Key=key, Body=data)
        return self.artifact_path(namespace_id, object_uri, artifact_kind)

    def get_artifact(
        self, namespace_id: str, object_uri: str, artifact_kind: str
    ) -> Optional[bytes]:
        from botocore.exceptions import ClientError

        try:
            resp = self._s3.get_object(
                Bucket=self.bucket, Key=self._key(namespace_id, object_uri, artifact_kind)
            )
            return resp["Body"].read()
        except ClientError:
            return None

    def delete_artifact(self, namespace_id: str, object_uri: str, artifact_kind: str) -> None:
        self._s3.delete_object(
            Bucket=self.bucket, Key=self._key(namespace_id, object_uri, artifact_kind)
        )

    def move_artifacts(self, namespace_id: str, old_uri: str, new_uri: str) -> None:
        """Rename: copy every artifact under the old object prefix to the new one, delete old."""
        old_pref, new_pref = (
            self._obj_prefix(namespace_id, old_uri),
            self._obj_prefix(namespace_id, new_uri),
        )
        resp = self._s3.list_objects_v2(Bucket=self.bucket, Prefix=old_pref)
        for obj in resp.get("Contents", []):
            old_key = obj["Key"]
            new_key = new_pref + old_key[len(old_pref) :]
            self._s3.copy_object(
                Bucket=self.bucket, CopySource={"Bucket": self.bucket, "Key": old_key}, Key=new_key
            )
            self._s3.delete_object(Bucket=self.bucket, Key=old_key)

    def files_root(self, namespace_id: str, connector_id: str) -> Path:
        p = self.root / "files" / namespace_id / connector_id
        p.mkdir(parents=True, exist_ok=True)
        return p

    def uploads_dir(self, namespace_id: str, connector_id: str) -> Path:
        p = self.root / "uploads" / namespace_id / connector_id
        p.mkdir(parents=True, exist_ok=True)
        return p
