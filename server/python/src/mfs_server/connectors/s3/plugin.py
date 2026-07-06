"""S3 / R2 / GCS / MinIO connector — object-key tree; object_kind
by extension (reuses file connector's mapping). aioboto3 (async wrapper over boto3).
Works against any S3-compatible endpoint via endpoint_url (R2/GCS/MinIO).

API verified against boto3 S3 docs: list_objects_v2(Bucket, Prefix, ContinuationToken,
MaxKeys) -> {Contents:[{Key,Size,ETag,LastModified}], IsTruncated, NextContinuationToken};
get_object(Bucket, Key)['Body'] (StreamingBody). aioboto3 mirrors this with `async with
session.client('s3') as s3`. NOT end-to-end tested (MinIO locally testable later).
"""

from __future__ import annotations

import mimetypes
import os
from collections.abc import AsyncIterator
from typing import Optional

import aioboto3

from ..base import (
    Capabilities,
    ConnectorConfigSchema,
    ConnectorPlugin,
    Entry,
    HealthStatus,
    ObjectChange,
    ObjectKind,
    PathStat,
    Range,
    SyncOptions,
)
from ..file.plugin import CODE_EXT, DOC_EXT, IMAGE_EXT, TEXTBLOB_EXT


class S3Config(ConnectorConfigSchema):
    bucket: Optional[str] = None
    access_key_id: Optional[str] = None
    secret_access_key: Optional[str] = None
    region: Optional[str] = None
    endpoint_url: Optional[str] = None
    prefix: str = ""


