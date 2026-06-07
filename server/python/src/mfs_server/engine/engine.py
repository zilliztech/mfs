"""Engine: orchestration for `mfs add` (register connector -> job -> sync ->
object_tasks -> process). `_index_object` does the real per-object work: read ->
chunk/convert/VLM/summary -> embed -> Milvus upsert, per object_kind. Jobs run inline
(process=True) or are drained by the standalone worker (run_worker_*).

per-object atomic writes + job inheritance + circuit breaker.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone

from ..common.converter import CONVERT_EXTS, CachingConverterClient
from ..common.embedding import CachingEmbeddingClient
from ..common.retrieval import build_filter, collapse_by_object, to_envelope
from ..common.summary import CachingSummaryClient
from ..common.vlm import CachingVlmClient
from ..config import ServerConfig
from ..connectors.base import ConnectorContext, ObjectConfig, SyncOptions
from ..connectors.registry import get_plugin_cls, load_builtin
from ..storage.file_state import FileStateStore
from ..storage.ids import chunk_id
from ..storage.metadata import make_metadata_store
from ..storage.milvus import MILVUS_MAX_RESULT_WINDOW, MilvusStore
from ..storage.artifact_cache import make_artifact_cache
from ..storage.transformation_cache import make_transformation_cache
from .adapters import ArtifactStoreAdapter, EmbedderAdapter, MilvusSinkAdapter, TxCacheAdapter
from .pipeline import EmbedConsumer, TaskEnvelope, _EMBED_FLUSH_IDLE_MS, make_chunks_q
from .producers import select_producer
from .producers.base import (
    DescriptionConcurrencyGate,
    EndOfTask,
    ObjectTask,
    ProducerContext,
    SummaryConcurrencyGate,
    cap_content,
)
from .producers.render import render_record, resolve_path
from .job_watcher import ConnectorJobWatcher
from .reduce import build_reduce_subsystem
from .state import ConnectorStateStore

_SCHEME_RE = re.compile(r"^([a-z][a-z0-9+.\-]*)://")
_HEAD_CACHE_N = 100  # rows pre-cached per structured object to speed `head`
_BARE_CAT_MAX_BYTES = 5 * 1024 * 1024  # bare `cat` (no range) rejects objects larger than this
_GREP_LINEAR_SCAN_MAX = 200  # cap on not-indexed files a single grep scans linearly
_JOB_STALE_AFTER_S = 120  # no heartbeat for this long => worker presumed dead
_HEARTBEAT_INTERVAL_S = 10  # worker refreshes its job heartbeat this often (<< stale)
_WORKER_CONNECT_TIMEOUT_S = 30  # bound plugin.connect() in the worker so a hanging/unreachable
# connector fails its job cleanly instead of blocking the single in-process worker forever

# Local, per-object events that aren't systemic failures: the source disappeared
# (file deleted mid-sync), or its path type changed (file <-> dir under us). These
# happen normally when a user edits / git-checkouts / cleans up while `mfs add` runs,
# so they must NOT retry and must NOT count toward consecutive_fatal_threshold. The
# circuit breaker exists to stop on "the source is systemically broken" (auth/rate
# limit/network), not on "I lost a few files".
_PER_OBJECT_SKIP_ERRORS: tuple = (
    FileNotFoundError,
    NotADirectoryError,
    IsADirectoryError,
)


def _normalize_json(s: str) -> str:
    """Sort-key + strip-whitespace normalize a JSON object string so two configs
    with identical contents but different key ordering / whitespace compare
    equal. Used to detect real config drift vs cosmetic re-serialization."""
    import json as _json

    try:
        return _json.dumps(_json.loads(s), sort_keys=True, separators=(",", ":"))
    except (ValueError, TypeError):
        return s


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _match_object_config(objects_cfg: list, path: str) -> ObjectConfig | None:
    """Find the user [[objects]] entry whose `match` matches this path,
    first-match wins; None when nothing matches (caller falls back to a built-in preset)."""
    import fnmatch

    fields = ObjectConfig.__dataclass_fields__
    for o in objects_cfg:
        m = o.get("match", "")
        if m and (fnmatch.fnmatch(path, m) or fnmatch.fnmatch(path.lstrip("/"), m) or m in path):
            return ObjectConfig(**{k: v for k, v in o.items() if k != "match" and k in fields})
    return None


_CODE_SYMBOL = re.compile(r"^\s*(def |class |func |fn |public |private |func\(|type )")


def _density_view(text: str, ext: str, density: str) -> str:
    """Skeleton view of a document/code object:
    peek = headings (markdown #) or code symbol lines only;
    skim = peek + the first non-blank line of prose under each heading.
    """
    lines = text.splitlines()
    is_md = ext in (".md", ".markdown", ".rst", ".txt", "")
    out: list[str] = []
    if is_md:
        for i, ln in enumerate(lines):
            if ln.lstrip().startswith("#"):
                out.append(ln.rstrip())
                if density == "skim":
                    for nxt in lines[i + 1 :]:
                        if nxt.strip():
                            out.append("    " + nxt.strip()[:120])
                            break
    else:
        for ln in lines:
            if _CODE_SYMBOL.match(ln):
                out.append(ln.rstrip() if density == "skim" else ln.split("(")[0].rstrip())
    if not out:
        # nothing structural found -> first lines as a fallback peek
        out = [ln.rstrip() for ln in lines[:15]]
    return "\n".join(out)


class _PipelineEmbedConsumer(EmbedConsumer):
    """EmbedConsumer wired for the real Milvus + embedding cache: supplies the embed cache key
    (provider/model/version aware, shared with CachingEmbeddingClient) and the Milvus row shape
    (chunk_id PK + namespace_id + indexed_at)."""

    def __init__(self, *args, namespace_id: str, embed_key_fn, **kwargs):
        super().__init__(*args, **kwargs)
        self._ns = namespace_id
        self._embed_key_fn = embed_key_fn

    def _cache_key(self, chunk) -> str:
        return self._embed_key_fn(chunk.content)

    def _build_row(self, env: TaskEnvelope, chunk, vec: list[float]) -> dict:
        content, _ = cap_content(chunk.content)
        loc = chunk.locator
        return {
            "chunk_id": chunk_id(self._ns, env.connector_uri, env.task_uri, chunk.chunk_kind, loc),
            "namespace_id": self._ns,
            "connector_uri": env.connector_uri,
            "object_uri": env.task_uri,
            "locator": loc,
            "content": content,
            "dense_vec": vec,
            "chunk_kind": chunk.chunk_kind,
            "metadata": chunk.metadata,
            "indexed_at": int(time.time() * 1000),
        }


class Engine:
    # okinds always routed to the producer + chunks_q + EmbedConsumer path (§3.2). image and
    # table_schema route conditionally (see _routes_to_pipeline); everything else is
    # metadata-only. dir_summary is not an object_task — the Reduce subsystem owns it (§3.5).
    _PIPELINE_OKINDS = ("document", "code", "message_stream", "record_collection", "table_rows")

    def __init__(self, cfg: ServerConfig):
        self.cfg = cfg
        self.ns = cfg.namespace
        self.meta = make_metadata_store(cfg)
        self.milvus = MilvusStore(cfg)
        self.artifact_cache = make_artifact_cache(cfg)
        self.tx_cache = make_transformation_cache(cfg)
        self.embed = CachingEmbeddingClient(cfg, self.tx_cache)
        self.converter = CachingConverterClient(cfg, self.tx_cache)
        self.vlm = CachingVlmClient(cfg, self.tx_cache)
        self.summary = CachingSummaryClient(cfg, self.tx_cache)
        self._artifact_writes = 0  # throttles LRU eviction sweeps
        # --- new pipeline (process singletons; built lazily in _build_pipeline) ---
        self._chunks_q: asyncio.Queue | None = None
        self._embed_consumer: _PipelineEmbedConsumer | None = None
        self._producer_ctx: ProducerContext | None = None
        # full_uri -> (cid, relpath, stat, indexable, plugin, task_id) for pipeline-path objects
        # whose objects-table row + object_tasks status are written by _on_pipeline_object_indexed
        # when the EmbedConsumer reports the task done.
        self._pending_finalize: dict[str, tuple] = {}
        self._embed_idle_ms = _EMBED_FLUSH_IDLE_MS
        # Reduce subsystem (dir_summary lane); built in _build_pipeline, inert when summary off.
        self._reduce = None
        # ConnectorJobWatcher: out-of-band job completion / cancel / reduce-evict (§5.7).
        self._job_watcher = None
        self._job_watcher_task: asyncio.Task | None = None

    async def startup(self) -> None:
        load_builtin()
        await self.meta.connect()
        await self.meta.init_schema()
        await self.tx_cache.connect()
        self.milvus.connect()
        self.milvus.ensure_collection(self.ns)
        self._build_pipeline()
        await self._recover_reduce()
        # ConnectorJobWatcher runs in this same event loop as the EmbedConsumer + SummaryWorker
        # pool, finalizing jobs out-of-band (§5.7).
        self._job_watcher = ConnectorJobWatcher(self.meta, self._reduce)
        self._job_watcher_task = asyncio.create_task(self._job_watcher.run())
        # NB: the embedding dim-mismatch warning is deferred to the first provider build
        # (CachingEmbeddingClient._warn_dim_mismatch_once) so boot never downloads/loads the
        # model just to read .dimension — the provider stays lazy and cold start is fast.

    async def shutdown(self) -> None:
        if self._job_watcher is not None:
            self._job_watcher.stop()
            if self._job_watcher_task is not None:
                try:
                    await self._job_watcher_task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            self._job_watcher = None
        if self._reduce is not None:
            # stop the SummaryWorker pool first so no new dir chunks are produced, then
            # drain whatever already reached the queue.
            await self._reduce.stop()
        if self._embed_consumer is not None:
            # drain the queue + flush the final batch before the loop closes, so an
            # in-flight task's chunks aren't lost on shutdown.
            await self._embed_consumer.shutdown()
            self._embed_consumer = None
        await self.meta.close()
        await self.tx_cache.close()

    # --- new pipeline: process-singleton chunks_q + EmbedConsumer (§3.1 / §5.2) ---
    def _build_pipeline(self) -> None:
        """Construct the process-level chunks_q + EmbedConsumer + ProducerContext and start
        the consumer draining in the background. Idempotent; called from startup() (and
        lazily from _index_via_pipeline so the pipeline path works even if a caller skipped
        startup). The EmbedConsumer is shared across all jobs so embed batches fill across
        connectors (§5.2)."""
        if self._embed_consumer is not None:
            return
        batch_size = self.cfg.embedding.batch_size
        self._chunks_q = make_chunks_q(batch_size)
        self._embed_consumer = _PipelineEmbedConsumer(
            # raw provider embed (no caching) so the consumer's TxCacheAdapter is the single
            # embed cache and there is no double-cache; cache key matches CachingEmbeddingClient.
            EmbedderAdapter(self.embed._embed_api),
            MilvusSinkAdapter(self.milvus, self.ns),
            TxCacheAdapter(
                self.tx_cache,
                kind="embedding",
                provider=self.embed.provider_name,
                model=self.embed.model,
                version=self.embed.version,
            ),
            batch_size,
            idle_ms=self._embed_idle_ms,
            namespace_id=self.ns,
            embed_key_fn=self.embed._key,
        )
        self._embed_consumer.register_on_succeeded(self._on_pipeline_object_indexed)
        # ONE description gate + ONE summary gate per process (§5.5), shared by BOTH the Map
        # producers (image / table_schema) and the Reduce SummaryWorker pool, so every VLM /
        # summary provider call — wherever it originates — draws from the same in-flight budget
        # ([description].concurrency / [summary].concurrency).
        self._description_gate = DescriptionConcurrencyGate(self.cfg.description.concurrency)
        self._summary_gate = SummaryConcurrencyGate(self.cfg.summary.concurrency)
        self._producer_ctx = ProducerContext(
            cfg=self.cfg,
            namespace_id=self.ns,
            artifacts=ArtifactStoreAdapter(
                self._put_artifact, self._read_artifact, self.artifact_cache.artifact_path
            ),
            converter=self.converter,
            vlm=self.vlm,
            summary=self.summary,
            description_gate=self._description_gate,
            summary_gate=self._summary_gate,
        )
        # Reduce subsystem (§3.5): dir summaries emit into the SAME chunks_q. Its
        # on_embed_succeeded is registered alongside the Map per-task hook so a file's
        # success both unblocks _index_via_pipeline AND notifies the dir tree (§6.4.4).
        self._reduce = build_reduce_subsystem(
            self.cfg,
            tx_cache=self.tx_cache,
            summary=self.summary,
            vlm=self.vlm,
            converter=self.converter,
            chunks_q=self._chunks_q,
            description_gate=self._description_gate,
            summary_gate=self._summary_gate,
        )
        self._embed_consumer.register_on_succeeded(self._reduce.on_embed_succeeded)
        self._embed_consumer.start(self._chunks_q)
        self._reduce.start()  # no-op unless cfg.summary.enabled

    def _routes_to_pipeline(self, okind: str) -> bool:
        """Whether this okind goes through the producer -> chunks_q -> EmbedConsumer path.

        document / code / message_stream / record_collection / table_rows always route
        (_PIPELINE_OKINDS). image routes only when [description] is enabled — its
        ImageChunksProducer makes a VLM call, so with it off the image is recorded metadata-only.
        table_schema routes only when [summary] is enabled — its TableSchemaProducer makes a
        summary LLM call; with it off the schema is metadata-only. dir_summary is not an
        object_task at all — the Reduce subsystem owns it (§3.5)."""
        if okind in self._PIPELINE_OKINDS:
            return True
        if okind == "image":
            return self.cfg.description.enabled
        if okind == "table_schema":
            return self.summary.enabled
        return False

    async def _recover_reduce(self) -> None:
        """Rebuild the Reduce subsystem's in-memory dir trees for jobs left 'running' by a
        crash (§6.4.5). Best-effort: a per-job failure is logged and skipped, never blocking
        boot. Already-written directory summaries are recomputed (idempotent + summary-cache
        cheap) rather than queried back from Milvus."""
        if self._reduce is None or not self._reduce.enabled:
            return
        import json as _json

        try:
            jobs = await self.meta.fetchall(
                "SELECT id, connector_id FROM connector_jobs WHERE status='running'"
            )
        except Exception:  # noqa: BLE001 — recovery must never wedge startup
            return
        for job in jobs:
            job_id, cid = job["id"], job["connector_id"]
            try:
                crow = await self.meta.fetchone(
                    "SELECT root_uri, type, config_json FROM connectors WHERE id=?", (cid,)
                )
                if not crow:
                    continue
                connector_uri, ctype = crow["root_uri"], crow["type"]
                stored_cfg = _json.loads(crow["config_json"]) if crow["config_json"] else {}
                plugin, _ = self._build_plugin(ctype, stored_cfg, cid)
                await asyncio.wait_for(plugin.connect(), timeout=_WORKER_CONNECT_TIMEOUT_S)
                rows = await self.meta.fetchall(
                    "SELECT object_uri, status FROM object_tasks "
                    "WHERE connector_job_id=? AND change_kind != 'dir_summary'",
                    (job_id,),
                )
                objects = [
                    (r["object_uri"], plugin.object_kind_of(r["object_uri"]), r["status"])
                    for r in rows
                ]
                self._reduce.recover_job(job_id, connector_uri, plugin, objects, [])
            except Exception as e:  # noqa: BLE001
                print(f"mfs-server: WARNING reduce recovery for job {job_id} failed: {e}", flush=True)

    async def _on_pipeline_object_indexed(
        self, task_uri: str, job_id: str | None, chunk_count: int = 0, partial: bool = False
    ) -> None:
        """EmbedConsumer success hook: write the `objects` row, commit the connector's
        file_state cursor, and flip object_tasks to 'succeeded' for a pipeline-path object — now
        that the EmbedConsumer knows its final chunk_count + partial flag. Skips tasks it has no
        stashed context for (e.g. a Reduce directory_summary success, which has no objects row)."""
        ctx = self._pending_finalize.pop(task_uri, None)
        if ctx is None:
            return
        cid, relpath, st, indexable, plugin, task_id = ctx
        if chunk_count == 0:
            search_status = "not_indexed"
        elif partial:
            search_status = "partial"
        else:
            search_status = "indexed"
        await self._write_object_row(cid, relpath, st, indexable, search_status, chunk_count)
        await plugin.on_object_indexed(relpath)
        # Completion lives here, not in the worker loop: the pump enqueues chunks without
        # blocking, so only the consumer knows when they have landed. Conditional on
        # status='running' so a task cancelled out from under us is not revived.
        await self.meta.execute(
            "UPDATE object_tasks SET status='succeeded', finished_at=? WHERE id=? AND status='running'",
            (_now(), task_id),
        )

    async def _write_object_row(
        self, cid: str, relpath: str, st, indexable: bool, search_status: str, chunk_count: int
    ) -> None:
        """UPSERT the `objects` registry row (type/media/size/fingerprint + search_status +
        chunk_count). Shared by the inline _index_object tail and the pipeline success hook."""
        await self.meta.execute(
            "INSERT INTO objects (connector_id, object_uri, parent_path, type, media_type, size_hint, "
            " fingerprint, indexable, last_seen, search_status, chunk_count, indexed_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(connector_id, object_uri) DO UPDATE SET "
            " type=excluded.type, media_type=excluded.media_type, size_hint=excluded.size_hint, "
            " fingerprint=excluded.fingerprint, indexable=excluded.indexable, last_seen=excluded.last_seen, "
            " search_status=excluded.search_status, chunk_count=excluded.chunk_count, indexed_at=excluded.indexed_at",
            (
                cid,
                relpath,
                os.path.dirname(relpath) or "/",
                st.type,
                st.media_type,
                st.size_hint,
                st.fingerprint,
                1 if indexable else 0,
                _now(),
                search_status,
                chunk_count,
                _now(),
            ),
        )

    async def _index_via_pipeline(
        self, plugin, connector_uri: str, relpath: str, full_uri: str, okind: str, task: dict
    ) -> None:
        """Produce this object's chunks and forward them to the shared chunks_q, then return —
        a non-blocking producer pump. Completion is async: the EmbedConsumer writes the chunks
        (delete_by_object once on the first chunk, then upsert — the §6.1 per-object atomic
        invariant) and fires the success hooks, which write the objects row + flip the
        object_tasks status. The caller marks nothing for this task ('deferred')."""
        if self._embed_consumer is None:
            self._build_pipeline()
        task_id = task["id"]
        job_id = task.get("connector_job_id")
        producer = select_producer(okind, self._producer_ctx)
        try:
            if producer is None:
                # unreachable for routed okinds; emit a bare EndOfTask so the consumer finalizes
                # (zero-chunk) and the success hook still writes the metadata-only objects row.
                await self._chunks_q.put(
                    TaskEnvelope(task_id, full_uri, connector_uri, job_id, EndOfTask())
                )
            else:
                otask = ObjectTask(
                    object_uri=relpath,
                    connector_uri=connector_uri,
                    okind=okind,
                    change_kind=task["change_kind"],
                    connector_job_id=job_id,
                    task_id=task_id,
                    plugin=plugin,
                    ocfg=plugin.ctx.object_config_for(relpath),
                )
                async for item in producer.produce(otask):
                    await self._chunks_q.put(
                        TaskEnvelope(task_id, full_uri, connector_uri, job_id, item)
                    )
        except Exception:
            # produce() failed before all chunks were enqueued: drop the stashed finalize
            # context so no objects row is written, then re-raise for _process_with_retry.
            self._pending_finalize.pop(full_uri, None)
            raise
        finally:
            if okind == "message_stream":
                # GC the per-task raw_records jsonl the MessageStreamProducer materialized
                # (§5.4): only needed to regroup messages by thread during produce(), which is
                # done once the produce loop above exhausts. Runs on success AND failure.
                try:
                    await asyncio.to_thread(
                        self.artifact_cache.delete_artifact, self.ns, full_uri, "raw_records"
                    )
                except Exception:  # noqa: BLE001 — GC of a temp artifact must never fail the task
                    pass

    # --- target resolution (Phase 2: file only) ---
    def _resolve_target(self, target: str) -> tuple[str, str, str, dict]:
        m = _SCHEME_RE.match(target)
        if m:
            sch = m.group(1)
            if sch == "github":
                # github://<owner>/<repo> (also tolerate github://github.com/<owner>/<repo>):
                # derive `repo` from the URI into the connector config so the bare documented
                # form works without an explicit `--config repo=…`. This mirrors how a local
                # path injects {root}; the plugin has no access to its own connector URI, so
                # the identity must be carried in config. Without it the github plugin's
                # _owner_repo() has no repo and the sync/read path raises a 500.
                rest = target[len("github://") :].strip("/")
                if rest.startswith("github.com/"):
                    rest = rest[len("github.com/") :]
                parts = [p for p in rest.split("/") if p]
                cfg = {"repo": f"{parts[0]}/{parts[1]}"} if len(parts) >= 2 else {}
                return "github", target, "github", cfg
            if sch in (
                "web",
                "github",
                "postgres",
                "mysql",
                "mongo",
                "slack",
                "discord",
                "gmail",
                "notion",
                "jira",
                "linear",
                "zendesk",
                "salesforce",
                "hubspot",
                "bigquery",
                "snowflake",
                "s3",
                "gdrive",
                "feishu",
            ):
                return sch, target, sch, {}
            if sch != "file":
                raise NotImplementedError(f"connector scheme '{sch}' not yet implemented")
        # file:///abs/path — empty authority — is the canonical URI for a LOCAL path
        #: treat it as the local path, not an upload identity, so
        # `mfs add file:///abs/path` registers with a real root instead of failing.
        if target.startswith("file:///"):
            abs_path = os.path.abspath(target[len("file://") :])
            return (
                "file",
                f"file://local{abs_path}",
                "file",
                {"root": abs_path, "client_id": "local"},
            )
        # canonical local URI: file://local<abs_path> — what `mfs connector list` prints.
        # Map it back to the same (root, connector_uri) a bare path would resolve to,
        # so inspect/remove/update accept the identifier `connector list` shows.
        if target.startswith("file://local/"):
            abs_path = target[len("file://local") :]
            return (
                "file",
                f"file://local{abs_path}",
                "file",
                {"root": abs_path, "client_id": "local"},
            )
        # logical upload identity file://<client_id><abs> (client_id != local): the real
        # config (staging root) lives on the already-registered connector, so return bare.
        if target.startswith("file://") and not target.startswith("file://local"):
            return "file", target, "file", {}
        # local path -> file connector
        abs_path = os.path.abspath(target)
        connector_uri = f"file://local{abs_path}"
        return "file", connector_uri, "file", {"root": abs_path, "client_id": "local"}

    # substrings that mark a config key as holding a secret. Matched case-insensitively
    # anywhere in the key, and recursively (nested OAuth token dicts, lists), so e.g.
    # secret_access_key / refresh_token / client_secret are all caught.
    # dsn (postgres) and session_id (salesforce) carry credentials but don't contain any
    # of the obvious words; we DON'T add 'uri'/'url' here because those also name benign
    # fields (mongo's password is caught by the value check below, while salesforce's
    # instance_url and the web connector's target urls must be kept).
    _SECRET_SUBSTRINGS = (
        "token",
        "secret",
        "password",
        "passwd",
        "apikey",
        "api_key",
        "access_key",
        "private_key",
        "refresh",
        "credential",
        "dsn",
        "session_id",
    )
    # credential-reference schemes that are actually resolved (see _resolve_ref). Only these
    # are treated as safe (kept, not redacted); anything else under a secret key is redacted,
    # so an unimplemented scheme can't masquerade as a working ref and silently fail auth.
    _CRED_REF_PREFIXES = ("env:", "file:")
    # a connection string carrying inline credentials: scheme://user:password@host…
    # (postgres://u:p@…, mongodb://u:p@…). A plain URL with no userinfo password is NOT
    # matched, so web targets / instance_url stay intact.
    _CONN_URI_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+.\-]*://[^/\s:@]+:[^/\s@]+@")
    _REDACTED = "<redacted: use credential_ref=env:VAR>"

    @classmethod
    def _is_secret_key(cls, key: str) -> bool:
        kl = str(key).lower()
        return any(s in kl for s in cls._SECRET_SUBSTRINGS)

    @classmethod
    def _redact_config(cls, value, key_is_secret: bool = False):
        """Recursively redact raw inline secrets from a config before persistence. A
        credential_ref (env:/secret:/file:/vault:) is kept; anything else under a
        secret-looking key is replaced. Recurses into dicts/lists so nested OAuth token
        dicts don't leak."""
        if isinstance(value, dict):
            return {k: cls._redact_config(v, cls._is_secret_key(k)) for k, v in value.items()}
        if isinstance(value, list):
            return [cls._redact_config(v, key_is_secret) for v in value]
        if isinstance(value, str) and value.startswith(cls._CRED_REF_PREFIXES):
            return value  # a safe credential reference, keep as-is
        if key_is_secret and value not in (None, "", [], {}):
            return cls._REDACTED
        # value-level catch: an inline connection string carrying a password leaks via a
        # field name (dsn/uri/url/connection) that doesn't look secret — redact by shape.
        if isinstance(value, str) and cls._CONN_URI_RE.search(value):
            return cls._REDACTED
        return value

    async def register_or_get_connector(
        self, connector_uri: str, ctype: str, config: dict, overwrite_config: bool = False
    ) -> str:
        import json

        stored = self._redact_config(config)
        row = await self.meta.fetchone(
            "SELECT id, config_json FROM connectors WHERE namespace_id=? AND root_uri=?",
            (self.ns, connector_uri),
        )
        if row:
            # `mfs connector update --config` re-registers an existing connector: refresh
            # its stored config so changed text_fields / scope / credential_ref take effect.
            # `mfs add --config` on an already-registered connector persists the new config
            # and WARNs about the indexing implication, rather than silently dropping it.
            # Existing chunks are NOT re-embedded
            # under the new config until the user re-syncs with --force-index. The
            # warning is suppressed when nothing actually changed (same config dict
            # passed by the upload-mode staging shortcut on every call).
            new_json = json.dumps(stored, sort_keys=True)
            old_json = row["config_json"] or "{}"
            drift = _normalize_json(new_json) != _normalize_json(old_json)
            if overwrite_config or drift:
                await self.meta.execute(
                    "UPDATE connectors SET config_json=? WHERE id=?",
                    (json.dumps(stored), row["id"]),
                )
                if drift and not overwrite_config:
                    # Count what's actually at risk so the warning is concrete
                    # rather than scary boilerplate.
                    n = await self.meta.fetchone(
                        "SELECT count(*) AS n FROM objects "
                        "WHERE connector_id=? AND search_status='indexed'",
                        (row["id"],),
                    )
                    indexed = (n or {}).get("n", 0)
                    print(
                        f"mfs-server: WARNING --config differs from stored config for "
                        f"{connector_uri}; persisted, but {indexed} existing indexed "
                        f"object(s) retain the OLD config until you re-sync with "
                        f"`mfs add --force-index`.",
                        flush=True,
                    )
            return row["id"]
        cid = uuid.uuid4().hex
        await self.meta.execute(
            "INSERT INTO connectors (id, namespace_id, root_uri, type, status, config_json, registered_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (cid, self.ns, connector_uri, ctype, "active", json.dumps(stored), _now()),
        )
        return cid

    @staticmethod
    def _resolve_ref(v):
        """Resolve a credential reference to its actual value: `env:VAR` ->
        environment, `file:/path` -> the file's contents (k8s/docker secret mounts).
        Non-ref values pass through unchanged. These are the only schemes _CRED_REF_PREFIXES
        advertises, so a ref left unresolved (and silently used as a literal token) can't
        happen."""
        if isinstance(v, str):
            if v.startswith("env:"):
                name = v[4:]
                if name not in os.environ:
                    raise ValueError(
                        f"credential_ref {v!r}: environment variable {name} is not set"
                    )
                return os.environ[name]
            if v.startswith("file:"):
                try:
                    with open(v[5:], encoding="utf-8") as f:
                        return f.read().strip()
                except OSError as e:
                    raise ValueError(f"credential_ref {v!r}: cannot read secret file ({e})") from e
            if v.startswith(("secret:", "vault:")):
                # advertised-looking but unimplemented schemes must fail loudly, never be
                # used as a literal credential token.
                raise ValueError(
                    f"credential_ref scheme {v.split(':', 1)[0]!r} is not implemented "
                    f"(use env: or file:)"
                )
        return v

    def _build_plugin(self, ctype: str, config: dict, connector_id: str):
        cls = get_plugin_cls(ctype)
        if cls is None:
            raise NotImplementedError(f"no plugin for {ctype}")
        # Resolve credential references at build time so secrets live in the environment,
        # not in connectors.config_json. The stored config keeps the `env:VAR`
        # ref / `_credential_ref`; only this in-memory copy carries resolved values.
        credential = None
        if isinstance(config, dict):
            config = {k: self._resolve_ref(v) for k, v in config.items()}
            # design name is `credential_ref`; accept `_credential_ref` as a legacy alias
            cred_a = config.pop("credential_ref", None)
            cred_b = config.pop("_credential_ref", None)
            credential = cred_a if cred_a is not None else cred_b
        objects_cfg = config.get("objects", []) if isinstance(config, dict) else []
        state = ConnectorStateStore(self.meta, connector_id)
        ctx = ConnectorContext(state, connector_id, self.ns, object_config_resolver=None)
        if ctype == "file":
            from ..connectors.file.plugin import FileConfig

            plugin = cls(
                FileConfig(
                    root=config["root"],
                    client_id=config.get("client_id", "local"),
                    upload_mode=config.get("upload_mode", False),
                ),
                credential,
                ctx=ctx,
            )
            plugin.file_state = FileStateStore(self.meta, self.ns, connector_id)
        else:
            plugin = cls(config, credential, ctx=ctx)

        # resolver: user [[objects]] match wins; else the connector's built-in preset
        # so SaaS sources are searchable with zero config.
        from dataclasses import replace as _replace

        from ..connectors.base import preset_object_config

        _CHUNK_MAX_DEFAULT = ObjectConfig.__dataclass_fields__["chunk_max"].default

        def _resolve_cfg(p: str) -> ObjectConfig:
            user = _match_object_config(objects_cfg, p)
            if user is not None:
                oc = user
            else:
                preset_key = plugin.preset_for(p)
                oc = (
                    (preset_object_config(preset_key) or ObjectConfig())
                    if preset_key
                    else ObjectConfig()
                )
            # framework-level chunk cap applies unless this object config set its own
            if (
                oc.chunk_max == _CHUNK_MAX_DEFAULT
                and self.cfg.chunking.default_chunk_max != _CHUNK_MAX_DEFAULT
            ):
                oc = _replace(oc, chunk_max=self.cfg.chunking.default_chunk_max)
            return oc

        ctx._resolver = _resolve_cfg
        return plugin, ctx

    # --- add (register + sync + worker) ---
    async def add(
        self,
        target: str,
        config: dict | None = None,
        full: bool = False,
        since: str | None = None,
        process: bool = True,
        update_config: bool = False,
    ) -> str:
        """Register + sync + enqueue tasks. process=True (AIO default): run the job
        inline and return when done. process=False: leave the job 'queued' for a
        standalone worker to pick up via run_worker_*(). On an already-
        registered connector, --config is ignored unless update_config (change
        config via `mfs connector update`, not a re-sync)."""
        import json

        _, connector_uri, ctype, default_config = self._resolve_target(target)
        # --since requires the plugin to actually filter records by opts.since.
        # cursor_kind only names which field the plugin's own delta logic uses;
        # since_pushdown is the explicit "I honor opts.since" opt-in. Reject
        # early on connectors that lack it — better than silently full-scanning
        # and letting the user believe --since worked.
        if since:
            cls = get_plugin_cls(ctype)
            if cls is not None and not getattr(cls.CAPABILITIES, "since_pushdown", False):
                raise ValueError("since_unsupported")
        # merge user config OVER the resolved defaults so a local path keeps its
        # auto {root, client_id} while still accepting [[objects]]/schemas/credential_ref.
        cfg_dict = {**default_config, **config} if config is not None else default_config
        cid = await self.register_or_get_connector(
            connector_uri, ctype, cfg_dict, overwrite_config=update_config
        )
        row0 = await self.meta.fetchone(
            "SELECT config_json, status FROM connectors WHERE id=?", (cid,)
        )
        if row0 and row0["status"] == "removing":
            raise ValueError("connector_removing")
        # this session uses the caller's full config (raw secrets intact); the persisted copy
        # is redacted. A later re-sync/worker rebuild reads the persisted config and resolves
        # secrets from credential_ref (env) — so persistent runs must use credential_ref.
        stored_cfg = (
            cfg_dict
            if config is not None
            else (json.loads(row0["config_json"]) if row0 and row0["config_json"] else cfg_dict)
        )

        job_id = await self._open_sync_job(cid, process)
        return await self._drain_job(
            job_id, cid, connector_uri, ctype, stored_cfg, full, since, process
        )

    async def _open_sync_job(self, cid: str, process: bool) -> str:
        """Reserve the one-in-flight-sync slot for a connector and inherit
        its leftover tasks. Raises connector_removing / sync_already_running. Callers that
        mutate state (e.g. upload) MUST call this BEFORE mutating, so a rejected sync leaves
        nothing half-applied."""
        row = await self.meta.fetchone("SELECT status FROM connectors WHERE id=?", (cid,))
        if row and row["status"] == "removing":
            raise ValueError("connector_removing")
        job_id = uuid.uuid4().hex
        try:
            await self.meta.execute(
                "INSERT INTO connector_jobs (id, namespace_id, connector_id, op_kind, trigger, status, "
                " started_at, heartbeat) VALUES (?,?,?,?,?,?,?,?)",
                (
                    job_id,
                    self.ns,
                    cid,
                    "sync",
                    "manual",
                    "running" if process else "preparing",
                    _now(),
                    _now(),
                ),
            )
        except Exception as e:  # noqa: BLE001 - unique-violation: one running/queued per connector
            if "unique" in str(e).lower() or "constraint" in str(e).lower():
                raise ValueError("sync_already_running") from e
            raise
        await self.meta.execute(
            "UPDATE object_tasks SET connector_job_id=?, status='pending' "
            "WHERE connector_id=? AND status IN ('pending','failed') AND attempts < ? "
            "AND change_kind != 'dir_summary'",
            (job_id, cid, self.cfg.object_task.max_retries),
        )
        return job_id

    async def _drain_job(
        self,
        job_id: str,
        cid: str,
        connector_uri: str,
        ctype: str,
        stored_cfg: dict,
        full: bool,
        since: str | None,
        process: bool,
    ) -> str:
        """Run a reserved sync job: enumerate (plugin.sync) -> enqueue object_tasks ->
        process inline (process=True) or leave queued for a worker."""
        plugin = None
        ctx = None
        aborted: str | None = None
        try:
            # build/connect/enumerate are all inside the try: if any of them raises (e.g. a
            # real filesystem permission error during plugin.sync()), the reserved job would
            # otherwise stay 'running' (process=True) or 'queued' (process=False) forever,
            # and the connector's next sync would hit ux_jobs_one_* -> sync_already_running.
            plugin, ctx = self._build_plugin(ctype, stored_cfg, cid)
            await plugin.connect()
            opts = SyncOptions(full=full, since=since)
            # Keep the job's heartbeat warm during enumeration too: a slow-enumerating
            # connector would otherwise look stale and be reclaimed mid-enumeration (the
            # job is 'running'/'preparing' here with no other heartbeat source).
            stop_hb = asyncio.Event()
            hb = asyncio.create_task(self._heartbeat_loop(job_id, stop_hb))
            # Reduce subsystem: build this job's in-memory dir tree as sync() yields (§6.4).
            self._reduce.register_job(job_id, connector_uri, plugin)
            try:
                async for ch in plugin.sync(opts):
                    if ch.kind == "deleted" and (
                        ctx.enumeration_mode == "incremental"
                        or getattr(plugin.CAPABILITIES, "delete_detection", "") == "never"
                    ):
                        # only the unsafe 'incremental' mode skips deletes; 'full' (diff) and
                        # 'explicit_only' (yielded events, e.g. upload) honor them
                        continue  # never-delete connectors (slack/gmail) keep the index
                    tid = uuid.uuid4().hex
                    await self.meta.execute(
                        "INSERT INTO object_tasks (id, connector_job_id, connector_id, object_uri, old_uri, "
                        " change_kind, status, priority, attempts) VALUES (?,?,?,?,?,?,?,?,0)",
                        (
                            tid,
                            job_id,
                            cid,
                            ch.uri,
                            ch.old_uri,
                            ch.kind,
                            "pending",
                            plugin.task_priority(ch),
                        ),
                    )
                    if ch.kind != "deleted":
                        # Accumulate the dir tree (okind passed in — no extra DB hit, §6.4.1),
                        # but ONLY for okinds that actually flow through the EmbedConsumer. A
                        # non-pipeline okind (binary, image with [description] off, table_schema
                        # with [summary] off) takes the inline tail and never fires
                        # on_embed_succeeded, so counting it would leave its parent dir's pending
                        # stuck and the job's reduce phase would never finish.
                        okind = plugin.object_kind_of(ch.uri)
                        if self._routes_to_pipeline(okind):
                            self._reduce.on_yield_object_change(job_id, ch.uri, okind)
            finally:
                stop_hb.set()
                hb.cancel()
                try:
                    await hb
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            # sync enumeration finished: finalize the dir tree (pushes ready leaves; the rest
            # are pushed as Map file tasks succeed). Done for both inline and enqueue models so
            # an in-process worker can drain the summaries later.
            self._reduce.on_sync_done(job_id)
            if not process:
                # enqueue model: stash staged state on the job; the worker commits it only
                # after the job succeeds, so a failed background job doesn't
                # advance the cursor past objects that never got indexed.
                await self.meta.execute(
                    "UPDATE connector_jobs SET state_snapshot=? WHERE id=?",
                    (json.dumps(ctx.state.snapshot()), job_id),
                )
                # ONLY NOW expose the job to workers: it was 'preparing' (unclaimable) during
                # enumeration, so a worker couldn't claim it, find zero tasks, and finalize it
                # 'succeeded' before the tasks above were inserted (lost-task race).
                await self.meta.execute(
                    "UPDATE connector_jobs SET status='queued' WHERE id=? AND status='preparing'",
                    (job_id,),
                )
                return job_id
            aborted = await self._run_job(job_id, cid, connector_uri, plugin)
        except Exception as e:  # noqa: BLE001
            # drop the half-enqueued (incomplete enumeration) tasks and finalize the job
            # 'failed' so the in-flight slot is freed, then re-raise for the caller/API.
            await self.meta.execute(
                "UPDATE object_tasks SET status='cancelled' "
                "WHERE connector_job_id=? AND status='pending'",
                (job_id,),
            )
            await self._finalize_job(job_id, f"sync_error: {e}")
            raise
        finally:
            if plugin is not None:
                try:
                    await plugin.close()
                except Exception:  # noqa: BLE001
                    pass
        if aborted is None:
            await ctx.state.commit()
        await self._finalize_job(job_id, aborted)
        return job_id

    async def ingest_upload(
        self, name: str, data: bytes, fmt: str = "tar", process: bool = True
    ) -> dict:
        """CS upload flow: client/server don't share a fs, so the client
        ships a tar(.gz) of the tree (?name=<label>). The label is the connector's stable
        identity file://<name> — the SAME file://<client_id><root> shape the manifest-diff
        flow uses — so the upload is searchable / removable by that logical URI rather than
        by the server's internal staging path (which the old code leaked as file://local…,
        diverging from the manifest flow). Full-tree snapshot; guards zip-slip."""
        import hashlib
        import io
        import tarfile

        # Validate the body IS a readable, non-empty tar BEFORE registering a connector, so a
        # garbage / empty bundle returns a clean 400 and leaves no residual connector behind
        # (a non-tar throws tarfile.ReadError; an all-zero body parses as an empty archive).
        try:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as _probe:
                members = _probe.getmembers()
                if not members:
                    raise ValueError("invalid or empty upload bundle")
                for m in members:
                    _validate_upload_member(m)
        except tarfile.TarError as e:
            raise ValueError("invalid or empty upload bundle") from e

        staging, connector_uri, cid = await self._staging_connector(name, "")
        fs = FileStateStore(self.meta, self.ns, cid)

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
            # reserve the sync slot BEFORE mutating staging/file_state (so a rejected sync —
            # sync_already_running — leaves nothing half-applied), then stage the tree.
            job_id = await self._open_sync_job(cid, process)
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
        crow = await self.meta.fetchone("SELECT config_json FROM connectors WHERE id=?", (cid,))
        stored_cfg = json.loads(crow["config_json"]) if crow and crow["config_json"] else {}
        await self._drain_job(job_id, cid, connector_uri, "file", stored_cfg, False, None, process)
        return {"job_id": job_id, "connector_uri": connector_uri, "staging": staging}

    # --- manifest-diff upload protocol: stable identity
    #     file://<client_id><abs-root>, byte-diff + index-diff both on the file_state table ---
    def _staging_root(self, client_id: str, root: str) -> str:
        import hashlib

        sub = hashlib.sha1(f"{client_id}:{root}".encode()).hexdigest()[:16]
        return os.path.realpath(str(self.artifact_cache.files_root(self.ns, sub)))

    async def _staging_connector(self, client_id: str, root: str):
        """(staging_dir, connector_uri, connector_id). The connector's stable identity is
        file://<client_id><client-abs-root> so the user can later search / remove by the
        original local path; the bytes physically live in a server-side staging dir."""
        staging = self._staging_root(client_id, root)
        connector_uri = f"file://{client_id}{root}"
        cid = await self.register_or_get_connector(
            connector_uri, "file", {"root": staging, "client_id": client_id, "upload_mode": True}
        )
        return staging, connector_uri, cid

    async def files_manifest(self, client_id: str, root: str, files: list[dict]) -> dict:
        """Step ②: diff the client's stat-only manifest against the
        server-side file_state (the same table the file connector uses) and return which
        paths' bytes are needed + deletion candidates (with sha1/inode for rename pairing)."""
        staging, connector_uri, cid = await self._staging_connector(client_id, root)
        fs = FileStateStore(self.meta, self.ns, cid)
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

        # Validate the bundle IS a readable, non-empty tar BEFORE registering a connector, so a
        # garbage / empty bundle returns a clean 400 and leaves no residual connector behind
        # (a non-tar throws tarfile.ReadError; an all-zero body parses as an empty archive).
        try:
            with tarfile.open(fileobj=io.BytesIO(bundle), mode="r:*") as _probe:
                members = _probe.getmembers()
                if not members:
                    raise ValueError("invalid or empty upload bundle")
                for m in members:
                    if m.name == ".mfs-meta.json":
                        if not m.isfile():
                            raise ValueError("invalid upload metadata")
                        continue
                    _validate_upload_member(m)
                mm = next((m for m in members if m.name == ".mfs-meta.json"), None)
                if mm:
                    _json.loads(_probe.extractfile(mm).read().decode())
        except tarfile.TarError as e:
            raise ValueError("invalid or empty upload bundle") from e

        staging, connector_uri, cid = await self._staging_connector(client_id, root)
        fs = FileStateStore(self.meta, self.ns, cid)

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
            job_id = await self._open_sync_job(cid, process)

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
        crow = await self.meta.fetchone("SELECT config_json FROM connectors WHERE id=?", (cid,))
        stored_cfg = _json.loads(crow["config_json"]) if crow and crow["config_json"] else {}
        # full=True (--force-index / --force-upload): upload-mode sync also re-yields the
        # already-indexed staging rows so a forced rebuild re-embeds the whole tree.
        await self._drain_job(job_id, cid, connector_uri, "file", stored_cfg, full, None, process)
        return {"job_id": job_id, "connector_uri": connector_uri, "staging": staging}

    async def _finalize_job(self, job_id: str, aborted: str | None) -> None:
        """Set terminal job status + per-status object counts."""
        counts = await self.meta.fetchall(
            "SELECT status, count(*) AS n FROM object_tasks WHERE connector_job_id=? GROUP BY status",
            (job_id,),
        )
        cmap = {r["status"]: r["n"] for r in counts}
        jrow = await self.meta.fetchone("SELECT status FROM connector_jobs WHERE id=?", (job_id,))
        if jrow and jrow["status"] == "cancelled":
            status = "cancelled"
        elif aborted:
            status = "failed"
        else:
            status = "succeeded"
        await self.meta.execute(
            "UPDATE connector_jobs SET status=?, finished_at=?, error=?, "
            " total_objects=?, succeeded_objects=?, failed_objects=?, cancelled_objects=? WHERE id=?",
            (
                status,
                _now(),
                aborted,
                sum(cmap.values()),
                cmap.get("succeeded", 0),
                cmap.get("failed", 0),
                cmap.get("cancelled", 0),
                job_id,
            ),
        )
        # job reached a terminal state: free the Reduce subsystem's in-memory dir tree (§6.4.6)
        if self._reduce is not None:
            self._reduce.evict_job(job_id)

    # --- standalone worker: poll DB queue, process queued jobs ---
    async def cancel_job(self, job_id: str) -> bool:
        """Cancel a job: mark it + its pending/running tasks cancelled. A running
        worker stops at the next per-object boundary (checked in _run_job)."""
        row = await self.meta.fetchone("SELECT status FROM connector_jobs WHERE id=?", (job_id,))
        if not row or row["status"] in ("succeeded", "failed", "cancelled"):
            return False
        await self.meta.execute(
            "UPDATE object_tasks SET status='cancelled' "
            "WHERE connector_job_id=? AND status IN ('pending','running')",
            (job_id,),
        )
        await self.meta.execute(
            "UPDATE connector_jobs SET status='cancelled', finished_at=? WHERE id=?",
            (_now(), job_id),
        )
        return True

    async def _claim_queued_job(self) -> dict | None:
        """Atomically claim the oldest queued job. Multi-worker safe: the claim is a
        conditional UPDATE guarded on status='queued', and we take the job only when
        *this* worker's UPDATE flipped the row (rowcount == 1). Two workers racing the
        same job -> only one's UPDATE matches; the loser tries the next candidate."""
        candidates = await self.meta.fetchall(
            "SELECT * FROM connector_jobs WHERE status='queued' ORDER BY started_at LIMIT 8"
        )
        for row in candidates:
            won = await self.meta.execute_rowcount(
                "UPDATE connector_jobs SET status='running', heartbeat=? WHERE id=? AND status='queued'",
                (_now(), row["id"]),
            )
            if won == 1:
                return row
        return None

    async def run_worker_once(self) -> str | None:
        """Claim + process one queued job. Returns its id, or None if queue empty."""
        import json

        job = await self._claim_queued_job()
        if not job:
            return None
        cid = job["connector_id"]
        crow = await self.meta.fetchone(
            "SELECT root_uri, type, config_json FROM connectors WHERE id=?", (cid,)
        )
        connector_uri, ctype = crow["root_uri"], crow["type"]
        stored_cfg = json.loads(crow["config_json"]) if crow["config_json"] else {}
        plugin = None
        try:
            plugin, _ = self._build_plugin(ctype, stored_cfg, cid)
            # Bound connect(): an unreachable/hanging connector (or one whose persisted creds
            # no longer resolve) must fail THIS job cleanly, not block the single in-process
            # sqlite worker forever — one bad connector cannot be allowed to wedge all ingest.
            await asyncio.wait_for(plugin.connect(), timeout=_WORKER_CONNECT_TIMEOUT_S)
            aborted = await self._run_job(job["id"], cid, connector_uri, plugin)
            await self._finalize_job(job["id"], aborted)
            # commit the deferred connector state only now that the job succeeded:
            # a failed/cancelled background job leaves the cursor where it was.
            if aborted is None:
                jrow = await self.meta.fetchone(
                    "SELECT state_snapshot, status FROM connector_jobs WHERE id=?", (job["id"],)
                )
                if jrow and jrow["status"] == "succeeded" and jrow["state_snapshot"]:
                    await ConnectorStateStore(self.meta, cid).apply(
                        json.loads(jrow["state_snapshot"])
                    )
        except Exception as e:  # noqa: BLE001
            # Move the claimed job to a terminal 'failed' state and release the worker, so the
            # queue keeps draining. Without this a connect timeout/exception would leave the
            # job stuck 'running' and (with the single sqlite worker) wedge every later job.
            reason = (
                "connector_unhealthy: connect timed out"
                if isinstance(e, asyncio.TimeoutError)
                else f"sync_error: {e}"
            )
            await self.meta.execute(
                "UPDATE object_tasks SET status='failed', finished_at=?, last_error=? "
                "WHERE connector_job_id=? AND status='running'",
                (_now(), str(reason)[:300], job["id"]),
            )
            await self.meta.execute(
                "UPDATE connector_jobs SET status='failed', finished_at=?, error=? "
                "WHERE id=? AND status IN ('running', 'queued')",
                (_now(), str(reason)[:300], job["id"]),
            )
            print(
                f"mfs-server: WARNING sync job {job['id']} for {connector_uri} failed: {reason}",
                flush=True,
            )
        finally:
            if plugin is not None:
                try:
                    await plugin.close()
                except Exception:  # noqa: BLE001
                    pass
        return job["id"]

    def _resolve_concurrency(self, concurrency=None) -> int:
        c = concurrency if concurrency is not None else self.cfg.chunks_producer.concurrency
        if c == "auto":
            return max(1, (os.cpu_count() or 2))
        try:
            return max(1, int(c))
        except (TypeError, ValueError):
            return 1

    async def _reclaim_stale_jobs(self, stale_after_s: int = _JOB_STALE_AFTER_S) -> None:
        """Housekeeping: a job whose worker died keeps status='running' (or 'preparing')
        with a stale heartbeat forever. Recover such jobs so a live worker resumes them.

        Each job is recovered independently and any error is LOGGED, never silently swallowed
        — a single un-recoverable job must not abort (and thus starve) the reclaim of every
        other orphan, which would wedge crash-recovery for all connectors."""
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=stale_after_s)).isoformat()

        # Fail stale 'preparing' jobs FIRST: one whose process died mid-enumeration never
        # started running, and while it lingers it holds the connector's ux_jobs_one_pending
        # slot — which would make the running->queued reset below raise a UNIQUE violation.
        try:
            stale_prep = await self.meta.fetchall(
                "SELECT id FROM connector_jobs WHERE status='preparing' "
                "AND heartbeat IS NOT NULL AND heartbeat < ?",
                (cutoff,),
            )
        except Exception as e:  # noqa: BLE001
            print(
                f"mfs-server: WARNING reclaim: listing stale preparing jobs failed: {e}", flush=True
            )
            stale_prep = []
        for j in stale_prep:
            try:
                await self.meta.execute(
                    "UPDATE connector_jobs SET status='failed', finished_at=?, "
                    "error='reclaimed: enumeration abandoned' WHERE id=? AND status='preparing'",
                    (_now(), j["id"]),
                )
            except Exception as e:  # noqa: BLE001
                print(
                    f"mfs-server: WARNING reclaim: failing stale preparing job {j['id']}: {e}",
                    flush=True,
                )

        try:
            stale = await self.meta.fetchall(
                "SELECT id, connector_id FROM connector_jobs WHERE status='running' "
                "AND heartbeat IS NOT NULL AND heartbeat < ?",
                (cutoff,),
            )
        except Exception as e:  # noqa: BLE001
            print(
                f"mfs-server: WARNING reclaim: listing stale running jobs failed: {e}", flush=True
            )
            return
        for j in stale:
            try:
                # If the connector still has another in-flight enqueue (queued OR a non-stale
                # 'preparing'), flipping this orphan to 'queued' would violate
                # ux_jobs_one_pending. Hand its in-flight tasks to that job and fail the orphan
                # instead. The 'preparing' case matters: a preparing sibling holds the pending
                # slot too, so without it the reclaim would raise UNIQUE and silently abort.
                sibling = await self.meta.fetchone(
                    "SELECT id FROM connector_jobs WHERE connector_id=? "
                    "AND status IN ('queued', 'preparing') AND id<>? LIMIT 1",
                    (j["connector_id"], j["id"]),
                )
                if sibling:
                    await self.meta.execute(
                        "UPDATE object_tasks SET status='pending', connector_job_id=? "
                        "WHERE connector_job_id=? AND status='running'",
                        (sibling["id"], j["id"]),
                    )
                    await self.meta.execute(
                        "UPDATE connector_jobs SET status='failed', finished_at=?, "
                        "error='reclaimed: superseded by in-flight job' WHERE id=? AND status='running'",
                        (_now(), j["id"]),
                    )
                    continue
                # reset the dead worker's in-flight tasks back to pending FIRST, else the
                # re-claiming worker sees only 'pending', finds none, and finalizes the job
                # 'succeeded' while a task is still stuck 'running' (P1 crash-recovery gap).
                await self.meta.execute(
                    "UPDATE object_tasks SET status='pending' "
                    "WHERE connector_job_id=? AND status='running'",
                    (j["id"],),
                )
                await self.meta.execute(
                    "UPDATE connector_jobs SET status='queued' WHERE id=? AND status='running'",
                    (j["id"],),
                )
            except Exception as e:  # noqa: BLE001 — one un-recoverable orphan must not starve the rest
                print(
                    f"mfs-server: WARNING reclaim: recovering stale running job {j['id']}: {e}",
                    flush=True,
                )

    async def run_worker_forever(self, poll_interval: float = 1.0, concurrency=None) -> None:
        """Drain the queued-job queue with `concurrency` parallel workers. Each worker
        atomically claims a distinct job (the conditional claim is race-free), so N
        connectors' sync jobs run in parallel. Idle workers run a housekeeping pass that
        reclaims jobs orphaned by a crashed worker (stale heartbeat)."""
        n = self._resolve_concurrency(concurrency)

        async def _loop() -> None:
            while True:
                try:
                    jid = await self.run_worker_once()
                except Exception:  # noqa: BLE001 — a single job must NEVER kill the worker
                    # coroutine; with the sqlite single worker that would wedge all ingest.
                    jid = None
                if jid is None:
                    await self._reclaim_stale_jobs()
                    await asyncio.sleep(poll_interval)

        await asyncio.gather(*[_loop() for _ in range(n)])

    async def _claim_batch(self, limit: int, connector_id: str) -> list[dict]:
        """Claim up to `limit` pending tasks for ONE connector, ordered by priority then age,
        so a worker coroutine picks up the highest-priority pending task across that connector's
        jobs (a late high-priority job interleaves with an older one). The `change_kind !=
        'dir_summary'` clause is a defensive guard: dir_summary is never enqueued as an
        object_task — the Reduce subsystem (§3.5) owns it entirely — so it only excludes a
        stray row.

        Scoped to connector_id because the worker loop processes each claimed task with the
        plugin bound to THIS connector. A global claim would hand another connector's task to
        the wrong plugin, reading the wrong source; a true cross-connector worker pool needs
        per-task plugin resolution, which is a separate change. Per-connector parallelism is
        preserved: each connector's drain runs its own loop(s)."""
        rows = await self.meta.fetchall(
            "SELECT * FROM object_tasks WHERE status='pending' AND connector_id=? "
            "AND change_kind != 'dir_summary' "
            "ORDER BY priority ASC, started_at ASC LIMIT ?",
            (connector_id, limit),
        )
        return await self._claim_rows(rows)

    async def _claim_rows(self, rows: list[dict]) -> list[dict]:
        """Take each candidate row with a conditional UPDATE guarded on status='pending';
        return only the rows this worker actually flipped (rowcount == 1), so concurrent
        workers never double-process a task."""
        claimed = []
        for r in rows:
            won = await self.meta.execute_rowcount(
                "UPDATE object_tasks SET status='running', started_at=?, attempts=attempts+1 "
                "WHERE id=? AND status='pending'",
                (_now(), r["id"]),
            )
            if won == 1:
                claimed.append(r)
        return claimed

    @staticmethod
    def _classify_error(e: Exception) -> str:
        """Classify an embedding/provider error:
          'auth'      — bad/unauthorized key (OpenAI 401 / AuthenticationError)
          'quota'     — billing/quota exhausted (insufficient_quota / 402)
          'retryable' — transient (429 rate-limit / 5xx / timeout)
        'auth' and 'quota' are GLOBAL and non-retryable: a known-bad key or empty balance
        fails identically for every object, so the caller aborts the whole job with the
        documented embedding_auth_failed / embedding_quota_exceeded code instead of grinding
        each object (and masking the run as a 0-indexed 'succeeded')."""
        m = str(e).lower()
        nm = type(e).__name__.lower()
        auth_markers = (
            "invalid_api_key",
            "invalid x-api-key",
            "authentication",
            "unauthorized",
            "permission denied",
            "401",
        )
        if any(k in m for k in auth_markers) or "authentication" in nm or "permissiondenied" in nm:
            return "auth"
        # quota exhausted is distinct from a transient 429 rate-limit (which stays retryable):
        # OpenAI signals it with insufficient_quota; 402 is payment-required.
        if "insufficient_quota" in m or "402" in m:
            return "quota"
        return "retryable"

    async def _process_with_retry(self, plugin, connector_uri: str, task: dict) -> str | None:
        """Returns None on success, 'fatal', 'retryable_exhausted', or 'skipped'.
        'skipped' means a local per-object event (source disappeared, path type changed)
        — the object is recorded as status='skipped' and the breaker is left alone."""
        import asyncio as _a

        max_r = self.cfg.object_task.max_retries
        for attempt in range(max_r + 1):
            try:
                # None = inline okind done (caller marks succeeded); "deferred" = pipeline
                # okind whose completion is flipped async by the EmbedConsumer success hook.
                return await self._index_object(plugin, connector_uri, task)
            except _PER_OBJECT_SKIP_ERRORS as e:
                # Source vanished / type changed mid-sync. No point retrying (the file
                # really is gone), and counting this toward the consecutive-fatal breaker
                # would let a `git checkout` / cleanup of 5+ files nuke a 500-file job
                # (D53). Record as 'skipped' so it's visible in object_tasks without
                # inflating failed_objects.
                await self.meta.execute(
                    "UPDATE object_tasks SET status='skipped', finished_at=?, last_error=? "
                    "WHERE id=?",
                    (_now(), f"{type(e).__name__}: source disappeared mid-sync", task["id"]),
                )
                uri = f"{connector_uri}{task.get('object_uri', '')}"
                print(
                    f"mfs-server: object {uri} skipped (source disappeared mid-sync)",
                    flush=True,
                )
                return "skipped"
            except Exception as e:  # noqa: BLE001
                kind = self._classify_error(e)
                if kind in ("auth", "quota"):
                    # Global, non-retryable provider failure: return the documented code so
                    # _run_job_loop aborts the whole job (status=failed, error=<code>) on the
                    # first occurrence rather than retrying or marking the run 'succeeded'.
                    code = "embedding_auth_failed" if kind == "auth" else "embedding_quota_exceeded"
                    await self.meta.execute(
                        "UPDATE object_tasks SET status='failed', finished_at=?, last_error=? WHERE id=?",
                        (_now(), f"{code}: {e}", task["id"]),
                    )
                    self._warn_object_failed(connector_uri, task, e)
                    return code
                if str(e).startswith("field_missing"):
                    # Deterministic [[objects]] config error (text_field key absent from the
                    # records) — retrying re-reads the same records to no avail. Fail this
                    # object immediately with the documented code so the user fixes the config.
                    await self.meta.execute(
                        "UPDATE object_tasks SET status='failed', finished_at=?, last_error=? WHERE id=?",
                        (_now(), str(e), task["id"]),
                    )
                    self._warn_object_failed(connector_uri, task, e)
                    return "retryable_exhausted"
                if attempt < max_r:
                    # A pipeline okind whose producer raised may have pumped partial chunks +
                    # bookkeeping into the EmbedConsumer before failing. Reset that per-task
                    # state so the re-pump below behaves like a fresh attempt (§6.1): runs
                    # delete_by_object again and counts only the retry's chunks.
                    if self._embed_consumer is not None:
                        self._embed_consumer.on_task_retry(task["id"])
                    # exponential backoff capped at backoff_max_ms: a flat
                    # initial-only sleep ignored backoff_max_ms entirely and hammered a
                    # rate-limited provider at a fixed cadence.
                    delay_ms = min(
                        self.cfg.object_task.backoff_initial_ms * (2**attempt),
                        self.cfg.object_task.backoff_max_ms,
                    )
                    await _a.sleep(delay_ms / 1000)
                    continue
                await self.meta.execute(
                    "UPDATE object_tasks SET status='failed', finished_at=?, last_error=? WHERE id=?",
                    (_now(), str(e), task["id"]),
                )
                self._warn_object_failed(connector_uri, task, e)
                return "retryable_exhausted"
        return "retryable_exhausted"

    @staticmethod
    def _warn_object_failed(connector_uri: str, task: dict, e: Exception) -> None:
        """One-line server-log WARNING when an object is finally marked failed. object_tasks
        rows (and their last_error) are pruned after the job, so without this a user only sees
        the aggregate `failed_objects: N` with no way to learn which object failed or why.
        Pairs with the 'Milvus backend:' / dim-mismatch startup logs."""
        uri = f"{connector_uri}{task.get('object_uri', '')}"
        reason = f"{type(e).__name__}: {e}".replace("\n", " ").strip()
        if len(reason) > 300:
            reason = reason[:297] + "..."
        print(f"mfs-server: WARNING object {uri} failed: {reason}", flush=True)

    async def _should_stop(self, job_id: str, cid: str) -> bool:
        """A task boundary must stop the job if it was cancelled OR its connector is being
        removed — so no _index_object runs (writing Milvus) after teardown begins."""
        jr = await self.meta.fetchone("SELECT status FROM connector_jobs WHERE id=?", (job_id,))
        if jr and jr["status"] == "cancelled":
            return True
        cr = await self.meta.fetchone("SELECT status FROM connectors WHERE id=?", (cid,))
        return bool(cr and cr["status"] == "removing")

    async def _heartbeat_loop(self, job_id: str, stop: asyncio.Event) -> None:
        """Keep a job's heartbeat fresh for the WHOLE time a worker holds it, on a fixed
        cadence independent of how long any single object takes. A per-task-only refresh
        let a single object slower than the stale window (large PDF convert + embed) look
        like a dead worker, so remove()/reclaim would cancel the job and tear the connector
        down mid-write — the orphan-chunk race again. Tying the heartbeat to this coroutine's
        liveness makes 'fresh heartbeat' mean exactly 'worker process alive': if the worker
        dies the loop stops with it and the heartbeat goes stale (the intended signal)."""
        while not stop.is_set():
            await self.meta.execute(
                "UPDATE connector_jobs SET heartbeat=? WHERE id=?", (_now(), job_id)
            )
            try:
                await asyncio.wait_for(stop.wait(), timeout=_HEARTBEAT_INTERVAL_S)
            except asyncio.TimeoutError:
                pass

    async def _run_job(self, job_id: str, cid: str, connector_uri: str, plugin) -> str | None:
        """Returns None on normal completion, or a circuit-breaker reason string.
        Consecutive fatal failures abort the job."""
        threshold = self.cfg.object_task.consecutive_fatal_threshold
        consec_fail = 0  # consecutive object failures (fatal OR retries exhausted)
        stop_hb = asyncio.Event()
        hb_task = asyncio.create_task(self._heartbeat_loop(job_id, stop_hb))
        try:
            r = await self._run_job_loop(job_id, cid, connector_uri, plugin, threshold, consec_fail)
            if r is not None:
                return r  # map phase aborted (cancel / circuit breaker)
            # The pump enqueued every map task without blocking; wait for the
            # EmbedConsumer to write them and flip their object_tasks status, so the job isn't
            # finalized before its chunks are in Milvus (§6.1) and the dir tree's file
            # notifications have all fired.
            await self._await_map_drained(job_id)
            if self.summary.enabled and self._reduce is not None:
                # Reduce subsystem (§3.5): the dir tree was accumulated during sync and is
                # driven bottom-up by the Map success notifications + the SummaryWorker pool.
                # Block until every directory_summary for this job is computed AND persisted,
                # so the job isn't marked succeeded before its summaries are in Milvus.
                await self._reduce.await_reduce_done(job_id)
            return None
        finally:
            stop_hb.set()
            hb_task.cancel()
            try:
                await hb_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    async def _await_map_drained(self, job_id: str) -> None:
        """Block until this job has no still-running map tasks — i.e. the EmbedConsumer has
        written every pumped task's chunks and the success hook flipped it to a terminal
        status. The heartbeat (held by _run_job) stays warm meanwhile; the consumer's idle
        flush guarantees forward progress so this can't wedge while the consumer is alive."""
        while True:
            row = await self.meta.fetchone(
                "SELECT count(*) AS n FROM object_tasks "
                "WHERE connector_job_id=? AND status='running'",
                (job_id,),
            )
            if (row["n"] if row else 0) == 0:
                return
            await asyncio.sleep(0.05)

    async def _run_job_loop(
        self, job_id: str, cid: str, connector_uri: str, plugin, threshold: int, consec_fail: int
    ) -> str | None:
        # Map phase claims this connector's pending object_tasks. dir_summary is not an
        # object_task — the Reduce subsystem owns it (§3.5), driven by the success notifications.
        while True:
            if await self._should_stop(job_id, cid):
                return "cancelled"
            tasks = await self._claim_batch(64, cid)
            if not tasks:
                break
            for t in tasks:
                # re-check the stop boundary before EACH task (not just per batch): a
                # concurrent cancel/remove can land mid-batch, and we must not keep writing
                # chunks for a connector being torn down. The heartbeat
                # is kept warm by the background _heartbeat_loop, so even a multi-minute
                # single object won't be mistaken for a dead worker.
                if await self._should_stop(job_id, cid):
                    return "cancelled"
                r = await self._process_with_retry(plugin, connector_uri, t)
                if r is None:
                    # inline okind (deleted/renamed/binary/metadata-only) done synchronously.
                    # only flip a task we still own: a conditional UPDATE means a task that
                    # was cancelled out from under us (remove/cancel) is NOT revived to succeeded.
                    await self.meta.execute(
                        "UPDATE object_tasks SET status='succeeded', finished_at=? "
                        "WHERE id=? AND status='running'",
                        (_now(), t["id"]),
                    )
                    consec_fail = 0
                elif r == "deferred":
                    # pipeline okind: chunks enqueued; the EmbedConsumer success hook flips this
                    # task to 'succeeded' once they're written. The pump does NOT block or mark.
                    consec_fail = 0
                elif r == "skipped":
                    # Per-object local event (source vanished / type changed); the row was
                    # already marked status='skipped' inside _process_with_retry. We DID
                    # make forward progress on the job (this object is no longer pending),
                    # so reset the breaker — otherwise a bursty wave of `rm`s would slowly
                    # accumulate consec_fail across many objects and still trip it later.
                    consec_fail = 0
                elif r in ("embedding_auth_failed", "embedding_quota_exceeded"):
                    # Global, non-retryable failure (bad key / no quota): abort the whole job
                    # at once with the documented code — every object would fail identically,
                    # so grinding them (or finishing 'succeeded' with 0 indexed) just hides it.
                    await self.meta.execute(
                        "UPDATE object_tasks SET status='cancelled' "
                        "WHERE connector_job_id=? AND status IN ('pending','running')",
                        (job_id,),
                    )
                    return r
                else:
                    # retryable_exhausted counts toward the breaker: a provider that rate-limits
                    # (429) or times out on every object is classified retryable, and without
                    # counting it the job would grind the whole connector, burning
                    # (max_retries+1) calls per object.
                    consec_fail += 1
                    if consec_fail >= threshold:
                        await self.meta.execute(
                            "UPDATE object_tasks SET status='cancelled' "
                            "WHERE connector_job_id=? AND status IN ('pending','running')",
                            (job_id,),
                        )
                        return "circuit_breaker_tripped"
        return None

    async def _read_text(self, plugin, relpath: str) -> str:
        return (await self._read_bytes(plugin, relpath)).decode("utf-8", errors="replace")

    async def _read_bytes(self, plugin, relpath: str) -> bytes:
        buf = bytearray()
        async for chunk in plugin.read(relpath):
            buf += chunk
        return bytes(buf)

    # --- artifact cache: bytes in the object store + a metadata row
    #     in artifact_cache, with LRU size eviction ---
    async def _put_artifact(self, ns: str, object_uri: str, kind: str, data: bytes) -> str:
        """Store artifact bytes and record/refresh its artifact_cache row (size +
        content fingerprint + timestamps), then run a throttled LRU sweep so the cache
        stays under budget. fingerprint = sha1(bytes) — lets a re-build detect a
        no-op (same content) and gives a stale-check handle."""
        import hashlib

        path = await asyncio.to_thread(self.artifact_cache.put_artifact, ns, object_uri, kind, data)
        now = _now()
        fp = hashlib.sha1(data).hexdigest()
        await self.meta.execute(
            "INSERT INTO artifact_cache (namespace_id, object_uri, artifact_kind, storage_path, "
            " fingerprint, size_bytes, built_at, last_accessed) VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(namespace_id, object_uri, artifact_kind) DO UPDATE SET "
            " storage_path=excluded.storage_path, fingerprint=excluded.fingerprint, "
            " size_bytes=excluded.size_bytes, built_at=excluded.built_at, last_accessed=excluded.last_accessed",
            (ns, object_uri, kind, str(path), fp, len(data), now, now),
        )
        self._artifact_writes += 1
        if self._artifact_writes % 16 == 0:
            await self._evict_artifacts_if_needed(ns)
        return path

    async def _drop_artifacts(self, ns: str, object_uri: str) -> None:
        """Delete all cached artifacts of an object (bytes + artifact_cache rows) — on
        object deletion so the cache doesn't retain orphaned bytes. 'raw_records' is the
        message_stream materialization (jsonl); a deleted Slack/Gmail object would otherwise
        leak it."""
        for kind in ("converted_md", "vlm_text", "head_cache", "raw_records"):
            try:
                await asyncio.to_thread(self.artifact_cache.delete_artifact, ns, object_uri, kind)
            except Exception:  # noqa: BLE001
                pass
        await self.meta.execute(
            "DELETE FROM artifact_cache WHERE namespace_id=? AND object_uri=?", (ns, object_uri)
        )

    async def _read_artifact(self, ns: str, object_uri: str, kind: str) -> bytes | None:
        """Fetch artifact bytes and bump last_accessed (LRU recency) when present."""
        data = await asyncio.to_thread(self.artifact_cache.get_artifact, ns, object_uri, kind)
        if data is not None:
            await self.meta.execute(
                "UPDATE artifact_cache SET last_accessed=? "
                "WHERE namespace_id=? AND object_uri=? AND artifact_kind=?",
                (_now(), ns, object_uri, kind),
            )
        return data

    async def _evict_artifacts_if_needed(self, ns: str) -> int:
        """Evict least-recently-accessed artifacts until total bytes fall under
        artifact_cache.max_size_gb. Returns the number evicted."""
        max_bytes = int(self.cfg.artifact_cache.max_size_gb * (1 << 30))
        row = await self.meta.fetchone(
            "SELECT sum(size_bytes) AS total FROM artifact_cache WHERE namespace_id=?", (ns,)
        )
        total = (row and row["total"]) or 0
        if total <= max_bytes:
            return 0
        victims = await self.meta.fetchall(
            "SELECT object_uri, artifact_kind, size_bytes FROM artifact_cache "
            "WHERE namespace_id=? ORDER BY last_accessed ASC",
            (ns,),
        )
        evicted = 0
        for v in victims:
            if total <= max_bytes:
                break
            try:
                await asyncio.to_thread(
                    self.artifact_cache.delete_artifact, ns, v["object_uri"], v["artifact_kind"]
                )
            except Exception:  # noqa: BLE001
                pass
            await self.meta.execute(
                "DELETE FROM artifact_cache WHERE namespace_id=? AND object_uri=? AND artifact_kind=?",
                (ns, v["object_uri"], v["artifact_kind"]),
            )
            total -= v["size_bytes"] or 0
            evicted += 1
        return evicted

    async def _index_object(self, plugin, connector_uri: str, task: dict) -> None:
        """Handle one object_task. Change-kind branches (deleted / renamed) run inline here;
        indexable objects that route to the pipeline are produced + embedded asynchronously
        (returns 'deferred'); everything else falls to the metadata-only tail. Per-object
        atomic: delete_by_object then upsert all of this object's chunks (§6.1)."""
        relpath = task["object_uri"]
        kind = task["change_kind"]
        cid = task["connector_id"]
        ns = self.ns
        full_uri = connector_uri + relpath

        if kind == "deleted":
            await self.meta.execute(
                "DELETE FROM objects WHERE connector_id=? AND object_uri=?", (cid, relpath)
            )
            await asyncio.to_thread(self.milvus.delete_by_object, ns, connector_uri, full_uri)
            await self._drop_artifacts(ns, full_uri)  # purge cached artifact bytes too
            await plugin.on_object_deleted(relpath)
            return

        if kind == "renamed" and task["old_uri"]:
            old_full = connector_uri + task["old_uri"]
            # rename = chunk_id rewrite, REUSE vectors (zero re-embed)
            old_chunks = await asyncio.to_thread(
                self.milvus.get_chunks_by_object, ns, connector_uri, old_full
            )
            if old_chunks:
                rows = []
                for ch in old_chunks:
                    loc = ch.get("locator")
                    rows.append(
                        {
                            "chunk_id": chunk_id(
                                ns, connector_uri, full_uri, ch["chunk_kind"], loc
                            ),
                            "namespace_id": ns,
                            "connector_uri": connector_uri,
                            "object_uri": full_uri,
                            "locator": loc,
                            "content": ch["content"],
                            "dense_vec": ch["dense_vec"],
                            "chunk_kind": ch["chunk_kind"],
                            "metadata": ch.get("metadata") or {},
                            "indexed_at": ch.get("indexed_at") or int(time.time() * 1000),
                        }
                    )
                await asyncio.to_thread(self.milvus.delete_by_object, ns, connector_uri, old_full)
                await asyncio.to_thread(self.milvus.upsert, ns, rows)
                await asyncio.to_thread(self.artifact_cache.move_artifacts, ns, old_full, full_uri)
                # move_artifacts moved the per-object dir; bring the artifact_cache
                # indirection rows along too so LRU bookkeeping (size accounting,
                # last_accessed bumps on cat) tracks the artifact under its new uri.
                artifact_rows = await self.meta.fetchall(
                    "SELECT artifact_kind FROM artifact_cache "
                    "WHERE namespace_id=? AND object_uri=?",
                    (ns, old_full),
                )
                for ar in artifact_rows:
                    new_storage = str(
                        self.artifact_cache.artifact_path(ns, full_uri, ar["artifact_kind"])
                    )
                    await self.meta.execute(
                        "UPDATE artifact_cache SET object_uri=?, storage_path=? "
                        "WHERE namespace_id=? AND object_uri=? AND artifact_kind=?",
                        (full_uri, new_storage, ns, old_full, ar["artifact_kind"]),
                    )
                st = await plugin.stat(relpath)
                await self.meta.execute(
                    "DELETE FROM objects WHERE connector_id=? AND object_uri=?",
                    (cid, task["old_uri"]),
                )
                await self.meta.execute(
                    "INSERT INTO objects (connector_id, object_uri, parent_path, type, media_type, size_hint, "
                    " fingerprint, indexable, last_seen, search_status, chunk_count, indexed_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(connector_id, object_uri) DO UPDATE SET "
                    " type=excluded.type, media_type=excluded.media_type, size_hint=excluded.size_hint, "
                    " fingerprint=excluded.fingerprint, indexable=excluded.indexable, last_seen=excluded.last_seen, "
                    " search_status=excluded.search_status, chunk_count=excluded.chunk_count, indexed_at=excluded.indexed_at",
                    (
                        cid,
                        relpath,
                        os.path.dirname(relpath) or "/",
                        st.type,
                        st.media_type,
                        st.size_hint,
                        st.fingerprint,
                        1,
                        _now(),
                        "indexed",
                        len(rows),
                        _now(),
                    ),
                )
                await plugin.on_object_indexed(relpath)
                return  # reused vectors — no chunk/embed
            # fallback (old had no chunks): drop refs, index new normally below
            await asyncio.to_thread(self.milvus.delete_by_object, ns, connector_uri, old_full)
            await self.meta.execute(
                "DELETE FROM objects WHERE connector_id=? AND object_uri=?", (cid, task["old_uri"])
            )

        st = await plugin.stat(relpath)
        okind = plugin.object_kind_of(relpath)
        chunk_count = 0
        search_status = "not_indexed"
        top_cfg = plugin.ctx.object_config_for(relpath)
        # `indexable` is binary-vs-not by object_kind, AND can be opted out per
        # [[objects]] config indexable=false: record the object so it
        # shows in ls/inspect, but skip all chunk/embed/Milvus work.
        indexable = okind not in ("binary",) and top_cfg.indexable

        if not indexable:
            pass  # binary / opted-out: metadata-only, no chunk/embed (gated below)
        elif self._routes_to_pipeline(okind):
            # Pipeline path (§3.1 / §3.2): document/code via TextChunksProducer, image via
            # ImageChunksProducer, etc. The Chunk stream is embedded + upserted by the process-
            # level EmbedConsumer; delete_by_object is done once by the consumer (first chunk).
            # The objects-table row + on_object_indexed are written by _on_pipeline_object_indexed
            # when the consumer reports the task done, so stash the per-object context and
            # return before the shared inline tail.
            self._pending_finalize[full_uri] = (cid, relpath, st, indexable, plugin, task["id"])
            await self._index_via_pipeline(
                plugin, connector_uri, relpath, full_uri, okind, task
            )
            # "deferred": the producer chunks are enqueued; the EmbedConsumer success hook
            # (_on_pipeline_object_indexed) writes the objects row + flips status when they land.
            # _run_job_loop must NOT mark this task succeeded — its chunks aren't written yet.
            return "deferred"

        # Directory summaries are NOT produced here per enumerated object. They are built
        # bottom-up by the independent Reduce subsystem (engine/reduce/, §3.5) from the
        # in-memory dir tree, so a parent folder's summary can fold in its children's summaries.

        # Inline tail — only reached for NON-pipeline okinds (pipeline okinds return early
        # above; their objects row is written by _on_pipeline_object_indexed).
        if chunk_count == 0:
            # A rebuild that produced no chunks (object became binary / indexable=false /
            # document emptied / empty VLM or summary) must still purge chunks from a previous
            # index, else search keeps returning stale content.
            await asyncio.to_thread(self.milvus.delete_by_object, ns, connector_uri, full_uri)
        await self._write_object_row(cid, relpath, st, indexable, search_status, chunk_count)
        await plugin.on_object_indexed(relpath)

    # --- search ---
    async def _has_registered_search_scope(self, connector_uri: str | None) -> bool:
        """Return whether a search scope can match registered connector-owned chunks.

        Searching an empty namespace, or a path that resolves to an unregistered connector
        URI, cannot produce hits. Fast-pathing that case avoids cold-starting the query
        embedder for a guaranteed-empty result.
        """
        if connector_uri is None:
            row = await self.meta.fetchone(
                "SELECT id FROM connectors WHERE namespace_id=? LIMIT 1", (self.ns,)
            )
        else:
            row = await self.meta.fetchone(
                "SELECT id FROM connectors WHERE namespace_id=? AND root_uri=? LIMIT 1",
                (self.ns, connector_uri),
            )
        return row is not None

    async def search(
        self,
        query: str,
        connector_uri: str | None = None,
        object_prefix: str | None = None,
        mode: str = "hybrid",
        top_k: int = 10,
        chunk_kinds: list[str] | None = None,
        collapse: bool = False,
    ) -> list[dict]:
        if top_k <= 0 or not query or not query.strip():
            return []  # nothing to ask for: skip the embed call and Milvus' limit>0 rule
        # Coarse fast-path: reject an absurd top_k before embedding/querying. Hybrid
        # over-fetches each sub-search by over_fetch_ratio, so the request Milvus sees is
        # top_k * ratio; other modes send top_k directly. This only catches values above the
        # hard window — the backend's real per-search cap is lower and backend-specific
        # (Milvus Lite tops out far below Zilliz), so MilvusStore translates the actual
        # MilvusException into the same `top_k_too_large` error as the authoritative guard.
        effective = top_k * self.cfg.search.over_fetch_ratio if mode == "hybrid" else top_k
        if effective > MILVUS_MAX_RESULT_WINDOW:
            raise ValueError("top_k_too_large")
        if not await self._has_registered_search_scope(connector_uri):
            return []
        expr = build_filter(self.ns, connector_uri, object_prefix, chunk_kinds)
        if mode == "keyword":
            hits = await asyncio.to_thread(self.milvus.sparse_search, self.ns, query, top_k, expr)
        else:
            qvec = (await self.embed.batch_embed([query]))[0]
            if mode == "semantic":
                hits = await asyncio.to_thread(self.milvus.search_dense, self.ns, qvec, top_k, expr)
            else:  # hybrid
                hits = await asyncio.to_thread(
                    self.milvus.hybrid_search,
                    self.ns,
                    qvec,
                    query,
                    top_k,
                    expr,
                    None,
                    self.cfg.search.over_fetch_ratio,
                )
        envs = [to_envelope(h) for h in hits]
        return collapse_by_object(envs) if collapse else envs

    async def resolve_connector_uri(self, target: str) -> tuple[str, str | None]:
        """Map a user path/URI to (connector_uri, object_prefix) for search/grep scope.
        Matches the registered connector whose root is the longest prefix of `target`,
        so `search q /repo/src` (after `add /repo`) scopes to the /src subtree instead
        of fabricating a brand-new connector_uri that would match no indexed chunks."""
        match = await self._match_connector(target)
        if match is None:
            # not under any registered connector: fall back to literal resolution so an
            # exact connector root still scopes correctly (object_prefix unknown -> None).
            _, connector_uri, _, _ = self._resolve_target(target)
            return connector_uri, None
        row, rel = match
        connector_uri = row["root_uri"]
        # stored chunk object_uri == connector_uri + relpath, so prefix on the full URI
        object_prefix = (connector_uri + rel) if rel not in ("", "/") else None
        return connector_uri, object_prefix

    # --- connector management: probe / inspect / remove ---
    async def probe(self, target: str, config: dict | None = None) -> dict:
        """Try-connect a connector without registering or writing state."""
        _, connector_uri, ctype, default_config = self._resolve_target(target)
        cfg_dict = {**default_config, **config} if config is not None else default_config
        plugin = None
        try:
            # Build inside the guard: _build_plugin resolves credential refs (_resolve_ref),
            # and a missing/unresolvable env:/file: ref is a user config error — it must come
            # back as ok=false like a failed connect/auth, not escape to the generic 500
            # handler. NotImplementedError (an uninstalled connector extra) is intentionally
            # NOT caught here so it still renders as the 501 not_available envelope.
            plugin, _ = self._build_plugin(ctype, cfg_dict, "probe-" + uuid.uuid4().hex)
            await plugin.connect()
            hs = await plugin.healthcheck()
            return {"target": connector_uri, "type": ctype, "ok": hs.ok, "detail": hs.detail}
        except NotImplementedError:
            raise
        except Exception as e:  # noqa: BLE001
            return {"target": connector_uri, "type": ctype, "ok": False, "detail": str(e)}
        finally:
            if plugin is not None:
                try:
                    await plugin.close()
                except Exception:  # noqa: BLE001
                    pass

    async def estimate(
        self,
        target: str,
        config: dict | None = None,
        sample_objects: int = 3,
        sample_records: int = 1000,
    ) -> dict:
        """Zero-billing pre-flight estimate: enumerate the object set
        (metadata-only) and run the chunker + local tokenizer on a small sample to
        extrapolate physical work (chunks / tokens). Never calls the embedding API or
        writes Milvus — the user sees the prompt before any money is spent. Returns
        physical quantities only (no $/time, per design)."""
        from ..processors.text import chunk_body

        _, connector_uri, ctype, default_config = self._resolve_target(target)
        cfg_dict = {**default_config, **config} if config is not None else default_config
        tmp_cid = "estimate-" + uuid.uuid4().hex
        plugin, _ = self._build_plugin(ctype, cfg_dict, tmp_cid)
        await plugin.connect()
        try:
            obj_uris: list[str] = []
            # dry_run: enumerate object URIs without hashing bytes or writing any state
            # estimate must be side-effect-free and cheap.
            async for ch in plugin.sync(SyncOptions(full=True, dry_run=True)):
                if ch.kind != "deleted":
                    obj_uris.append(ch.uri)
                if len(obj_uris) >= 200000:
                    break
            total = len(obj_uris)
            try:
                import tiktoken

                enc = tiktoken.get_encoding("cl100k_base")
                ntok = lambda s: len(enc.encode(s))  # noqa: E731
            except Exception:  # noqa: BLE001 - tokenizer unavailable -> ~4 chars/token
                ntok = lambda s: max(1, len(s) // 4)  # noqa: E731
            s_chunks = s_tokens = s_objs = 0
            for rel in obj_uris[:sample_objects]:
                okind = plugin.object_kind_of(rel)
                texts: list[str] = []
                if okind in ("document", "code", "text_blob"):
                    ext = os.path.splitext(rel)[1].lower()
                    text = await self._read_text(plugin, rel)
                    texts = [t for t, _ in chunk_body(text, okind, ext, self.cfg.chunking.chunk_size)]
                elif okind in ("table_rows", "record_collection", "message_stream"):
                    ocfg = plugin.ctx.object_config_for(rel)
                    records = plugin.read_records(rel)
                    sampled = 0  # records actually consumed (≤ sample_records)
                    if records is not None and ocfg.text_fields:
                        try:
                            async for rec in records:
                                t = render_record(rec, ocfg.text_fields, ocfg.render_template)
                                if t.strip():
                                    texts.append(t)
                                sampled += 1
                                if sampled >= sample_records:
                                    break
                        finally:
                            # Breaking out of `async for` does NOT close the async generator,
                            # so a connector that yields from inside `async with pool.acquire()`
                            # (mysql/postgres/mongo) keeps a DB connection pinned by the
                            # suspended generator; the estimate's `plugin.close()` then deadlocks
                            # in pool.wait_closed() waiting for that connection. Explicitly close
                            # the generator after the capped sample so the connection is released.
                            aclose = getattr(records, "aclose", None)
                            if aclose is not None:
                                await aclose()
                    # Ask the plugin for the real record count; if cheap and known,
                    # extrapolate per-record averages over the full count instead of
                    # summing the truncated sample. Otherwise fall back to summing
                    # what we sampled (matches the old behavior — a known
                    # under-count when one object contains many records).
                    rec_total: int | None = None
                    if okind in ("table_rows", "record_collection", "message_stream"):
                        try:
                            rec_total = await plugin.record_count(rel)
                        except Exception:  # noqa: BLE001 - estimate must never fail
                            rec_total = None
                    if rec_total is not None and rec_total > sampled > 0 and texts:
                        per_rec_chunks = len(texts) / sampled
                        per_rec_tokens = sum(ntok(t) for t in texts) / sampled
                        s_chunks += per_rec_chunks * rec_total
                        s_tokens += per_rec_tokens * rec_total
                        texts = []  # already accounted for, don't re-count below
                if texts:
                    s_chunks += len(texts)
                    s_tokens += sum(ntok(t) for t in texts)
                s_objs += 1
            per_chunks = (s_chunks / s_objs) if s_objs else 0
            per_tokens = (s_tokens / s_objs) if s_objs else 0
            return {
                "target": connector_uri,
                "type": ctype,
                "objects": total,
                "sampled_objects": s_objs,
                "est_chunks": int(per_chunks * total),
                "est_tokens": int(per_tokens * total),
            }
        finally:
            try:
                await plugin.close()
            except Exception:  # noqa: BLE001
                pass
            # belt-and-suspenders: drop any rows a connector's sync may have written under
            # the throwaway estimate id (dry_run covers file; this catches the rest so a
            # probe/estimate can never accrete orphan state nothing will ever clean up).
            for tbl in ("file_state", "connector_state"):
                try:
                    await self.meta.execute(f"DELETE FROM {tbl} WHERE connector_id=?", (tmp_cid,))
                except Exception:  # noqa: BLE001
                    pass

    async def inspect(self, target: str) -> dict | None:
        """Connector row + object/job summary."""
        _, connector_uri, _, _ = self._resolve_target(target)
        row = await self.meta.fetchone(
            "SELECT id, root_uri, type, status, registered_at FROM connectors "
            "WHERE namespace_id=? AND root_uri=?",
            (self.ns, connector_uri),
        )
        if not row:
            return None
        cid = row["id"]
        objs = await self.meta.fetchall(
            "SELECT search_status, count(*) AS n FROM objects WHERE connector_id=? GROUP BY search_status",
            (cid,),
        )
        jobs = await self.meta.fetchall(
            "SELECT status, count(*) AS n FROM connector_jobs WHERE connector_id=? GROUP BY status",
            (cid,),
        )
        total = await self.meta.fetchone(
            "SELECT count(*) AS n, sum(chunk_count) AS chunks FROM objects WHERE connector_id=?",
            (cid,),
        )
        return {
            **dict(row),
            "objects": {o["search_status"]: o["n"] for o in objs},
            "object_count": total["n"] or 0,
            "chunk_count": total["chunks"] or 0,
            "jobs": {j["status"]: j["n"] for j in jobs},
        }

    async def remove_connector(self, target: str) -> bool:
        """Remove a connector and everything it owns: Milvus chunks, artifacts, and all
        metadata rows (objects / tasks / jobs / state / file_state)."""
        _, connector_uri, _, _ = self._resolve_target(target)
        row = await self.meta.fetchone(
            "SELECT id FROM connectors WHERE namespace_id=? AND root_uri=?",
            (self.ns, connector_uri),
        )
        if not row:
            return False
        cid = row["id"]
        # preempt any in-flight sync. Mark 'removing' (new syncs ->
        # connector_removing; a running worker observes it at its next task boundary via
        # _should_stop and exits). Cancel only the not-yet-started work (queued job +
        # pending tasks). Crucially DON'T flip the running job ourselves — its status
        # leaving 'running' is the signal that the worker has exited _run_job and no
        # _index_object is mid-write; only then is it safe to delete the data.
        await self.meta.execute("UPDATE connectors SET status='removing' WHERE id=?", (cid,))
        await self.meta.execute(
            "UPDATE object_tasks SET status='cancelled' WHERE connector_id=? AND status='pending'",
            (cid,),
        )
        await self.meta.execute(
            "UPDATE connector_jobs SET status='cancelled', finished_at=? "
            "WHERE connector_id=? AND status IN ('queued','preparing')",
            (_now(), cid),
        )
        # Wait for the worker to leave 'running' — that transition (set in _finalize_job
        # after _run_job's loop exits) is the proof the last _index_object's Milvus upsert
        # has completed, so it's the only safe moment to delete. Don't bound this by wall
        # clock (the old ~10s cap would delete out from under an object still mid-write,
        # re-opening the orphan-chunk race); instead trust the heartbeat. A live worker
        # refreshes it per task, so we keep waiting; only a stale heartbeat means the
        # worker died/stuck, in which case WE take the job over and then delete.
        stale_after_s = _JOB_STALE_AFTER_S
        while True:
            running = await self.meta.fetchone(
                "SELECT id, heartbeat FROM connector_jobs WHERE connector_id=? AND status='running'",
                (cid,),
            )
            if not running:
                break
            cutoff = (datetime.now(timezone.utc) - timedelta(seconds=stale_after_s)).isoformat()
            if not running["heartbeat"] or running["heartbeat"] < cutoff:
                # worker is dead or wedged — reclaim: cancel its in-flight tasks + the job
                # so the 'running' row clears and no later write can resurrect it.
                await self.meta.execute(
                    "UPDATE object_tasks SET status='cancelled' "
                    "WHERE connector_job_id=? AND status IN ('pending','running')",
                    (running["id"],),
                )
                await self.meta.execute(
                    "UPDATE connector_jobs SET status='cancelled', finished_at=? "
                    "WHERE id=? AND status='running'",
                    (_now(), running["id"]),
                )
                break
            await asyncio.sleep(0.1)
        # 1. Milvus chunks for this connector partition (worker has now stopped writing)
        await asyncio.to_thread(self.milvus.delete_by_connector, self.ns, connector_uri)
        # 2. best-effort artifact bytes per object
        objs = await self.meta.fetchall(
            "SELECT object_uri FROM objects WHERE connector_id=?", (cid,)
        )
        for o in objs:
            await self._drop_artifacts(self.ns, connector_uri + o["object_uri"])
        # 3. metadata rows
        for tbl, col in (
            ("object_tasks", "connector_id"),
            ("connector_jobs", "connector_id"),
            ("objects", "connector_id"),
            ("connector_state", "connector_id"),
            ("file_state", "connector_id"),
        ):
            await self.meta.execute(f"DELETE FROM {tbl} WHERE {col}=?", (cid,))
        await self.meta.execute("DELETE FROM connectors WHERE id=?", (cid,))
        return True

    # --- read commands — any connector ---
    async def _match_connector(self, path: str) -> tuple[dict, str] | None:
        """Find the registered connector whose root is the longest prefix of `path`;
        return (connector_row, relpath) or None. Shared by _open_path (read commands)
        and resolve_connector_uri (search/grep scope). Handles local file paths (file
        connector) and scheme URIs (postgres://, github://, ...)."""
        rows = await self.meta.fetchall("SELECT * FROM connectors WHERE namespace_id=?", (self.ns,))
        # Any URI (postgres://, web://, file://<client_id><abs>, file://local<abs>) ->
        # longest registered root_uri prefix. Covers upload connectors registered under
        # their stable file://<client_id> identity.
        if "://" in path:
            best, best_root = None, ""
            for r in rows:
                ru = r["root_uri"]
                if "://" not in ru:
                    continue
                if path == ru or path.startswith(ru.rstrip("/") + "/"):
                    if len(ru) > len(best_root):
                        best, best_root = r, ru
            if best is None:
                return None
            rel = path[len(best_root) :] or "/"
            if not rel.startswith("/"):
                rel = "/" + rel
            return best, rel
        # bare local filesystem path -> file://local connector whose root is the longest prefix
        abs_path = os.path.abspath(path)
        best, best_root = None, ""
        for r in rows:
            if r["type"] != "file":
                continue
            root_abs = r["root_uri"].replace("file://local", "", 1)
            if abs_path == root_abs or abs_path.startswith(root_abs.rstrip("/") + "/"):
                if len(root_abs) > len(best_root):
                    best, best_root = r, root_abs
        if best is None:
            return None
        rel = "/" if abs_path == best_root else "/" + os.path.relpath(abs_path, best_root)
        return best, rel

    async def _open_path(self, path: str):
        """(connector_id, connector_uri, relpath, plugin) for the registered connector
        whose root is the longest prefix of `path`."""
        import json

        match = await self._match_connector(path)
        if match is None:
            raise ValueError(f"path not under any registered connector: {path}")
        row, rel = match
        plugin, _ = self._build_plugin(row["type"], json.loads(row["config_json"]), row["id"])
        await plugin.connect()
        return row["id"], row["root_uri"], rel, plugin

    async def ls(self, path: str) -> dict:
        """List children, each enriched with its full path + index state from the
        objects table, plus the connector's capabilities."""
        cid, curi, rel, plugin = await self._open_path(path)
        try:
            entries = await plugin.list(rel)
            caps = plugin.CAPABILITIES.to_dict()
        finally:
            await plugin.close()
        out = []
        base = rel.rstrip("/")
        for e in entries:
            child_rel = f"{base}/{e.name}" if base else "/" + e.name
            row = await self.meta.fetchone(
                "SELECT search_status, indexable FROM objects WHERE connector_id=? AND object_uri=?",
                (cid, child_rel),
            )
            out.append(
                {
                    "name": e.name,
                    "type": e.type,
                    "media_type": e.media_type,
                    "size_hint": e.size_hint,
                    "path": curi + child_rel,
                    "search_status": row["search_status"] if row else None,
                    "indexable": (
                        bool(row["indexable"]) if row and row["indexable"] is not None else None
                    ),
                }
            )
        return {"entries": out, "capabilities": caps}

    @staticmethod
    def _locator_matches(rec: dict, ocfg, idx: int, locator: dict) -> bool:
        if "_row" in locator:
            return idx == int(locator["_row"])
        # "lines" is the framework-reserved key for body/code chunks and is never a
        # structured-record PK — never compare it against the row. The cat router
        # dispatches body-chunk reads through plugin.read(range=...) before reaching
        # this helper, so seeing it here is a misconfiguration we just ignore.
        keys = [k for k in (ocfg.locator_fields or list(locator.keys())) if k != "lines"]
        present = [k for k in keys if k in locator]
        # Require at least one recognized locator key: a locator that's empty or whose keys
        # don't correspond to this object's locator_fields matches nothing. Without this guard
        # `all([])` is True, so a bogus/typo'd locator silently returns record #0 instead of
        # the documented locator_not_found.
        if not present:
            return False
        # resolve with the SAME JSONPath-lite used to WRITE the locator (engine indexing:
        # {f: resolve_path(rec, f)}); plain rec.get() couldn't reopen a nested locator key.
        return all(str(resolve_path(rec, k)) == str(locator.get(k)) for k in present)

    async def cat(
        self,
        path: str,
        range: tuple[int, int] | None = None,
        meta: bool = False,
        density: str | None = None,
        locator: dict | None = None,
    ):
        import json as _json
        from contextlib import aclosing

        from ..connectors.base import Range

        _, curi, rel, plugin = await self._open_path(path)
        try:
            st = await plugin.stat(rel)
            if st.type == "dir":
                raise IsADirectoryError(path)
            if meta:
                return {
                    "source": curi + rel,
                    "media_type": st.media_type,
                    "size_hint": st.size_hint,
                    "fingerprint": st.fingerprint,
                }
            okind = plugin.object_kind_of(rel)
            structured = okind in ("table_rows", "record_collection", "message_stream")
            # Binary objects have no line-based view — reading them with --range
            # would return mojibake (UTF-8 errors="replace") under the guise of a
            # text slice. Refuse cleanly so the caller falls back to `export`.
            if range is not None and okind == "binary":
                raise ValueError("range_unsupported")

            # --- locator with reserved "lines" key: route to the range path ---
            # Body / code / document chunks store identity as {"lines":[s,e]};
            # reopening one means slicing the file by line range, not iterating
            # structured records. locator.lines is 1-based half-open (matches
            # how cat --range is exposed); plugin.read takes 0-based half-open.
            if (
                locator is not None
                and isinstance(locator, dict)
                and "lines" in locator
                and len(locator) == 1
                and not structured
            ):
                s, e = int(locator["lines"][0]), int(locator["lines"][1])
                rg = Range(max(0, s - 1), max(0, e - 1))
                buf = bytearray()
                async for ch in plugin.read(rel, rg):
                    buf += ch
                return bytes(buf).decode("utf-8", errors="replace")

            # --- locator: reopen a single structured record ---
            if locator is not None:
                records = plugin.read_records(rel)
                if records is None:
                    raise ValueError("range_unsupported")  # not a structured object
                ocfg = plugin.ctx.object_config_for(rel)
                i = 0
                # aclosing: a match returns mid-iteration, so the record generator must be
                # closed deterministically — else a connector holding a cursor/transaction
                # (e.g. asyncpg) leaks the connection and pool.close() later blocks ~60s.
                async with aclosing(records):
                    async for rec in records:
                        if self._locator_matches(rec, ocfg, i, locator):
                            return {
                                "source": curi + rel,
                                "locator": locator,
                                "content": _json.dumps(rec, default=str, ensure_ascii=False),
                            }
                        i += 1
                raise ValueError("locator_not_found")

            # --- structured object: range pushdown over records (lazy, not materialized) ---
            if structured:
                if range is None:
                    # Bare cat of a structured object: stream the records as JSONL
                    # into a buffer up to _BARE_CAT_MAX_BYTES, then return. Small
                    # objects (Slack users.jsonl, small GitHub issue feeds, dozens-
                    # of-row tables) fit comfortably and round-trip as JSONL. Large
                    # ones (a postgres table with 1M rows) blow the budget mid-
                    # stream and raise the same object_too_large_for_cat so the
                    # caller still falls back to head / cat --range / export.
                    records = plugin.read_records(rel)
                    if records is None:
                        raise ValueError("object_too_large_for_cat")
                    budget = _BARE_CAT_MAX_BYTES
                    out: list[str] = []
                    size = 0
                    async with aclosing(records):
                        async for rec in records:
                            line = _json.dumps(rec, default=str, ensure_ascii=False)
                            size += len(line.encode("utf-8")) + 1  # +1 newline
                            if size > budget:
                                raise ValueError("object_too_large_for_cat")
                            out.append(line)
                    return "\n".join(out)
                start, end = range[0], range[1]
                # Pass Range(0, end) — a LIMIT-only hint — and slice [start, end) HERE, in one
                # place. Connectors disagree on whether they honor the Range: the DB ones
                # (mysql/postgres/mongo/bigquery) push OFFSET start + LIMIT down, while the SaaS
                # ones (jira/slack/notion/…) ignore it and return from row 0 — yet ALL declare
                # paged_cat=True, so the engine can't tell them apart. Pushing OFFSET start AND
                # then re-slicing `i >= start` double-applied the offset on the DB connectors, so
                # `cat --range 100:200` returned an empty/wrong page. With offset=0 every
                # connector returns rows from 0 and the single `i >= start` slice is correct for
                # both. (Trade-off: the DB connectors lose OFFSET pushdown and read `end` rows for
                # a deep page — still LIMIT-bounded; restoring true offset-pushdown needs an
                # explicit "range honored" capability — see human_todo [dborder/D65].)
                records = plugin.read_records(rel, Range(0, end))
                if records is not None:
                    out, i = [], 0
                    async with aclosing(records):  # break-early must close the generator
                        async for rec in records:
                            if i >= end:
                                break
                            if i >= start:
                                out.append(_json.dumps(rec, default=str, ensure_ascii=False))
                            i += 1
                    return "\n".join(out)

            ext = os.path.splitext(rel)[1].lower()
            text: str | None = None
            # converted markdown artifact: pdf/docx/html (CONVERT_EXTS) AND web/github pages,
            # whose .md is generated at ingest — read it from the artifact store so cat works
            # across restarts / fresh plugin instances, not just in-memory.
            if ext in CONVERT_EXTS or curi.startswith(("web://", "github://")):
                art = await self._read_artifact(self.ns, curi + rel, "converted_md")
                if art is not None:
                    text = art.decode("utf-8", errors="replace")
            if text is None:
                art_vlm = await self._read_artifact(self.ns, curi + rel, "vlm_text")
                if art_vlm is not None:  # image -> VLM description
                    return art_vlm.decode("utf-8", errors="replace")
            if text is None:
                if range is None and st.size_hint and st.size_hint > _BARE_CAT_MAX_BYTES:
                    raise ValueError("object_too_large_for_cat")
                rg = Range(range[0], range[1]) if range else None
                buf = bytearray()
                async for ch in plugin.read(rel, rg):
                    buf += ch
                text = bytes(buf).decode("utf-8", errors="replace")
            if density and density != "deep":
                okind = plugin.object_kind_of(rel)
                if okind not in ("document", "code"):
                    raise ValueError("density_unsupported")
                return _density_view(text, ext, density)
            return text
        finally:
            await plugin.close()

    async def _read_full(self, path: str) -> tuple[str, bool]:
        """Whole object content for export / tail: returns (text, partial).
        partial=True when the connector capped the read (structured objects
        above max_read_rows). The bare-cat size guard is not applied. Backs
        export and tail; tail discards the partial flag (just wants the last
        N lines), export surfaces it."""
        import json as _json

        _, curi, rel, plugin = await self._open_path(path)
        try:
            st = await plugin.stat(rel)
            if st.type == "dir":
                raise IsADirectoryError(path)
            okind = plugin.object_kind_of(rel)
            if okind in ("table_rows", "record_collection", "message_stream"):
                records = plugin.read_records(rel)
                if records is not None:
                    out = []
                    async for rec in records:
                        out.append(_json.dumps(rec, default=str, ensure_ascii=False))
                    text = "\n".join(out)
                    # ctx.declare_partial is the channel structured connectors use
                    # to flag "we capped at max_read_rows". Read it back here so
                    # export tells the truth instead of silently returning a slice.
                    partial = bool(getattr(plugin.ctx, "was_partial", lambda _r: False)(rel))
                    self._warn_if_huge_export(curi + rel, text)
                    return text, partial
            ext = os.path.splitext(rel)[1].lower()
            if ext in CONVERT_EXTS or curi.startswith(("web://", "github://")):
                art = await self._read_artifact(self.ns, curi + rel, "converted_md")
                if art is not None:
                    text = art.decode("utf-8", errors="replace")
                    self._warn_if_huge_export(curi + rel, text)
                    return text, False
            art_vlm = await self._read_artifact(self.ns, curi + rel, "vlm_text")
            if art_vlm is not None:
                text = art_vlm.decode("utf-8", errors="replace")
                self._warn_if_huge_export(curi + rel, text)
                return text, False
            buf = bytearray()
            async for ch in plugin.read(rel):
                buf += ch
            text = bytes(buf).decode("utf-8", errors="replace")
            self._warn_if_huge_export(curi + rel, text)
            return text, False
        finally:
            await plugin.close()

    def _warn_if_huge_export(self, uri: str, text: str) -> None:
        """Single-host export materializes the whole object in memory; warn on anything over
        100 MB so the operator sees the cost before the next OOM rather than after. A streaming
        export path is the proper fix but is deferred — objects this large are rare on the
        single-host deployment this guard covers, and the warning makes the cost explicit."""
        size = len(text.encode("utf-8", errors="ignore")) if text else 0
        if size > 100 * 1024 * 1024:
            print(
                f"mfs-server: WARNING export {uri} materialized "
                f"{size // (1024 * 1024)} MB in memory "
                f"(streaming export not yet implemented)",
                flush=True,
            )

    async def export(self, path: str) -> tuple[str, bool]:
        """Full content for `mfs export`: returns (text, partial). Honest
        boundary — structured connectors with more rows than max_read_rows
        return partial=True; the caller (API layer) surfaces it in the
        CatResponse. The bare-cat size guard does not apply, but each
        connector's own row cap does (true streaming export is deferred)."""
        return await self._read_full(path)

    async def head(self, path: str, n: int = 20) -> str:
        cid, curi, rel, plugin = await self._open_path(path)
        try:
            okind = plugin.object_kind_of(rel)
            structured = okind in ("table_rows", "record_collection", "message_stream")
            if structured:
                # fast path: pre-cached first rows. The cache is capped at _HEAD_CACHE_N, so
                # it's authoritative ONLY when it holds the whole object (< the cap) OR n fits
                # within it; for a larger n on a capped cache, fall through to the live bounded
                # query below — otherwise `head -n 200` would silently return just the 100
                # cached rows instead of 200 (head must give min(n, total), not min(n, cache)).
                art = await self._read_artifact(self.ns, curi + rel, "head_cache")
                if art is not None:
                    cached = art.decode("utf-8", errors="replace").splitlines()
                    if len(cached) < _HEAD_CACHE_N or n <= len(cached):
                        return "\n".join(cached[:n])
            else:
                ext = os.path.splitext(rel)[1].lower()
                # plain text / code / logs: stream just the first n lines so a large file
                # never materializes and never trips bare-cat's size guard — head is exactly
                # the escape hatch for big objects. Artifact-backed
                # objects (pdf/docx/html, web/github pages, images) have bounded cached text,
                # so they fall through to cat below.
                if not (
                    okind == "image"
                    or ext in CONVERT_EXTS
                    or curi.startswith(("web://", "github://"))
                ):
                    lines: list[str] = []
                    buf = b""
                    async for chunk in plugin.read(rel):
                        buf += chunk
                        while len(lines) < n:
                            nl = buf.find(b"\n")
                            if nl < 0:
                                break
                            lines.append(buf[:nl].decode("utf-8", errors="replace"))
                            buf = buf[nl + 1 :]
                        if len(lines) >= n:
                            break
                    if len(lines) < n and buf:
                        lines.append(buf.decode("utf-8", errors="replace"))
                    return "\n".join(lines[:n])
        finally:
            await plugin.close()
        if structured:
            return await self.cat(path, range=(0, n))  # bounded page, not the whole table
        text = await self.cat(path)  # artifact-backed text, bounded
        return "\n".join(text.splitlines()[:n])

    async def tail(self, path: str, n: int = 20) -> str:
        if n <= 0:
            return ""
        # plain-text real local file: read the last n lines straight off disk (native
        # accelerator / bounded reverse-read), so a huge log isn't fully materialized.
        # Artifact-backed (pdf/docx/html, web/github) and structured objects fall back.
        from ..common import accel

        cid, curi, rel, plugin = await self._open_path(path)
        try:
            okind = plugin.object_kind_of(rel)
            ext = os.path.splitext(rel)[1].lower()
            plain_local = (
                curi.startswith("file://local")
                and okind
                not in (
                    "image",
                    "table_rows",
                    "record_collection",
                    "message_stream",
                    "table_schema",
                )
                and ext not in CONVERT_EXTS
            )
            abs_file = (curi.replace("file://local", "", 1) + rel) if plain_local else None
        finally:
            await plugin.close()
        if abs_file and os.path.isfile(abs_file):
            return "\n".join(await asyncio.to_thread(accel.tail_lines, abs_file, n))
        text, _partial = await self._read_full(
            path
        )  # artifact-backed / structured / non-local; tail ignores the partial flag
        return "\n".join(text.splitlines()[-n:])

    async def grep(
        self, pattern: str, path: str, top_k: int = 100, regex: bool = False
    ) -> list[dict]:
        """Dispatch: pushdown (file: none) -> BM25 (indexed scope) -> linear scan
        (not_indexed objects in scope). The linear scan uses the native
        accelerator (mfs_server_rs) when the object is a real local file, else falls
        back to reading bytes + pure-Python regex."""
        from ..common import accel
        from ..connectors.base import GrepOptions

        cid, curi, rel, plugin = await self._open_path(path)
        scope_prefix = (curi + rel) if rel != "/" else None
        try:
            results: list[dict] = []
            # 2a connector grep pushdown: exact, source-side (e.g.
            # SQL ILIKE for structured connectors). Returns None when unsupported.
            ocfg = plugin.ctx.object_config_for(rel)
            try:
                gen = await plugin.grep(
                    pattern,
                    rel,
                    GrepOptions(
                        pattern=pattern,
                        text_fields=ocfg.text_fields,
                        metadata_fields=ocfg.metadata_fields,
                    ),
                )
            except Exception:  # noqa: BLE001 - pushdown failure shouldn't kill grep
                gen = None
            if gen is not None:
                async for gm in gen:
                    # Structured pushdown carries gm.locator (PK dict); text/code
                    # pushdown carries gm.line_no. locator.lines is 1-based
                    # half-open [s,e), so a single line n is [n, n+1] — not
                    # [n, n], which would round-trip as an empty slice.
                    loc = (
                        gm.locator
                        if gm.locator is not None
                        else ({"lines": [gm.line_no, gm.line_no + 1]} if gm.line_no else None)
                    )
                    results.append(
                        {
                            "source": curi + gm.path,
                            "locator": loc,
                            "content": gm.content,
                            "via": "pushdown",
                        }
                    )
                return results
            # 2b BM25 over indexed objects in scope
            expr = build_filter(self.ns, curi, scope_prefix)
            hits = await asyncio.to_thread(self.milvus.sparse_search, self.ns, pattern, top_k, expr)
            for h in hits:
                e = h.get("entity", h)
                results.append(
                    {
                        "source": e.get("object_uri"),
                        "locator": e.get("locator"),
                        "content": e.get("content"),
                        "via": "bm25",
                    }
                )
            # 2c linear scan over not_indexed objects in scope (file connector)
            root_abs = (
                curi.replace("file://local", "", 1) if curi.startswith("file://local") else None
            )
            # Path-component boundary, same fix as build_filter: scope `/src` must match the
            # object itself OR `/src/...`, NOT a sibling `/src-old`. Escape SQL LIKE wildcards
            # ('_'/'%') in the literal prefix so a path with '_' doesn't over-match either.
            if rel == "/":
                not_idx = await self.meta.fetchall(
                    "SELECT object_uri FROM objects WHERE connector_id=? AND search_status='not_indexed'",
                    (cid,),
                )
            else:
                base = rel.rstrip("/")
                esc = base.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                not_idx = await self.meta.fetchall(
                    "SELECT object_uri FROM objects WHERE connector_id=? AND search_status='not_indexed' "
                    "AND (object_uri = ? OR object_uri LIKE ? ESCAPE '\\')",
                    (cid, base, esc + "/%"),
                )
            if len(not_idx) > _GREP_LINEAR_SCAN_MAX:
                # don't silently scan a subset and imply it was exhaustive — tell the agent
                # so it can narrow the path or index first.
                results.append(
                    {
                        "source": None,
                        "locator": None,
                        "via": "notice",
                        "content": f"(grep linear scan capped at {_GREP_LINEAR_SCAN_MAX} of "
                        f"{len(not_idx)} not-indexed files in scope; narrow the path "
                        f"or run `mfs add` to index them for complete results)",
                    }
                )
            for o in not_idx[:_GREP_LINEAR_SCAN_MAX]:
                relp = o["object_uri"]
                try:
                    abs_file = (root_abs + relp) if root_abs else None
                    if abs_file and os.path.isfile(abs_file):
                        # native (or pure-Python) streaming grep straight off disk
                        for ln, line in await asyncio.to_thread(
                            accel.linear_grep_file, abs_file, pattern, False, regex, 200
                        ):
                            results.append(
                                {
                                    "source": curi + relp,
                                    "locator": {"lines": [ln, ln + 1]},
                                    "content": line,
                                    "via": "linear",
                                }
                            )
                    else:
                        rx = re.compile(pattern if regex else re.escape(pattern))
                        buf = bytearray()
                        async for ch in plugin.read(relp):
                            buf += ch
                        text = bytes(buf).decode("utf-8", errors="replace")
                        for i, line in enumerate(text.splitlines(), 1):
                            if rx.search(line):
                                results.append(
                                    {
                                        "source": curi + relp,
                                        "locator": {"lines": [i, i + 1]},
                                        "content": line,
                                        "via": "linear",
                                    }
                                )
                except Exception:  # noqa: BLE001
                    pass
            return results
        finally:
            await plugin.close()
