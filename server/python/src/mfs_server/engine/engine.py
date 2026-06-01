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
from ..processors.text import chunk_body
from ..storage.file_state import FileStateStore
from ..storage.ids import chunk_id
from ..storage.metadata import make_metadata_store
from ..storage.milvus import MilvusStore
from ..storage.object_store import make_object_store
from ..storage.transformation_cache import make_transformation_cache
from .state import ConnectorStateStore

_SCHEME_RE = re.compile(r"^([a-z][a-z0-9+.\-]*)://")
_HEAD_CACHE_N = 100  # rows pre-cached per structured object to speed `head`
_BARE_CAT_MAX_BYTES = 5 * 1024 * 1024  # bare `cat` (no range) rejects objects larger than this
_GREP_LINEAR_SCAN_MAX = 200  # cap on not-indexed files a single grep scans linearly
_JOB_STALE_AFTER_S = 120  # no heartbeat for this long => worker presumed dead
_HEARTBEAT_INTERVAL_S = 10  # worker refreshes its job heartbeat this often (<< stale)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm_rel(p: str) -> str:
    """Connector-relative path with a single leading '/' (file_state / object_uri convention)."""
    return "/" + p.lstrip("/")


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


class _SafeDict(dict):
    """format_map() helper: render unknown {field} placeholders as empty, not KeyError."""

    def __missing__(self, key):  # noqa: D401
        return ""


_PATH_SEG = re.compile(r"^([^\[\]]+)(?:\[([^\]]*)\])?$")


def _resolve_path(obj, path: str):
    """JSONPath-lite field resolver. Supports:
      a.b           nested dict access
      a[*].b / a[].b  every element's b   -> flattened list
      a[2].b        index
      a[0:5].b      slice                 -> list
    Returns a scalar for single-valued paths, a list for multi-valued ones, None/[] when
    absent. Used for text_fields / metadata_fields / locator_fields."""
    nodes = [obj]
    multi = False
    for seg in path.split("."):
        m = _PATH_SEG.match(seg)
        if not m:
            return None
        key, br = m.group(1), m.group(2)
        nxt = []
        for n in nodes:
            if not isinstance(n, dict) or key not in n:
                continue
            v = n[key]
            if br is None:
                nxt.append(v)
                continue
            if not isinstance(v, list):
                v = [v]
            if br in ("*", ""):
                nxt.extend(v)
                multi = True
            elif ":" in br:
                a, _, b = br.partition(":")
                nxt.extend(v[slice(int(a) if a else None, int(b) if b else None)])
                multi = True
            else:
                idx = int(br)
                if -len(v) <= idx < len(v):
                    nxt.append(v[idx])
        nodes = nxt
    if multi:
        return nodes
    return nodes[0] if nodes else None


def _field_values(rec: dict, field: str) -> list[str]:
    """Resolved field as a list of non-empty stringified values."""
    v = _resolve_path(rec, field)
    if v is None:
        return []
    items = v if isinstance(v, list) else [v]
    return [str(x) for x in items if x is not None and x != ""]


def _render_record(rec: dict, text_fields: list[str]) -> str:
    """Render a record into chunk content by joining configured text_fields (JSONPath-lite)
    with a default `field: value` layout. Multi-valued paths (e.g. `comments[].body`) flatten
    to bulleted lists."""
    parts = []
    for f in text_fields:
        vals = _field_values(rec, f)
        if not vals:
            continue
        if len(vals) == 1 and "[" not in f:
            parts.append(f"{f}: {vals[0]}")
        else:
            parts.append(f"{f}:\n- " + "\n- ".join(vals))
    return "\n\n".join(parts)


# Internal knobs for thread-aggregate sub-chunking. Not user-configurable: 2025 chat-RAG
# research (Weaviate / Unstructured / Slack RAG case studies) consistently shows that
# fixed ~200-word chunks at message boundaries + a small overlap matches or beats
# embedding-based semantic chunking for chat data — and the dependency cost of a real
# SemanticChunker (loading a second embedding model) is real, so we stay simple.
_THREAD_MAX_CHARS = 1500  # ~200-400 tokens; under the 8K embedding ceiling and well
#   under the 65535 Milvus content cap with headroom.
_THREAD_OVERLAP_MESSAGES = 2  # carry the last N rendered messages into the next sub-chunk
#   so a reply that references an earlier message keeps
#   that context in its embedding window.


def _split_thread(
    rendered: list[str], max_chars: int = _THREAD_MAX_CHARS, overlap: int = _THREAD_OVERLAP_MESSAGES
) -> list[tuple[int, int, str]]:
    """Split a thread's rendered messages into size-bounded sub-chunks that break ONLY at
    message boundaries (never mid-message). Adjacent sub-chunks share `overlap` trailing
    messages so cross-chunk references survive. Returns [(start_msg_idx, end_msg_idx, text)].
    A short thread (joined size <= max_chars) returns one item, preserving prior behaviour."""
    if not rendered:
        return []
    out: list[tuple[int, int, str]] = []
    cur: list[str] = []
    cur_len = 0
    start = 0
    for i, m in enumerate(rendered):
        # +2 accounts for the "\n\n" joiner between messages
        if cur and cur_len + len(m) + 2 > max_chars:
            out.append((start, start + len(cur) - 1, "\n\n".join(cur)))
            # carry the last `overlap` messages into the next sub-chunk for context
            carry = cur[-overlap:] if overlap else []
            cur = list(carry)
            cur_len = sum(len(x) + 2 for x in cur)
            start = i - len(carry)
        cur.append(m)
        cur_len += len(m) + 2
    if cur:
        out.append((start, start + len(cur) - 1, "\n\n".join(cur)))
    return out


