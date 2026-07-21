"""UploadService: tar + manifest-diff upload protocol.

Extracted from the Engine god-class (engine-redesign §4.7 stage 5). Stage-5
relocation landed in c10db97; this round (stage 6 follow-up) extracts
BundleValidator (tar probe + zip-slip guard) and StagingLocator (staging path +
connector registration) as standalone classes, plus a shared _drain_after_upload
tail (partial Template Method). The staging-step bodies (extractall vs
renames/bytes/deletions) stay inline - they differ enough that a shared
template would risk behavior drift (left as a future follow-up).
"""

from __future__ import annotations

import json
import os

from ...storage.file_state import FileStateStore


def _norm_rel(p: str) -> str:
    """Connector-relative path with a single leading '/' (file_state / object_uri convention)."""
    return "/" + p.lstrip("/")


def _validate_upload_member(m) -> None:
    """Reject archive members that tarfile could materialize outside the staging tree."""
    import posixpath as _posixpath

    if m.issym() or m.islnk():
        raise ValueError(f"links not allowed in upload: {m.name}")
    if not (m.isfile() or m.isdir()):
        raise ValueError(f"unsupported member in upload: {m.name}")
    rel = str(m.name or "")
    if not rel or _posixpath.isabs(rel) or any(part == ".." for part in rel.split("/")):
        raise ValueError(f"unsafe path in archive: {rel}")
    if _posixpath.normpath(rel) in ("", ".") and not m.isdir():
        raise ValueError(f"unsafe path in archive: {rel}")


class BundleValidator:
    """Validate an upload bundle IS a readable, non-empty tar BEFORE registering
    a connector, so a garbage / empty bundle returns a clean 400 and leaves no
    residual connector behind. zip-slip / links / unsafe paths raise here.

    require_meta=False (tar snapshot): every member is member-validated.
    require_meta=True (manifest-diff): `.mfs-meta.json` is checked to be a file
    and parsed; other members are member-validated. Returns (members, meta|None)."""

    @staticmethod
    def validate(data: bytes, *, require_meta: bool = False):
        import io
        import json as _json
        import tarfile

        try:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as probe:
                members = probe.getmembers()
                if not members:
                    raise ValueError("invalid or empty upload bundle")
                for m in members:
                    if require_meta and m.name == ".mfs-meta.json":
                        if not m.isfile():
                            raise ValueError("invalid upload metadata")
                        continue
                    _validate_upload_member(m)
                meta = None
                if require_meta:
                    mm = next((m for m in members if m.name == ".mfs-meta.json"), None)
                    if mm:
                        meta = _json.loads(probe.extractfile(mm).read().decode())
        except tarfile.TarError as e:
            raise ValueError("invalid or empty upload bundle") from e
        return members, meta


class StagingLocator:
    """Staging path + connector registration. The connector's stable identity is
    file://<client_id><client-abs-root> so the user can later search / remove by
    the original local path; the bytes physically live in a server-side staging dir."""

    def __init__(self, infra, ns, ingest):
        self._infra = infra
        self._ns = ns
        self._ingest = ingest

    def root(self, client_id: str, root: str) -> str:
        import hashlib

        sub = hashlib.sha1(f"{client_id}:{root}".encode()).hexdigest()[:16]
        return os.path.realpath(str(self._infra.artifact_cache.files_root(self._ns, sub)))

    async def connector(self, client_id: str, root: str):
        staging = self.root(client_id, root)
        connector_uri = f"file://{client_id}{root}"
        cid = await self._ingest.register_or_get_connector(
            connector_uri,
            "file",
            {"root": staging, "client_id": client_id, "upload_mode": True},
        )
        return staging, connector_uri, cid


