"""Background embedding worker.

For MVP the worker is typically invoked synchronously from the CLI (simpler to
debug). A detached-subprocess mode is also provided via `ensure_running`/`spawn`.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import signal
import subprocess
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from filelock import FileLock, Timeout

from ..config import Config, ensure_mfs_home, load_config
from ..embedder import EmbeddingProvider, get_provider
from ..llm import VLMCapable
from ..llm import get_provider as get_llm_provider
from ..store import ChunkRecord, MilvusStore
from .queue import QueueTask, TaskQueue


_DEFAULT_SUMMARIZE_PROMPT = (
    "Summarize the following document concisely. Capture the main topic, "
    "key points, and notable details in 3-6 sentences. Do not include "
    "preamble like 'This document'.\n\n{content}"
)
_MAX_SUMMARIZE_INPUT_CHARS = 30_000  # rough cap to keep LLM cost bounded


def _status_path(home: Path) -> Path:
    return home / "status.json"


def _status_lock_path(home: Path) -> Path:
    return home / "status.json.lock"


def _pid_path(home: Path) -> Path:
    return home / "worker.pid"


def _log_path(home: Path) -> Path:
    return home / "worker.log"


_STATUS_LOCK_TIMEOUT = 10.0


def load_status(home: Path) -> dict:
    p = _status_path(home)
    if not p.exists():
        return {"processed": 0, "total": 0, "sync_times": {}, "state": "idle"}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"processed": 0, "total": 0, "sync_times": {}, "state": "idle"}


def save_status(home: Path, status: dict) -> None:
    p = _status_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(status), encoding="utf-8")
    tmp.replace(p)


def update_status(home: Path, **changes) -> dict:
    """Atomically merge *changes* into the persisted status.

    Wraps load/modify/save in a filelock so concurrent writers (CLI and
    background worker) cannot interleave their read-modify-write cycles and
    lose updates.
    """
    home.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(_status_lock_path(home)), timeout=_STATUS_LOCK_TIMEOUT)
    try:
        with lock:
            status = load_status(home)
            status.update(changes)
            save_status(home, status)
            return status
    except Timeout:
        # Don't crash the caller: return a best-effort read-only view so
        # progress reporting keeps working even if the lock is contended.
        return load_status(home)


class Worker:
    """Manages the background embedding worker process."""

    def __init__(self, config: Config):
        self._config = config
        self._home = ensure_mfs_home()

    # -------------------------------------------------------------- lifecycle

    def is_running(self) -> bool:
        pf = _pid_path(self._home)
        if not pf.exists():
            return False
        try:
            pid = int(pf.read_text().strip())
        except (OSError, ValueError):
            return False
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            pf.unlink(missing_ok=True)
            return False
        except PermissionError:
            # The pid exists but is owned by someone else (unlikely in dev use)
            return True

    def ensure_running(self) -> int | None:
        if self.is_running():
            return None
        return self.spawn()

    WORKER_MODULE = "mfs.ingest.worker"

    def spawn(self) -> int:
        log_path = _log_path(self._home)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        # Open inside a `with` so the parent's FD closes after Popen has
        # duplicated it into the child. Without this the parent leaks one
        # FD per spawn — fatal for long-running `mfs watch` sessions.
        with open(log_path, "a") as log_fh:
            proc = subprocess.Popen(
                [sys.executable, "-m", self.WORKER_MODULE, "--run"],
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                close_fds=True,
            )

        pid_path = _pid_path(self._home)
        tmp = pid_path.with_suffix(".tmp")
        tmp.write_text(str(proc.pid))
        tmp.replace(pid_path)
        return proc.pid


# ----------------------------------------------------------------- main loop


def _setup_logging(home: Path) -> logging.Logger:
    logger = logging.getLogger("mfs.worker")
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return logger
    handler = RotatingFileHandler(
        _log_path(home), maxBytes=1 << 20, backupCount=1, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    )
    logger.addHandler(handler)
    return logger


def process_batch(
    batch: list[QueueTask],
    embedder: EmbeddingProvider,
    store: MilvusStore,
    logger: logging.Logger,
    *,
    llm_factory=None,
) -> int:
    """Process a queue batch.

    The batch may mix ``embed`` tasks (which still go through a single
    ``embedder.embed`` call to amortize the API cost) with LLM/VLM tasks.
    For LLM/VLM tasks we run the generation step first, then route the
    resulting text into the same embed-and-write pipeline.

    ``llm_factory`` is an optional zero-argument callable that returns an
    ``LLMProvider``. It is invoked lazily on the first LLM/VLM task seen,
    so callers (and users) without LLM tasks pay no import cost.
    """
    if not batch:
        return 0

    embed_tasks: list[QueueTask] = []
    llm_state: dict = {"provider": None, "factory": llm_factory}

    for task in batch:
        if task.task_type == "embed":
            embed_tasks.append(task)
            continue
        prepared = _prepare_llm_task(task, llm_state, logger)
        if prepared is not None:
            embed_tasks.append(prepared)

    if not embed_tasks:
        return 0

    texts = [t.chunk_text for t in embed_tasks]
    try:
        vectors = embedder.embed(texts)
    except Exception as exc:  # pragma: no cover
        logger.error("Embedding failed for batch of %d: %s", len(embed_tasks), exc)
        return 0
    records: list[ChunkRecord] = []
    for task, vec in zip(embed_tasks, vectors):
        records.append(
            ChunkRecord(
                id=task.chunk_id,
                source=task.source,
                parent_dir=task.parent_dir,
                chunk_index=task.chunk_index,
                start_line=task.start_line,
                end_line=task.end_line,
                chunk_text=task.chunk_text,
                dense_vector=vec,
                content_type=task.content_type,
                file_hash=task.file_hash,
                is_dir=task.is_dir,
                embed_status="complete",
                metadata=task.metadata,
                account_id=task.account_id,
            )
        )
    store.insert_chunks(records)
    return len(records)


def _prepare_llm_task(
    task: QueueTask,
    llm_state: dict,
    logger: logging.Logger,
) -> QueueTask | None:
    """Run the LLM/VLM call for *task* and return a populated embed task.

    On any failure the task is logged and dropped so the batch can continue.
    """
    factory = llm_state.get("factory")
    if factory is None:
        logger.error(
            "LLM task %s for %s skipped: no LLM provider configured",
            task.task_type, task.source,
        )
        return None

    if llm_state.get("provider") is None:
        try:
            llm_state["provider"] = factory()
        except Exception as exc:
            logger.error("Failed to instantiate LLM provider: %s", exc)
            llm_state["factory"] = None  # don't keep retrying
            return None
    llm = llm_state["provider"]

    try:
        if task.task_type == "llm_summarize":
            text = _generate_summary(llm, task)
        elif task.task_type == "vlm_describe":
            text = _generate_description(llm, task)
        else:
            logger.error("Unknown task_type %r for %s", task.task_type, task.source)
            return None
    except Exception as exc:
        logger.error("LLM call failed for %s (%s): %s",
                     task.source, task.task_type, exc)
        return None

    text = (text or "").strip()
    if not text:
        logger.warning("LLM returned empty output for %s (%s)",
                       task.source, task.task_type)
        return None

    populated = QueueTask(
        chunk_id=task.chunk_id,
        source=task.source,
        parent_dir=task.parent_dir,
        chunk_text=text,
        chunk_index=task.chunk_index,
        start_line=task.start_line,
        end_line=task.end_line,
        content_type=task.content_type,
        file_hash=task.file_hash,
        is_dir=task.is_dir,
        metadata=dict(task.metadata or {}),
        account_id=task.account_id,
        task_type="embed",
    )
    return populated


def _generate_summary(llm, task: QueueTask) -> str:
    src = Path(task.source)
    try:
        content = src.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise RuntimeError(f"cannot read {src}: {exc}") from exc
    if len(content) > _MAX_SUMMARIZE_INPUT_CHARS:
        content = content[:_MAX_SUMMARIZE_INPUT_CHARS]
    prompt = _DEFAULT_SUMMARIZE_PROMPT.format(content=content)
    return llm.generate(prompt)


def _generate_description(llm, task: QueueTask) -> str:
    if not isinstance(llm, VLMCapable):
        raise RuntimeError(
            f"LLM provider {type(llm).__name__} is not vision-capable; "
            "configure a VLM-capable provider (openai/anthropic/google) "
            "or use a vision model."
        )
    return llm.describe_image(task.source)


def _make_llm_factory(config: Config):
    """Return a zero-arg callable that constructs the configured LLM provider.

    The returned closure does not import any LLM SDK until invoked, keeping
    the worker startup path free of heavy optional dependencies.
    """
    llm_cfg = config.llm

    def _factory():
        return get_llm_provider(
            llm_cfg.provider,
            model=llm_cfg.model or None,
            api_key=llm_cfg.api_key or None,
            base_url=llm_cfg.base_url or None,
        )

    return _factory


def _cleanup_pid(home: Path) -> None:
    try:
        _pid_path(home).unlink(missing_ok=True)
    except Exception:
        pass


def _install_pid_cleanup(home: Path) -> None:
    """Register atexit + SIGTERM handlers so a crashing worker doesn't leave
    a stale PID file behind (which would make ``is_running`` report True for
    a process that no longer exists)."""
    atexit.register(_cleanup_pid, home)
    try:
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    except (ValueError, OSError):
        # Not running in the main thread (e.g. inside a test harness).
        pass


def worker_main(
    synchronous: bool = False,
    progress_cb=None,
) -> None:
    """Drain the queue, embedding and inserting chunks until empty.

    ``progress_cb`` is an optional callable ``(processed_in_batch, total_so_far)``
    invoked after each batch — used by the foreground ``mfs add --sync`` path
    to drive a rich progress bar without coupling the worker to UI code.
    """
    config = load_config()
    home = ensure_mfs_home()
    logger = _setup_logging(home)
    if not synchronous:
        _install_pid_cleanup(home)

    try:
        embedder = get_provider(
            config.embedding.provider,
            model=config.embedding.model,
            api_key=config.embedding.api_key,
            dimension=config.embedding.dimension,
            batch_size=config.embedding.batch_size,
        )
    except Exception as exc:
        logger.error("Failed to create embedder: %s", exc)
        if synchronous:
            raise
        return
    store = MilvusStore(config.milvus, embedder.dimension)
    store.connect()

    queue = TaskQueue(home / "queue.json")

    batch_size = max(1, config.embedding.batch_size)
    processed_total = 0
    logger.info("Worker started (batch_size=%d)", batch_size)
    update_status(home, state="indexing")
    llm_factory = _make_llm_factory(config)

    while True:
        batch = queue.dequeue(batch_size=batch_size)
        if not batch:
            break
        n = process_batch(batch, embedder, store, logger, llm_factory=llm_factory)
        processed_total += n
        status = load_status(home)
        status["processed"] = status.get("processed", 0) + n
        status["state"] = "indexing"
        save_status(home, status)
        logger.info("Processed batch of %d (total=%d)", n, processed_total)
        if progress_cb is not None:
            try:
                progress_cb(n, processed_total)
            except Exception:
                # Progress callbacks are advisory — never let UI errors halt indexing.
                pass

    _pid_path(home).unlink(missing_ok=True)
    update_status(home, state="idle")
    logger.info("Worker finished, %d chunks processed", processed_total)


# Allow `python -m mfs.ingest.worker --run`
def _main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "--run":
        try:
            worker_main()
        except Exception:
            logging.getLogger("mfs.worker").exception("Worker crashed")
            time.sleep(0.1)
            sys.exit(1)
    else:
        print("Usage: python -m mfs.ingest.worker --run")
        sys.exit(2)


if __name__ == "__main__":
    _main()