class S3Plugin(ConnectorPlugin):
    NAME = "s3"
    URI_SCHEME = "s3"
    DISPLAY_NAME = "S3 / R2 / GCS / MinIO"
    PROMPT = "An S3-compatible bucket's object-key tree (files at their key paths)."
    CAPABILITIES = Capabilities(
        manual_sync=True,
        watch=False,
        cursor_kind="etag",
        full_scan=True,
        delete_detection="full_scan",
        paged_cat=True,
    )
    CONFIG_SCHEMA = S3Config

    def _cfg(self, k, d=None):
        return (
            self.config.get(k, d) if isinstance(self.config, dict) else getattr(self.config, k, d)
        )

    def _bucket(self) -> str:
        return self._cfg("bucket")

    def _creds(self) -> tuple[Optional[str], Optional[str]]:
        ak, sk = self._cfg("access_key_id"), self._cfg("secret_access_key")
        # both inline fields are redacted before persistence, so on reopen fall back to a
        # single credential_ref carrying "<access_key_id>:<secret_access_key>".
        if (not ak or not sk) and self.credential and ":" in str(self.credential):
            cak, csk = str(self.credential).split(":", 1)
            ak, sk = ak or cak, sk or csk
        return ak, sk

    def _session(self) -> aioboto3.Session:
        ak, sk = self._creds()
        return aioboto3.Session(
            aws_access_key_id=ak, aws_secret_access_key=sk, region_name=self._cfg("region")
        )

    def _client_kwargs(self) -> dict:
        kw = {}
        if self._cfg("endpoint_url"):  # R2 / GCS / MinIO
            kw["endpoint_url"] = self._cfg("endpoint_url")
        return kw

    async def healthcheck(self) -> HealthStatus:
        # The base default never opens a real connection, so a bad access
        # key, wrong bucket, or unreachable endpoint would probe clean and
        # only surface once a real sync ran and failed. Prefer
        # list_objects_v2(MaxKeys=1) over head_bucket: verified against the
        # real (currently-broken) test bucket that head_bucket collapses
        # both "bad credentials" and "bucket doesn't exist" into an
        # undifferentiated 403, while list_objects_v2 surfaces the actual
        # error code (e.g. InvalidAccessKeyId) — same cost, better diagnostic.
        from botocore.exceptions import BotoCoreError, ClientError

        bucket = self._bucket()
        if not bucket:
            return HealthStatus(ok=False, detail="no bucket configured")
        try:
            async with self._session().client("s3", **self._client_kwargs()) as s3:
                await s3.list_objects_v2(Bucket=bucket, MaxKeys=1)
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "?")
            return HealthStatus(ok=False, detail=f"{bucket}: {code}")
        except BotoCoreError as e:
            return HealthStatus(ok=False, detail=f"network error reaching {bucket}: {e}")
        return HealthStatus(ok=True, detail=f"bucket {bucket} reachable")

    def object_kind_of(self, path: str) -> ObjectKind:
        ext = os.path.splitext(path)[1].lower()
        if ext in CODE_EXT:
            return "code"
        if ext in DOC_EXT:
            return "document"
        if ext in IMAGE_EXT:
            return "image"
        if ext in TEXTBLOB_EXT:
            return "text_blob"
        return "binary"

    def _media_type(self, path: str) -> Optional[str]:
        if path.endswith(".md"):
            return "text/markdown"
        mt, _ = mimetypes.guess_type(path)
        return mt

    async def stat(self, path: str) -> PathStat:
        if path == "/" or path.endswith("/"):
            return PathStat(path=path, type="dir")
        keys = await self.state.get("keys") or {}
        return PathStat(
            path=path, type="file", media_type=self._media_type(path), fingerprint=keys.get(path)
        )

    async def list(self, path: str) -> list[Entry]:
        keys = await self.state.get("keys") or {}
        prefix = "/" if path in ("", "/") else path.rstrip("/") + "/"
        seen: dict[str, str] = {}
        for k in keys:
            if k.startswith(prefix):
                rest = k[len(prefix) :]
                parts = rest.split("/", 1)
                seen[parts[0]] = "file" if len(parts) == 1 else "dir"
        return [
            Entry(name=n, type=t, media_type=self._media_type(n) if t == "file" else None)
            for n, t in sorted(seen.items())
        ]

    async def read(self, path: str, range: Optional[Range] = None) -> AsyncIterator[bytes]:
        key = path.lstrip("/")
        async with self._session().client("s3", **self._client_kwargs()) as s3:
            resp = await s3.get_object(Bucket=self._bucket(), Key=key)
            body = resp["Body"]
            async with body:
                # __aenter__ on the streaming body returns the raw aiohttp response (for
                # connection lifecycle), not something with a chunk-sized read -- keep
                # reading off `body` itself, which supports read(amt).
                if range is None:
                    while chunk := await body.read(65536):
                        yield chunk
                else:
                    # line range [start, end) — stream line-by-line, same approach as the
                    # file connector (cat --range must not buffer the whole object into memory).
                    start, end = range.start, range.end
                    i = 0
                    buf = b""
                    while chunk := await body.read(65536):
                        buf += chunk
                        while (nl := buf.find(b"\n")) >= 0:
                            line, buf = buf[: nl + 1], buf[nl + 1 :]
                            if start <= i < end:
                                yield line
                            i += 1
                            if i >= end:
                                return
                    if buf and start <= i < end:  # trailing line without a newline
                        yield buf

    async def fingerprint(self, path: str) -> Optional[str]:
        keys = await self.state.get("keys") or {}
        return keys.get(path)

    async def _list_keys(self) -> dict[str, str]:
        out: dict[str, str] = {}
        prefix = self._cfg("prefix", "")
        async with self._session().client("s3", **self._client_kwargs()) as s3:
            token = None
            while True:
                kw = {"Bucket": self._bucket(), "MaxKeys": 1000}
                if prefix:
                    kw["Prefix"] = prefix
                if token:
                    kw["ContinuationToken"] = token
                resp = await s3.list_objects_v2(**kw)
                for obj in resp.get("Contents", []):
                    out["/" + obj["Key"]] = obj.get("ETag", "").strip('"')
                if not resp.get("IsTruncated"):
                    break
                token = resp.get("NextContinuationToken")
        return out

    async def sync(self, opts: SyncOptions) -> AsyncIterator[ObjectChange]:
        self.ctx.declare_enumeration("full")
        old = await self.state.get("keys") or {}
        keys = await self._list_keys()
        for p, etag in keys.items():
            if opts.full or old.get(p) != etag:
                yield ObjectChange(p, "modified" if p in old else "added")
        for p in set(old) - set(keys):
            yield ObjectChange(p, "deleted")
        await self.state.set("keys", keys)