class Engine:
    def __init__(self, cfg: ServerConfig):
        self.cfg = cfg
        self.ns = cfg.namespace
        self.meta = make_metadata_store(cfg)
        self.milvus = MilvusStore(cfg)
        self.object_store = make_object_store(cfg)
        self.tx_cache = make_transformation_cache(cfg)
        self.embed = CachingEmbeddingClient(cfg, self.tx_cache)
        self.converter = CachingConverterClient(cfg, self.tx_cache)
        self.vlm = CachingVlmClient(cfg, self.tx_cache)
        self.summary = CachingSummaryClient(cfg, self.tx_cache)
        self._artifact_writes = 0  # throttles LRU eviction sweeps

    async def startup(self) -> None:
        load_builtin()
        await self.meta.connect()
        await self.meta.init_schema()
        await self.tx_cache.connect()
        self.milvus.connect()
        self.milvus.ensure_collection(self.ns)

    async def shutdown(self) -> None:
        await self.meta.close()
        await self.tx_cache.close()

    # --- target resolution (Phase 2: file only) ---
    def _resolve_target(self, target: str) -> tuple[str, str, str, dict]:
        m = _SCHEME_RE.match(target)
        if m:
            sch = m.group(1)
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
            "SELECT id FROM connectors WHERE namespace_id=? AND root_uri=?",
            (self.ns, connector_uri),
        )
        if row:
            # `mfs connector update --config` re-registers an existing connector: refresh
            # its stored config so changed text_fields / scope / credential_ref take effect.
            if overwrite_config:
                await self.meta.execute(
                    "UPDATE connectors SET config_json=? WHERE id=?",
                    (json.dumps(stored), row["id"]),
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
                and self.cfg.chunk.default_chunk_max != _CHUNK_MAX_DEFAULT
            ):
                oc = _replace(oc, chunk_max=self.cfg.chunk.default_chunk_max)
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
        # --since requires a time cursor; reject early on connectors without one
        if since:
            cls = get_plugin_cls(ctype)
            if cls is not None and not getattr(cls.CAPABILITIES, "cursor_kind", None):
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
            (job_id, cid, self.cfg.worker.max_retries),
        )
        # dir_summary tasks are regenerated from scratch in phase 2 each run; drop any
        # leftovers so they don't run in a file phase against stale content.
        await self.meta.execute(
            "UPDATE object_tasks SET status='cancelled' "
            "WHERE connector_id=? AND change_kind='dir_summary' AND status IN ('pending','failed','running')",
            (cid,),
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
            finally:
                stop_hb.set()
                hb.cancel()
                try:
                    await hb
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
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

        staging, connector_uri, cid = await self._staging_connector(name, "")
        fs = FileStateStore(self.meta, self.ns, cid)

        def _safe(rel: str) -> str:
            dest = os.path.realpath(os.path.join(staging, rel.lstrip("/")))
            if dest != staging and not dest.startswith(staging + os.sep):
                raise ValueError(f"unsafe path in archive: {rel}")
            return dest

        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tf:
            members = tf.getmembers()
            for m in members:  # validate EVERY member before any side effect
                if m.issym() or m.islnk():
                    raise ValueError(f"links not allowed in upload: {m.name}")
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
        return os.path.realpath(str(self.object_store.files_root(self.ns, sub)))

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

        staging, connector_uri, cid = await self._staging_connector(client_id, root)
        fs = FileStateStore(self.meta, self.ns, cid)

        def _safe(base: str, rel: str) -> str:
            dest = os.path.realpath(os.path.join(base, rel.lstrip("/")))
            if dest != base and not dest.startswith(base + os.sep):
                raise ValueError(f"unsafe path in archive: {rel}")
            return dest

        tmp = tempfile.mkdtemp(prefix=".upload-", dir=os.path.dirname(staging))
        try:
            with tarfile.open(fileobj=io.BytesIO(bundle), mode="r:*") as tf:
                members = tf.getmembers()
                for m in members:
                    if m.issym() or m.islnk():
                        raise ValueError(f"links not allowed in upload: {m.name}")
                    if m.name != ".mfs-meta.json":
                        _safe(staging, m.name)
                        _safe(tmp, m.name)
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
        plugin, _ = self._build_plugin(ctype, stored_cfg, cid)
        await plugin.connect()
        aborted: str | None = None
        try:
            aborted = await self._run_job(job["id"], cid, connector_uri, plugin)
        finally:
            await plugin.close()
        await self._finalize_job(job["id"], aborted)
        # commit the deferred connector state only now that the job succeeded:
        # a failed/cancelled background job leaves the cursor where it was.
        if aborted is None:
            jrow = await self.meta.fetchone(
                "SELECT state_snapshot, status FROM connector_jobs WHERE id=?", (job["id"],)
            )
            if jrow and jrow["status"] == "succeeded" and jrow["state_snapshot"]:
                await ConnectorStateStore(self.meta, cid).apply(json.loads(jrow["state_snapshot"]))
        return job["id"]

    def _resolve_concurrency(self, concurrency=None) -> int:
        c = concurrency if concurrency is not None else self.cfg.worker.concurrency
        if c == "auto":
            return max(1, (os.cpu_count() or 2))
        try:
            return max(1, int(c))
        except (TypeError, ValueError):
            return 1

    async def _reclaim_stale_jobs(self, stale_after_s: int = _JOB_STALE_AFTER_S) -> None:
        """Housekeeping: a job whose worker died keeps status='running'
        with a stale heartbeat forever. Reset such jobs to 'queued' so a live worker
        re-claims them. Best-effort — tolerate the rare one-queued-per-connector clash."""
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=stale_after_s)).isoformat()
        try:
            stale = await self.meta.fetchall(
                "SELECT id, connector_id FROM connector_jobs WHERE status='running' "
                "AND heartbeat IS NOT NULL AND heartbeat < ?",
                (cutoff,),
            )
            for j in stale:
                # If the connector already has a queued job (e.g. a sync was preempted),
                # flipping this stale one to 'queued' would violate ux_jobs_one_queued and
                # — silently swallowed — leave it stuck 'running' forever, never reclaimed.
                # Instead hand its in-flight tasks to that queued job and fail the stale one.
                existing_queued = await self.meta.fetchone(
                    "SELECT id FROM connector_jobs WHERE connector_id=? AND status='queued' "
                    "AND id<>? LIMIT 1",
                    (j["connector_id"], j["id"]),
                )
                if existing_queued:
                    await self.meta.execute(
                        "UPDATE object_tasks SET status='pending', connector_job_id=? "
                        "WHERE connector_job_id=? AND status='running'",
                        (existing_queued["id"], j["id"]),
                    )
                    await self.meta.execute(
                        "UPDATE connector_jobs SET status='failed', finished_at=?, "
                        "error='reclaimed: superseded by queued job' WHERE id=? AND status='running'",
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
            # A 'preparing' job whose process died mid-enumeration would otherwise hold the
            # one-in-flight-enqueue slot forever (blocking future enqueues with
            # sync_already_running). It never started running, so just fail it; any partially
            # enqueued tasks are inherited by the next sync (pending/failed inheritance).
            stale_prep = await self.meta.fetchall(
                "SELECT id FROM connector_jobs WHERE status='preparing' "
                "AND heartbeat IS NOT NULL AND heartbeat < ?",
                (cutoff,),
            )
            for j in stale_prep:
                await self.meta.execute(
                    "UPDATE connector_jobs SET status='failed', finished_at=?, "
                    "error='reclaimed: enumeration abandoned' WHERE id=? AND status='preparing'",
                    (_now(), j["id"]),
                )
        except Exception:  # noqa: BLE001
            pass

    async def run_worker_forever(self, poll_interval: float = 1.0, concurrency=None) -> None:
        """Drain the queued-job queue with `concurrency` parallel workers. Each worker
        atomically claims a distinct job (the conditional claim is race-free), so N
        connectors' sync jobs run in parallel. Idle workers run a housekeeping pass that
        reclaims jobs orphaned by a crashed worker (stale heartbeat)."""
        n = self._resolve_concurrency(concurrency)

        async def _loop() -> None:
            while True:
                jid = await self.run_worker_once()
                if jid is None:
                    await self._reclaim_stale_jobs()
                    await asyncio.sleep(poll_interval)

        await asyncio.gather(*[_loop() for _ in range(n)])

    async def _claim_batch(self, job_id: str, limit: int) -> list[dict]:
        """Claim up to `limit` pending tasks. Each is taken with a conditional UPDATE
        guarded on status='pending'; only rows this worker actually flipped (rowcount
        == 1) are returned, so concurrent workers never double-process a task."""
        rows = await self.meta.fetchall(
            "SELECT * FROM object_tasks WHERE connector_job_id=? AND status='pending' "
            "ORDER BY priority ASC, started_at ASC LIMIT ?",
            (job_id, limit),
        )
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
        """retryable (transient: 429 rate-limit / 5xx / timeout) vs fatal (structural:
        quota exhausted / auth)."""
        m = str(e).lower()
        fatal_markers = (
            "insufficient_quota",
            "quota",
            "invalid_api_key",
            "authentication",
            "unauthorized",
            "402",
            "401",
            "permission denied",
            "invalid x-api-key",
        )
        if any(k in m for k in fatal_markers):
            return "fatal"
        nm = type(e).__name__.lower()
        if "authentication" in nm or "permissiondenied" in nm:
            return "fatal"
        return "retryable"

    async def _process_with_retry(self, plugin, connector_uri: str, task: dict) -> str | None:
        """Returns None on success, 'fatal', or 'retryable_exhausted'."""
        import asyncio as _a

        max_r = self.cfg.worker.max_retries
        for attempt in range(max_r + 1):
            try:
                await self._index_object(plugin, connector_uri, task)
                return None
            except Exception as e:  # noqa: BLE001
                kind = self._classify_error(e)
                if kind == "fatal":
                    await self.meta.execute(
                        "UPDATE object_tasks SET status='failed', finished_at=?, last_error=? WHERE id=?",
                        (_now(), f"fatal: {e}", task["id"]),
                    )
                    return "fatal"
                if attempt < max_r:
                    # exponential backoff capped at backoff_max_ms: a flat
                    # initial-only sleep ignored backoff_max_ms entirely and hammered a
                    # rate-limited provider at a fixed cadence.
                    delay_ms = min(
                        self.cfg.worker.backoff_initial_ms * (2**attempt),
                        self.cfg.worker.backoff_max_ms,
                    )
                    await _a.sleep(delay_ms / 1000)
                    continue
                await self.meta.execute(
                    "UPDATE object_tasks SET status='failed', finished_at=?, last_error=? WHERE id=?",
                    (_now(), str(e), task["id"]),
                )
                return "retryable_exhausted"
        return "retryable_exhausted"

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
        threshold = self.cfg.worker.consecutive_fatal_threshold
        consec_fail = 0  # consecutive object failures (fatal OR retries exhausted)
        stop_hb = asyncio.Event()
        hb_task = asyncio.create_task(self._heartbeat_loop(job_id, stop_hb))
        try:
            r = await self._run_job_loop(job_id, cid, connector_uri, plugin, threshold, consec_fail)
            if r is not None:
                return r  # file phase aborted (cancel / circuit breaker)
            if self.summary.enabled:
                # phase 2: recursive directory summaries over the dirs this job touched,
                # processed deepest-first so a parent folds in its children's summaries.
                return await self._run_directory_summary_phase(
                    job_id, cid, connector_uri, plugin, threshold
                )
            return None
        finally:
            stop_hb.set()
            hb_task.cancel()
            try:
                await hb_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    async def _run_job_loop(
        self, job_id: str, cid: str, connector_uri: str, plugin, threshold: int, consec_fail: int
    ) -> str | None:
        while True:
            if await self._should_stop(job_id, cid):
                return "cancelled"
            tasks = await self._claim_batch(job_id, limit=64)
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
                    # only flip a task we still own: a conditional UPDATE means a task that
                    # was cancelled out from under us (remove/cancel) is NOT revived to succeeded.
                    await self.meta.execute(
                        "UPDATE object_tasks SET status='succeeded', finished_at=? "
                        "WHERE id=? AND status='running'",
                        (_now(), t["id"]),
                    )
                    consec_fail = 0
                else:
                    # both 'fatal' AND 'retryable_exhausted' count toward the breaker: a
                    # provider that rate-limits (429) or times out on every object is
                    # classified retryable, and without counting it the job would grind the
                    # whole connector, burning (max_retries+1) calls per object.
                    consec_fail += 1
                    if consec_fail >= threshold:
                        await self.meta.execute(
                            "UPDATE object_tasks SET status='cancelled' "
                            "WHERE connector_job_id=? AND status IN ('pending','running')",
                            (job_id,),
                        )
                        return "circuit_breaker_tripped"
        return None

    # --- phase 2: recursive directory summaries ---
    @staticmethod
    def _ancestor_dirs(relpath: str) -> set[str]:
        """All ancestor directory relpaths of a file object_uri, incl. the root '/'.
        '/src/connectors/file/plugin.py' -> {'/', '/src', '/src/connectors', '/src/connectors/file'}."""
        parts = [p for p in relpath.split("/") if p]
        dirs = {"/"}
        cur = ""
        for seg in parts[:-1]:  # drop the file leaf
            cur = f"{cur}/{seg}"
            dirs.add(cur)
        return dirs

    async def _run_directory_summary_phase(
        self, job_id: str, cid: str, connector_uri: str, plugin, threshold: int
    ) -> str | None:
        """Enqueue a directory_summary task for every directory this job touched (ancestors
        of its changed objects). priority=-depth so the shared run loop processes them
        deepest-first; since tasks run sequentially, a parent always runs after all its
        descendants, so its summary can fold in the children's already-written summaries."""
        rows = await self.meta.fetchall(
            "SELECT DISTINCT object_uri FROM object_tasks "
            "WHERE connector_job_id=? AND change_kind != 'dir_summary'",
            (job_id,),
        )
        dirs: set[str] = set()
        for r in rows:
            dirs |= self._ancestor_dirs(r["object_uri"])
        if not self.cfg.summary.dir_recursive:
            dirs &= {"/"}  # non-recursive: only the connector root
        for d in dirs:
            depth = len([p for p in d.split("/") if p])
            await self.meta.execute(
                "INSERT INTO object_tasks (id, connector_job_id, connector_id, object_uri, old_uri, "
                " change_kind, status, priority, attempts) VALUES (?,?,?,?,?,?,?,?,0)",
                (uuid.uuid4().hex, job_id, cid, d, None, "dir_summary", "pending", -depth),
            )
        return await self._run_job_loop(job_id, cid, connector_uri, plugin, threshold, 0)

    async def _read_text_capped(self, plugin, relpath: str, cap: int) -> str:
        """Read at most `cap` bytes (bounded so a huge file can't blow up the summary input)."""
        buf = bytearray()
        async for chunk in plugin.read(relpath):
            buf += chunk
            if len(buf) >= cap:
                break
        return bytes(buf[:cap]).decode("utf-8", errors="replace")

    async def _dir_child_text(self, plugin, connector_uri: str, child_rel: str, etype: str) -> str:
        """Content excerpt fed into a directory summary for one direct child entry:
        md/code raw, pdf/html/docx -> cached converted_md, image -> cached VLM text (only
        when include_image_desc), capped at per_file_max_kb. Reuses phase-1 artifacts so we
        don't re-convert or re-call the VLM."""
        ns = self.ns
        cap = self.cfg.summary.per_file_max_kb * 1024
        full_uri = connector_uri + child_rel
        okind = plugin.object_kind_of(child_rel)
        ext = os.path.splitext(child_rel)[1].lower()
        if okind == "image":
            if not self.cfg.summary.include_image_desc:
                return ""
            data = await asyncio.to_thread(self.object_store.get_artifact, ns, full_uri, "vlm_text")
            return data.decode("utf-8", errors="replace")[:cap] if data else ""
        if okind == "document" and ext in CONVERT_EXTS:
            data = await asyncio.to_thread(
                self.object_store.get_artifact, ns, full_uri, "converted_md"
            )
            return data.decode("utf-8", errors="replace")[:cap] if data else ""
        if okind in ("document", "code"):
            return await self._read_text_capped(plugin, child_rel, cap)
        return ""  # binary / text_blob / structured: skip

    async def _summarize_directory(self, plugin, connector_uri: str, relpath: str) -> None:
        """Build a directory's LLM summary from its direct children — file content excerpts
        plus the already-computed summaries of its sub-directories — then upsert one
        directory_summary chunk. Empty input purges any stale summary instead."""
        ns = self.ns
        full_uri = connector_uri + relpath
        budget = self.cfg.summary.max_input_kb * 1024
        try:
            entries = await plugin.list(relpath)
        except Exception:  # noqa: BLE001 - vanished dir: purge and move on
            await asyncio.to_thread(self.milvus.delete_by_object, ns, connector_uri, full_uri)
            return
        parts: list[str] = []
        base = relpath.rstrip("/")
        for e in entries:
            child_rel = f"{base}/{e.name}"
            if e.type == "dir":
                chs = await asyncio.to_thread(
                    self.milvus.get_chunks_by_object, ns, connector_uri, connector_uri + child_rel
                )
                sub = next(
                    (c["content"] for c in chs if c.get("chunk_kind") == "directory_summary"), ""
                )
                if sub.strip():
                    parts.append(f"## subdirectory {e.name}/\n{sub}")
            else:
                txt = await self._dir_child_text(plugin, connector_uri, child_rel, e.type)
                if txt.strip():
                    parts.append(f"## file {e.name}\n{txt}")
        listing = "\n\n".join(parts)[:budget]
        if not listing.strip():
            await asyncio.to_thread(self.milvus.delete_by_object, ns, connector_uri, full_uri)
            return
        summ = await self.summary.summarize(listing, "directory_summary")
        await asyncio.to_thread(self.milvus.delete_by_object, ns, connector_uri, full_uri)
        if summ.strip():
            vec = (await self.embed.batch_embed([summ]))[0]
            row = {
                "chunk_id": chunk_id(ns, connector_uri, full_uri, "directory_summary", None),
                "namespace_id": ns,
                "connector_uri": connector_uri,
                "object_uri": full_uri,
                "locator": None,
                "content": summ[:65000],
                "dense_vec": vec,
                "chunk_kind": "directory_summary",
                "metadata": {},
                "indexed_at": int(time.time() * 1000),
            }
            await asyncio.to_thread(self.milvus.upsert, ns, [row])

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

        path = await asyncio.to_thread(self.object_store.put_artifact, ns, object_uri, kind, data)
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
        object deletion so the cache doesn't retain orphaned bytes."""
        for kind in ("converted_md", "vlm_text", "head_cache"):
            try:
                await asyncio.to_thread(self.object_store.delete_artifact, ns, object_uri, kind)
            except Exception:  # noqa: BLE001
                pass
        await self.meta.execute(
            "DELETE FROM artifact_cache WHERE namespace_id=? AND object_uri=?", (ns, object_uri)
        )

    async def _read_artifact(self, ns: str, object_uri: str, kind: str) -> bytes | None:
        """Fetch artifact bytes and bump last_accessed (LRU recency) when present."""
        data = await asyncio.to_thread(self.object_store.get_artifact, ns, object_uri, kind)
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
                    self.object_store.delete_artifact, ns, v["object_uri"], v["artifact_kind"]
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
        """Real chunk/embed/Milvus. document/code -> body chunks;
        other kinds carry no chunks in Phase 3 (image VLM / pdf converter -> Phase 6).
        per-object atomic: delete_by_object then upsert all of this object's chunks."""
        relpath = task["object_uri"]
        kind = task["change_kind"]
        cid = task["connector_id"]
        ns = self.ns
        full_uri = connector_uri + relpath

        if kind == "dir_summary":
            await self._summarize_directory(plugin, connector_uri, relpath)
            return

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
                await asyncio.to_thread(self.object_store.move_artifacts, ns, old_full, full_uri)
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
                        self.object_store.artifact_path(ns, full_uri, ar["artifact_kind"])
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
        elif okind in ("document", "code"):
            ext = os.path.splitext(relpath)[1].lower()
            if okind == "document" and ext in CONVERT_EXTS:
                raw = await self._read_bytes(plugin, relpath)
                text = await self.converter.convert(raw, ext)
                await self._put_artifact(ns, full_uri, "converted_md", text.encode())
            else:
                text = await self._read_text(plugin, relpath)
                if connector_uri.startswith(("web://", "github://")):
                    await self._put_artifact(ns, full_uri, "converted_md", text.encode())
            ocfg = plugin.ctx.object_config_for(relpath)
            pairs = chunk_body(text, okind, ext, self.cfg.chunk.chunk_size)
            chunk_max = ocfg.chunk_max
            partial = len(pairs) > chunk_max
            if partial:
                pairs = pairs[:chunk_max]
            if pairs:
                vecs = await self.embed.batch_embed([p[0] for p in pairs])
                now_ms = int(time.time() * 1000)
                rows = []
                for (ctext, lines), vec in zip(pairs, vecs):
                    # Body chunks: the per-chunk identity is the line range,
                    # stored as the reserved "lines" key inside locator.
                    loc = {"lines": lines}
                    rows.append(
                        {
                            "chunk_id": chunk_id(ns, connector_uri, full_uri, "body", loc),
                            "namespace_id": ns,
                            "connector_uri": connector_uri,
                            "object_uri": full_uri,
                            "locator": loc,
                            "content": ctext[:65000],
                            "dense_vec": vec,
                            "chunk_kind": "body",
                            "metadata": {},
                            "indexed_at": now_ms,
                        }
                    )
                # No per-file summary: a whole-file LLM summary is only meaningful at the
                # directory level (see the recursive directory-summary phase), not per file.
                await asyncio.to_thread(self.milvus.delete_by_object, ns, connector_uri, full_uri)
                await asyncio.to_thread(self.milvus.upsert, ns, rows)
                chunk_count = len(rows)
                search_status = "partial" if partial else "indexed"

        elif okind == "image":
            ext = os.path.splitext(relpath)[1].lower()
            raw = await self._read_bytes(plugin, relpath)
            desc = await self.vlm.describe(raw, ext)
            await self._put_artifact(ns, full_uri, "vlm_text", desc.encode())
            if desc.strip():
                vec = (await self.embed.batch_embed([desc]))[0]
                row = {
                    "chunk_id": chunk_id(ns, connector_uri, full_uri, "vlm_description", None),
                    "namespace_id": ns,
                    "connector_uri": connector_uri,
                    "object_uri": full_uri,
                    "locator": None,
                    "content": desc[:65000],
                    "dense_vec": vec,
                    "chunk_kind": "vlm_description",
                    "metadata": {},
                    "indexed_at": int(time.time() * 1000),
                }
                await asyncio.to_thread(self.milvus.delete_by_object, ns, connector_uri, full_uri)
                await asyncio.to_thread(self.milvus.upsert, ns, [row])
                chunk_count = 1
                search_status = "indexed"

        elif okind == "message_stream":
            ocfg = plugin.ctx.object_config_for(relpath)
            records = plugin.read_records(relpath)
            if records is not None and ocfg.text_fields:
                # message_stream is auto-aggregated by thread: messages with the same
                # group_by value (slack thread_ts / gmail threadId / generic thread_id) are
                # joined in order. A short thread becomes one chunk; a long thread is split
                # at message boundaries into size-bounded sub-chunks with a small overlap,
                # so embedding vectors stay focused and a reply keeps the prior context it
                # might reference.
                cfg_key = ocfg.group_by
                group_key = cfg_key or "thread"
                _THREAD_KEYS = ("thread_ts", "threadId", "thread_id", "thread")
                groups: dict = {}
                order: list = []
                async for rec in records:
                    if cfg_key:
                        gk = rec.get(cfg_key)
                    else:
                        gk = next((rec[k] for k in _THREAD_KEYS if rec.get(k)), None)
                    gk = gk or rec.get("ts") or rec.get("id") or str(len(order))
                    if gk not in groups:
                        groups[gk] = []
                        order.append(gk)
                    groups[gk].append(rec)
                    if len(order) >= ocfg.chunk_max:
                        break
                pairs: list[tuple[str, dict | None]] = []
                for gk in order:
                    rendered = [_render_record(m, ocfg.text_fields) for m in groups[gk]]
                    rendered = [r for r in rendered if r.strip()]
                    sub = _split_thread(rendered)
                    if len(sub) == 1:
                        # short thread: keep the existing single-chunk locator shape so
                        # existing search/cat semantics are preserved.
                        pairs.append((sub[0][2][:65000], {group_key: gk}))
                    else:
                        # long thread: tag each sub-chunk with its position WITHIN the
                        # thread (0..N-1) so callers can stitch / cite a specific window.
                        for sub_i, (s, e, text) in enumerate(sub):
                            pairs.append(
                                (
                                    text[:65000],
                                    {group_key: gk, "chunk_index": sub_i, "msg_range": [s, e]},
                                )
                            )
                if pairs:
                    vecs = await self.embed.batch_embed([p[0] for p in pairs])
                    now_ms = int(time.time() * 1000)
                    rows = [
                        {
                            "chunk_id": chunk_id(
                                ns, connector_uri, full_uri, "thread_aggregate", loc
                            ),
                            "namespace_id": ns,
                            "connector_uri": connector_uri,
                            "object_uri": full_uri,
                            "locator": loc,
                            "content": ctext[:65000],
                            "dense_vec": vec,
                            "chunk_kind": "thread_aggregate",
                            "metadata": {},
                            "indexed_at": now_ms,
                        }
                        for (ctext, loc), vec in zip(pairs, vecs)
                    ]
                    await asyncio.to_thread(
                        self.milvus.delete_by_object, ns, connector_uri, full_uri
                    )
                    await asyncio.to_thread(self.milvus.upsert, ns, rows)
                    chunk_count = len(rows)
                    search_status = "indexed"

        elif okind in ("table_rows", "record_collection"):
            ocfg = plugin.ctx.object_config_for(relpath)
            records = plugin.read_records(relpath)
            if records is not None and ocfg.text_fields:
                # Always per-row: each record renders to one chunk via text_fields. The
                # JSONPath-lite resolver supports nested arrays (`comments[].body`) so
                # record_collection (issues / mongo docs) works the same way as table_rows.
                pairs: list[tuple[str, dict | None, dict]] = []
                head_buf: list[str] = []  # first N raw records -> head_cache artifact
                partial = False
                i = 0
                async for rec in records:
                    if len(head_buf) < _HEAD_CACHE_N:
                        head_buf.append(json.dumps(rec, default=str, ensure_ascii=False))
                    loc = (
                        {f: _resolve_path(rec, f) for f in ocfg.locator_fields}
                        if ocfg.locator_fields
                        else {"_row": i}
                    )
                    meta = (
                        {f: _resolve_path(rec, f) for f in ocfg.metadata_fields}
                        if ocfg.metadata_fields
                        else {}
                    )
                    text = _render_record(rec, ocfg.text_fields)
                    if text.strip():
                        pairs.append((text, loc, meta))
                    i += 1
                    if len(pairs) >= ocfg.chunk_max:
                        partial = True
                        break
                # release the record generator (cursor/connection) before the slow embed
                # batch, and so a chunk_max break doesn't leak a held connection.
                await records.aclose()
                if head_buf:  # pre-cache first rows so `head` is fast without re-querying
                    await self._put_artifact(
                        ns, full_uri, "head_cache", ("\n".join(head_buf)).encode()
                    )
                if pairs:
                    vecs = await self.embed.batch_embed([p[0] for p in pairs])
                    now_ms = int(time.time() * 1000)
                    rows = [
                        {
                            "chunk_id": chunk_id(ns, connector_uri, full_uri, "row_text", loc),
                            "namespace_id": ns,
                            "connector_uri": connector_uri,
                            "object_uri": full_uri,
                            "locator": loc,
                            "content": ctext[:65000],
                            "dense_vec": vec,
                            "chunk_kind": "row_text",
                            "metadata": meta,
                            "indexed_at": now_ms,
                        }
                        for (ctext, loc, meta), vec in zip(pairs, vecs)
                    ]
                    await asyncio.to_thread(
                        self.milvus.delete_by_object, ns, connector_uri, full_uri
                    )
                    await asyncio.to_thread(self.milvus.upsert, ns, rows)
                    chunk_count = len(rows)
                    # partial if chunk_max truncated OR the connector capped the read
                    capped = plugin.ctx.was_partial(relpath)
                    search_status = "partial" if (partial or capped) else "indexed"

        elif okind == "table_schema" and self.summary.enabled:
            # schema_summary chunk: an LLM description of the table/collection schema
            records = plugin.read_records(relpath)
            schema_obj = None
            if records is not None:
                async for r in records:
                    schema_obj = r
                    break
            if schema_obj is not None:
                import json as _json

                summ = await self.summary.summarize(
                    _json.dumps(schema_obj, default=str), "schema_summary"
                )
                if summ.strip():
                    vec = (await self.embed.batch_embed([summ]))[0]
                    row = {
                        "chunk_id": chunk_id(ns, connector_uri, full_uri, "schema_summary", None),
                        "namespace_id": ns,
                        "connector_uri": connector_uri,
                        "object_uri": full_uri,
                        "locator": None,
                        "content": summ[:65000],
                        "dense_vec": vec,
                        "chunk_kind": "schema_summary",
                        "metadata": {},
                        "indexed_at": int(time.time() * 1000),
                    }
                    await asyncio.to_thread(
                        self.milvus.delete_by_object, ns, connector_uri, full_uri
                    )
                    await asyncio.to_thread(self.milvus.upsert, ns, [row])
                    chunk_count = 1
                    search_status = "indexed"

        # Directory summaries are NOT produced here per enumerated object. They are built
        # bottom-up in a dedicated phase after all files are indexed (see
        # _run_directory_summary_phase / _summarize_directory), so a parent folder's summary
        # can fold in its children's summaries.

        if chunk_count == 0:
            # A rebuild that produced no chunks (object became binary / indexable=false /
            # document emptied / empty VLM or summary) must
            # still purge chunks from a previous index, else search keeps returning stale
            # content. The per-kind branches only delete when they have new rows to upsert,
            # so cover the zero-chunk case here (rebuild = delete-by-object + insert).
            await asyncio.to_thread(self.milvus.delete_by_object, ns, connector_uri, full_uri)
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

        await plugin.on_object_indexed(relpath)

    # --- search ---
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
        plugin, _ = self._build_plugin(ctype, cfg_dict, "probe-" + uuid.uuid4().hex)
        try:
            await plugin.connect()
            hs = await plugin.healthcheck()
            return {"target": connector_uri, "type": ctype, "ok": hs.ok, "detail": hs.detail}
        except Exception as e:  # noqa: BLE001
            return {"target": connector_uri, "type": ctype, "ok": False, "detail": str(e)}
        finally:
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
                    texts = [t for t, _ in chunk_body(text, okind, ext, self.cfg.chunk.chunk_size)]
                elif okind in ("table_rows", "record_collection", "message_stream"):
                    ocfg = plugin.ctx.object_config_for(rel)
                    records = plugin.read_records(rel)
                    if records is not None and ocfg.text_fields:
                        n = 0
                        async for rec in records:
                            t = _render_record(rec, ocfg.text_fields)
                            if t.strip():
                                texts.append(t)
                            n += 1
                            if n >= sample_records:
                                break
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
        # resolve with the SAME JSONPath-lite used to WRITE the locator (engine indexing:
        # {f: _resolve_path(rec, f)}); plain rec.get() couldn't reopen a nested locator key.
        return all(str(_resolve_path(rec, k)) == str(locator.get(k)) for k in keys if k in locator)

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

            # --- locator with reserved "lines" key: route to the range path ---
            # Body / code / document chunks store identity as {"lines":[s,e]};
            # reopening one means slicing the file by line range, not iterating
            # structured records.
            if (
                locator is not None
                and isinstance(locator, dict)
                and "lines" in locator
                and len(locator) == 1
                and not structured
            ):
                s, e = locator["lines"][0], locator["lines"][1]
                rg = Range(int(s), int(e))
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
                    # a bare cat would stream the whole table -> reject; the agent picks
                    # head / cat --range / export
                    raise ValueError("object_too_large_for_cat")
                start, end = range[0], range[1]
                # hand the range to the connector so a pushdown-capable one can LIMIT/OFFSET
                # at the source; the engine still slices defensively for connectors that ignore it.
                records = plugin.read_records(rel, Range(start, end))
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

    async def _read_full(self, path: str) -> str:
        """Whole object content (no cap): all records for structured objects, converted
        markdown / VLM text for artifact-backed ones, else the full byte stream. Backs
        export and tail."""
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
                    return "\n".join(out)
            ext = os.path.splitext(rel)[1].lower()
            if ext in CONVERT_EXTS or curi.startswith(("web://", "github://")):
                art = await self._read_artifact(self.ns, curi + rel, "converted_md")
                if art is not None:
                    return art.decode("utf-8", errors="replace")
            art_vlm = await self._read_artifact(self.ns, curi + rel, "vlm_text")
            if art_vlm is not None:
                return art_vlm.decode("utf-8", errors="replace")
            buf = bytearray()
            async for ch in plugin.read(rel):
                buf += ch
            return bytes(buf).decode("utf-8", errors="replace")
        finally:
            await plugin.close()

    async def export(self, path: str) -> str:
        """Full content for `mfs export`: the entire object, no row cap and no
        bare-cat size guard."""
        return await self._read_full(path)

    async def head(self, path: str, n: int = 20) -> str:
        cid, curi, rel, plugin = await self._open_path(path)
        try:
            okind = plugin.object_kind_of(rel)
            structured = okind in ("table_rows", "record_collection", "message_stream")
            if structured:
                # fast path: pre-cached first rows
                art = await self._read_artifact(self.ns, curi + rel, "head_cache")
                if art is not None:
                    return "\n".join(art.decode("utf-8", errors="replace").splitlines()[:n])
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
        text = await self._read_full(path)  # artifact-backed / structured / non-local
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
                    # pushdown carries gm.line_no (we wrap as {"lines":[n,n]}).
                    loc = (
                        gm.locator
                        if gm.locator is not None
                        else ({"lines": [gm.line_no, gm.line_no]} if gm.line_no else None)
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
            like = (rel.rstrip("/") + "%") if rel != "/" else "%"
            not_idx = await self.meta.fetchall(
                "SELECT object_uri FROM objects WHERE connector_id=? AND search_status='not_indexed' "
                "AND object_uri LIKE ?",
                (cid, like),
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
                                    "locator": {"lines": [ln, ln]},
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
                                        "locator": {"lines": [i, i]},
                                        "content": line,
                                        "via": "linear",
                                    }
                                )
                except Exception:  # noqa: BLE001
                    pass
            return results
        finally:
            await plugin.close()