class UploadService:
    """CS upload (tar snapshot) + manifest-diff upload (byte-diff + index-diff).
    Reuses the already-public IngestOrchestrator entrypoints (register_or_get_connector /
    open_sync_job / drain_job) to stage + sync. Holds no mutable state."""

    def __init__(self, cfg, infra, objects, ingest):
        self._cfg = cfg
        self._infra = infra
        self._objects = objects
        self._ingest = ingest
        self._ns = cfg.namespace
        self._locator = StagingLocator(infra, self._ns, ingest)

    async def _drain_after_upload(
        self, cid: str, connector_uri: str, job_id: str, full: bool, process: bool
    ) -> None:
        """Shared tail: read the stored config then drain the sync job. full=True
        (--force-index / --force-upload) re-yields already-indexed staging rows so
        a forced rebuild re-embeds the whole tree."""
        crow = await self._objects.get_connector_config(cid)
        stored_cfg = json.loads(crow["config_json"]) if crow and crow["config_json"] else {}
        await self._ingest.drain_job(
            job_id, cid, connector_uri, "file", stored_cfg, full, None, process
        )

    async def ingest_upload(
        self, name: str, data: bytes, fmt: str = "tar", process: bool = True
    ) -> dict:
        """CS upload flow: client/server don't share a fs, so the client
        ships a tar(.gz) of the tree (?name=<label>). The label is the connector's stable
        identity file://<name> - the SAME file://<client_id><root> shape the manifest-diff
        flow uses - so the upload is searchable / removable by that logical URI rather than
        by the server's internal staging path (which the old code leaked as file://local…,
        diverging from the manifest flow). Full-tree snapshot; guards zip-slip."""
        import hashlib
        import io
        import tarfile

        members, _meta = BundleValidator.validate(data)

        staging, connector_uri, cid = await self._locator.connector(name, "")
        fs = FileStateStore(self._infra.meta, self._ns, cid)

        def _safe(rel: str) -> str:
            dest = os.path.realpath(os.path.join(staging, rel))
            if dest != staging and not dest.startswith(staging + os.sep):
                raise ValueError(f"unsafe path in archive: {rel}")
            return dest

        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tf:
            members = tf.getmembers()
            for m in members:  # validate EVERY member before any side effect
                _validate_upload_member(m)
                _safe(m.name)  # incl. directory entries: a lone `../escaped`
                #                               dir member would otherwise extractall outside
                #                               staging (zip-slip via a directory, not a file)
            # reserve the sync slot BEFORE mutating staging/file_state (so a rejected sync -
            # sync_already_running - leaves nothing half-applied), then stage the tree.
            job_id = await self._ingest.open_sync_job(cid, process)
            tf.extractall(staging)  # validated above
            for m in members:
                if m.isdir():
                    continue
                real = _safe(m.name)
                st = os.stat(real)
                sha1 = hashlib.sha1(open(real, "rb").read()).hexdigest()
                await fs.upsert(
                    _norm_rel(m.name), st.st_size, st.st_mtime_ns, st.st_ino, sha1, status="staged"
                )
        await self._drain_after_upload(cid, connector_uri, job_id, False, process)
        return {"job_id": job_id, "connector_uri": connector_uri, "staging": staging}

    async def files_manifest(self, client_id: str, root: str, files: list[dict]) -> dict:
        """Step ②: diff the client's stat-only manifest against the
        server-side file_state (the same table the file connector uses) and return which
        paths' bytes are needed + deletion candidates (with sha1/inode for rename pairing)."""
        staging, connector_uri, cid = await self._locator.connector(client_id, root)
        fs = FileStateStore(self._infra.meta, self._ns, cid)
        # file_state stores connector-relative paths with a leading '/' (same convention as
        # the file connector, so object_uri = connector_uri + path joins cleanly); the client
        # speaks slash-less relpaths, so normalize on the boundary.
        prev = {r["path"]: r for r in await fs.all_rows()}  # keys '/auth.md'
        client = {f["path"]: f for f in files}  # keys 'auth.md'
        need_sha1 = [
            p
            for p, f in client.items()
            if _norm_rel(p) not in prev
            or prev[_norm_rel(p)]["size"] != f.get("size")
            or prev[_norm_rel(p)]["mtime_ns"] != f.get("mtime_ns")
        ]
        deletion_candidates = [
            {"path": p.lstrip("/"), "size": r["size"], "inode": r["inode"], "sha1": r["sha1"]}
            for p, r in prev.items()
            if p.lstrip("/") not in client
        ]
        return {
            "connector_uri": connector_uri,
            "staging": staging,
            "need_sha1": need_sha1,
            "deletion_candidates": deletion_candidates,
        }

    async def files_upload(
        self, client_id: str, root: str, bundle: bytes, process: bool = True, full: bool = False
    ) -> dict:
        """Step ④: validate the bundle in a temp dir (sha1), then in one
        commit apply renames / changed bytes / deletions to the staging area and UPSERT
        file_state (status='staged'); the file connector then indexes the staged rows.
        The bundle is a tar(.gz) carrying a `.mfs-meta.json` {hashes,renames,deletions}
        member plus the changed file bytes. zip-slip + sha1 guarded."""
        import hashlib
        import io
        import json as _json
        import shutil
        import tarfile
        import tempfile

        _members, meta = BundleValidator.validate(bundle, require_meta=True)

        staging, connector_uri, cid = await self._locator.connector(client_id, root)
        fs = FileStateStore(self._infra.meta, self._ns, cid)

        def _safe(base: str, rel: str) -> str:
            dest = os.path.realpath(os.path.join(base, rel))
            if dest != base and not dest.startswith(base + os.sep):
                raise ValueError(f"unsafe path in archive: {rel}")
            return dest

        tmp = tempfile.mkdtemp(prefix=".upload-", dir=os.path.dirname(staging))
        try:
            with tarfile.open(fileobj=io.BytesIO(bundle), mode="r:*") as tf:
                members = tf.getmembers()
                for m in members:
                    if m.name != ".mfs-meta.json":
                        _validate_upload_member(m)
                        _safe(staging, m.name)
                        _safe(tmp, m.name)
                    elif not m.isfile():
                        raise ValueError("invalid upload metadata")
                mm = next((m for m in members if m.name == ".mfs-meta.json"), None)
                meta = _json.loads(tf.extractfile(mm).read().decode()) if mm else {}
                hashes = {h["path"]: h for h in meta.get("hashes", [])}
                renames = meta.get("renames", [])
                deletions = meta.get("deletions", [])
                for m in members:
                    if m.name == ".mfs-meta.json" or m.isdir():
                        continue
                    tf.extract(m, tmp)
                for m in members:  # verify each payload's sha1 before touching staging
                    if m.name == ".mfs-meta.json" or m.isdir():
                        continue
                    h = hashes.get(m.name) or hashes.get("/" + m.name)
                    if h and h.get("sha1"):
                        got = hashlib.sha1(open(_safe(tmp, m.name), "rb").read()).hexdigest()
                        if got != h["sha1"]:
                            raise ValueError(f"sha1 mismatch for {m.name}")

            # bundle fully validated in temp; NOW reserve the sync slot. If a sync is
            # already in flight this raises sync_already_running and the staging area +
            # file_state are still untouched.
            job_id = await self._ingest.open_sync_job(cid, process)

            # --- apply to staging + file_state (status='staged') ---
            for r in renames:  # 1) renames: verify server sha1, mv, carry file_state
                old, new = _norm_rel(r["old"]), _norm_rel(r["new"])
                prev = await fs.get(old)
                if not prev or prev["sha1"] != r.get("sha1"):
                    continue  # reject -> client re-sends bytes next round
                op, npth = _safe(staging, r["old"]), _safe(staging, r["new"])
                if os.path.exists(op):
                    os.makedirs(os.path.dirname(npth), exist_ok=True)
                    os.replace(op, npth)
                await fs.delete(old)
                await fs.upsert(
                    new,
                    prev["size"],
                    prev["mtime_ns"],
                    prev["inode"],
                    prev["sha1"],
                    status="staged",
                    renamed_from=old,
                )
            for h in hashes.values():  # 2) changed bytes -> staging + file_state staged
                src = _safe(tmp, h["path"])
                if os.path.exists(src):
                    dst = _safe(staging, h["path"])
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    os.replace(src, dst)
                    await fs.upsert(
                        _norm_rel(h["path"]),
                        h.get("size"),
                        h.get("mtime_ns"),
                        h.get("inode"),
                        h.get("sha1"),
                        status="staged",
                    )
            for d in deletions:  # 3) deletions: mark file_state 'deleted' so the sync
                dp = _safe(staging, d)  #    drops the index, then on_object_deleted drops the row
                if os.path.exists(dp):
                    os.remove(dp)
                prev = await fs.get(_norm_rel(d))
                if prev:
                    await fs.upsert(
                        _norm_rel(d),
                        prev["size"],
                        prev["mtime_ns"],
                        prev["inode"],
                        prev["sha1"],
                        status="deleted",
                    )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        await self._drain_after_upload(cid, connector_uri, job_id, full, process)
        return {"job_id": job_id, "connector_uri": connector_uri, "staging": staging}
