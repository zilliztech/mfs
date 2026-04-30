"""CLI entry point for MFS.

Commands: add, remove, search, grep, ls, tree, cat, status.
"""

from __future__ import annotations

import hashlib
import math
import sys
import time
from pathlib import Path

import click

from . import __version__
from . import constants as C
from .cli_config import config_group
from .config import Config, ensure_mfs_home, load_config
from .embedder import get_provider
from .ingest.chunker import chunk_file, extract_frontmatter, generate_chunk_id, hash_text
from .ingest.chunker import chunk_text as chunk_plain_text
from .ingest.converter import convert_to_markdown, is_convertible
from .ingest.queue import QueueTask, TaskQueue
from .ingest.scanner import FileInfo, Scanner
from .ingest.worker import (
    Worker,
    _make_llm_factory,
    _setup_logging,
    load_status,
    process_batch,
    save_status,
    update_status,
    worker_main,
)
from .store import MilvusStore
from .output.display import (
    error,
    format_cat_density,
    format_cat_result,
    format_grep_results,
    format_ls,
    format_search_results,
    format_status,
    format_tree,
    warn,
)
from .output.pipe import format_mfs_headers, is_pipe, parse_mfs_headers, stdin_has_data
from .search.density import (
    density_view_for_path,
    detect_density_type,
    resolve_density,
)
from .search.searcher import Searcher, SearchMode
from .search.summary import build_dir_summary_records


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _build_store(
    config: Config,
    dimension: int,
    *,
    retry_on_lock: bool = False,
) -> MilvusStore:
    """Open a Milvus connection.

    ``retry_on_lock`` enables a short backoff loop when Milvus Lite reports the
    DB file is held by another process (a concurrent ``mfs add --watch``).
    Used by read-only commands (``search``, ``grep``, ``status``); writers keep
    the fast fail so we don't block an interactive ``add`` on a stale lock.
    """
    store = MilvusStore(config.milvus, dimension)
    if not retry_on_lock:
        store.connect()
        return store

    last_exc: Exception | None = None
    for delay in (0.2, 0.5, 1.0):
        try:
            with _suppressed_stderr():
                store.connect()
            return store
        except Exception as exc:  # pragma: no cover - backoff path
            last_exc = exc
            if not _looks_like_lock_error(exc):
                raise
            time.sleep(delay)
    # Final attempt — surface a clear message if still locked.
    try:
        with _suppressed_stderr():
            store.connect()
        return store
    except Exception as exc:
        if _looks_like_lock_error(exc):
            error(
                "index is locked by another process (likely a running watcher); "
                "try again in a moment"
            )
            raise click.exceptions.Exit(2) from exc
        raise last_exc or exc


def _looks_like_lock_error(exc: Exception) -> bool:
    # Milvus Lite writes the precise "opened by another program" text to
    # fd 2 from C++ but surfaces a generic "Open local milvus failed" /
    # ConnectionConfigException to Python. Both phrasings mean "someone
    # else holds the DB file" for our purposes.
    msg = str(exc).lower()
    if any(k in msg for k in (
        "opened by another",
        "another program",
        "database is locked",
        "resource temporarily unavailable",
        "open local milvus failed",
    )):
        return True
    return type(exc).__name__ == "ConnectionConfigException"


def _build_embedder(config: Config):
    return get_provider(
        config.embedding.provider,
        model=config.embedding.model,
        api_key=config.embedding.api_key,
        dimension=config.embedding.dimension,
        batch_size=config.embedding.batch_size,
    )


def _queue(config: Config) -> TaskQueue:
    return TaskQueue(config.mfs_home / "queue.json")


def _now() -> float:
    return time.time()


def _parent_dir(path: Path) -> str:
    return str(path.parent)


def _tasks_for_file(
    fi: FileInfo,
    model_id: str,
    account_id: str,
    scanner: Scanner,
    *,
    include_text: bool = True,
    task_type: str = "embed",
) -> tuple[list[QueueTask], str]:
    # Convertible formats (e.g. PDF) go through a binary-aware converter that
    # emits Markdown; everything else is read as text. The chunker is always
    # fed Markdown for these so it uses the heading-based splitter.
    if is_convertible(fi.extension):
        try:
            content = convert_to_markdown(fi.path)
        except RuntimeError as exc:
            warn(f"skip {fi.path}: {exc}")
            return [], ""
        chunk_ext = ".md"
    else:
        try:
            content = fi.path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            warn(f"skip {fi.path}: {exc}")
            return [], ""
        chunk_ext = fi.extension

    file_hash = scanner.compute_file_hash(fi.path)
    chunks = chunk_file(fi.path, content, chunk_ext)
    tasks: list[QueueTask] = []
    source = str(fi.path)
    parent = _parent_dir(fi.path)
    for ch in chunks:
        content_hash = hash_text(ch.text)
        chunk_id = generate_chunk_id(source, ch.start_line, ch.end_line, content_hash, model_id)
        tasks.append(
            QueueTask(
                chunk_id=chunk_id,
                source=source,
                parent_dir=parent,
                chunk_text=ch.text if include_text else "",
                chunk_index=ch.chunk_index,
                start_line=ch.start_line,
                end_line=ch.end_line,
                content_type=ch.content_type,
                file_hash=file_hash,
                is_dir=False,
                metadata=ch.metadata or {},
                account_id=account_id,
                content_hash=content_hash,
                task_type=task_type,
            )
        )
    return tasks, file_hash


def _task_priority(task: QueueTask) -> tuple[int, int, int, int, str]:
    """Lower tuple values should be queued first."""
    path = Path(task.source)
    parts = [p.lower() for p in path.parts]
    name = path.name.lower()
    suffix = path.suffix.lower()

    score = 1000
    if name in {p.lower() for p in C.PRIORITY_FILENAMES}:
        score -= 350
    if name in {
        "pyproject.toml", "package.json", "go.mod", "cargo.toml",
        "pom.xml", "build.gradle", "requirements.txt", "dockerfile",
        "compose.yaml", "docker-compose.yml",
    }:
        score -= 260
    if any(part in {"src", "lib", "app", "apps", "packages", "services", "cmd", "internal", "server", "client"} for part in parts):
        score -= 220
    if any(part in {"docs", "doc", "guides", "guide", "manual", "reference", "specs"} for part in parts):
        score -= 190
    if any(part in {"examples", "example", "samples", "sample", "notebooks"} for part in parts):
        score -= 90
    if any(part in {"tests", "test", "__tests__", "fixtures", "fixture", "snapshots"} for part in parts):
        score += 80
    if any(part in {"dist", "build", "coverage", "vendor", "third_party", "generated"} for part in parts):
        score += 260

    if suffix in C.MARKDOWN_EXTENSIONS:
        score -= 70
    elif suffix in {".pdf", ".docx"}:
        score -= 55
    elif suffix in C.CODE_EXTENSIONS:
        score -= 45
    elif suffix in C.TEXT_EXTENSIONS:
        score -= 20

    depth = len(parts)
    chunk_index = task.chunk_index if task.chunk_index >= 0 else 10_000
    return (score, depth, chunk_index, len(task.source), task.source)


def _sort_tasks_for_queue(tasks: list[QueueTask]) -> list[QueueTask]:
    return sorted(tasks, key=_task_priority)


_IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
)


def _is_text_summarizable(path: Path) -> bool:
    ext = path.suffix.lower()
    return (
        ext in C.MARKDOWN_EXTENSIONS
        or ext in C.TEXT_EXTENSIONS
        or ext in C.CODE_EXTENSIONS
        or is_convertible(ext)
    )


def _find_image_files(roots: list[Path]) -> list[Path]:
    """Return image files found under *roots* (recursively for directories)."""
    seen: set[Path] = set()
    out: list[Path] = []
    for root in roots:
        if root.is_file():
            if root.suffix.lower() in _IMAGE_EXTENSIONS and root not in seen:
                out.append(root)
                seen.add(root)
            continue
        if not root.is_dir():
            continue
        for f in root.rglob("*"):
            if not f.is_file():
                continue
            if f.suffix.lower() not in _IMAGE_EXTENSIONS:
                continue
            # Mirror scanner ignore list for hidden/vendored dirs
            if any(part in C.IGNORED_DIRNAMES or part.startswith(".") for part in f.relative_to(root).parts[:-1]):
                continue
            if f not in seen:
                out.append(f)
                seen.add(f)
    return out


def _make_llm_task(
    file_path: Path,
    *,
    task_type: str,
    content_type: str,
    model_id: str,
    account_id: str,
    scanner: Scanner,
) -> QueueTask:
    """Queue an empty LLM/VLM task; the worker fills in chunk_text after calling the model."""
    source = str(file_path.resolve())
    try:
        file_hash = (
            scanner.compute_file_hash(file_path)
            if file_path.exists() and file_path.is_file()
            else ""
        )
    except OSError:
        file_hash = ""
    # chunk_id is stable per (source, task_type, file_hash) so re-running
    # `mfs add --summarize` on an unchanged file dedups via the queue.
    chunk_id = generate_chunk_id(source, -1, -1, f"{task_type}:{file_hash}", model_id)
    return QueueTask(
        chunk_id=chunk_id,
        source=source,
        parent_dir=_parent_dir(file_path),
        chunk_text="",
        chunk_index=-1,
        start_line=0,
        end_line=0,
        content_type=content_type,
        file_hash=file_hash,
        is_dir=False,
        metadata={"stale": False},
        account_id=account_id,
        task_type=task_type,
    )


# ---------------------------------------------------------------------------
# Click commands
# ---------------------------------------------------------------------------


@click.group(help="MFS — Semantic File Search CLI powered by Milvus.")
@click.version_option(version=__version__, package_name="mfs")
def main() -> None:
    pass


main.add_command(config_group)


# -------------------------------------------------------------------- add


@main.command(help="Index local files and directories.")
@click.argument("paths", nargs=-1, required=True, type=click.Path(exists=True))
@click.option("--exclude", multiple=True, help="Glob patterns to exclude")
@click.option("--force", is_flag=True, help="Force full hash comparison (skip mtime)")
@click.option("--watch", is_flag=True, help="Watch for file changes and reindex automatically")
@click.option("--interval", default=None, help="Watch debounce interval, e.g. 1500ms, 10s, 1m")
@click.option("--summarize", is_flag=True,
              help="Auto-generate LLM summaries for indexed text files (uses configured llm provider)")
@click.option("--describe", is_flag=True,
              help="Auto-generate VLM descriptions for image files (requires VLM-capable provider)")
@click.option("--sync", "sync_mode", is_flag=True,
              help="Embed in foreground with a progress bar (default spawns a detached worker)")
@click.option("--quiet", is_flag=True, help="Minimal output")
def add(paths: tuple[str, ...], exclude: tuple[str, ...], force: bool,
        watch: bool, interval: str | None,
        summarize: bool, describe: bool,
        sync_mode: bool, quiet: bool) -> None:
    config = load_config()
    ensure_mfs_home()

    if config.embedding.provider == "openai" and not config.embedding.api_key:
        error("OpenAI provider configured but OPENAI_API_KEY is not set")
        raise click.exceptions.Exit(2)

    try:
        embedder = _build_embedder(config)
    except Exception as exc:
        error(str(exc))
        raise click.exceptions.Exit(2) from exc

    store = _build_store(config, embedder.dimension)
    scanner = Scanner(config, extra_excludes=list(exclude))

    abs_paths = [Path(p).resolve() for p in paths]

    # --- Watch mode ---
    if watch:
        _run_watch(abs_paths, interval, exclude, force, sync_mode, quiet,
                   config, embedder, store, scanner,
                   summarize=summarize, describe=describe)
        return

    # --- One-shot indexing ---
    _run_add_once(abs_paths, force, sync_mode, quiet,
                  config, embedder, store, scanner,
                  summarize=summarize, describe=describe)


def _run_add_once(
    abs_paths: list[Path],
    force: bool,
    sync_mode: bool,
    quiet: bool,
    config: Config,
    embedder,
    store: MilvusStore,
    scanner: Scanner,
    *,
    summarize: bool = False,
    describe: bool = False,
) -> None:
    files = scanner.scan(abs_paths)
    # When --describe is passed, images are first-class inputs — don't bail
    # out just because the scanner (which classifies images as ignored) finds
    # nothing indexable. `_queue_auto_llm_tasks` will pick them up below.
    has_images = bool(_find_image_files(abs_paths)) if describe else False
    # Even with nothing to add, we may still need to process deletions: a watch
    # cycle that fires after the last file under a root gets `rm`'d must reach
    # the diff logic below so Milvus drops the stale rows. Only bail when the
    # store has no indexed sources for any of the watched roots either.
    if not files and not has_images:
        any_indexed = False
        for root in abs_paths:
            prefix = str(root) if root.is_dir() else _parent_dir(root)
            try:
                if store.get_indexed_files(prefix):
                    any_indexed = True
                    break
            except Exception:
                pass
        if not any_indexed:
            if not quiet:
                click.echo("No indexable files found.")
            return

    status = load_status(config.mfs_home)
    sync_times = status.get("sync_times", {}) or {}

    queue = _queue(config) if not sync_mode else None
    # Accumulate tasks in memory and flush once. In sync mode we stream this
    # list directly through the embed pipeline; in async mode one enqueue avoids
    # repeatedly rewriting a growing queue.json.
    sync_tasks: list[QueueTask] = []
    sync_seen_ids: set[str] = set()
    async_tasks: list[QueueTask] = []
    async_seen_ids: set[str] = set()
    model_id = embedder.model_name
    account_id = config.milvus.account_id

    total_added_tasks = 0
    total_files_touched = 0
    total_deleted = 0

    def _add_tasks(new_tasks: list[QueueTask]) -> int:
        if not new_tasks:
            return 0
        if sync_mode:
            added = 0
            for t in new_tasks:
                if t.chunk_id in sync_seen_ids:
                    continue
                sync_seen_ids.add(t.chunk_id)
                sync_tasks.append(t)
                added += 1
            return added
        added = 0
        for t in new_tasks:
            if t.chunk_id in async_seen_ids:
                continue
            async_seen_ids.add(t.chunk_id)
            async_tasks.append(t)
            added += 1
        return added

    for root in abs_paths:
        if root.is_dir():
            prefix = str(root)
            indexed_files = store.get_indexed_files(prefix)
            root_files = [
                f for f in files
                if str(f.path).startswith(prefix + "/") or str(f.path) == prefix
            ]
        else:
            # Single-file add: only touch this file's index state, leave siblings alone.
            prefix = str(root)
            all_indexed = store.get_indexed_files(_parent_dir(root))
            indexed_files = {str(root): all_indexed[str(root)]} if str(root) in all_indexed else {}
            root_files = [f for f in files if f.path == root]
        last_sync = None if force else sync_times.get(str(root))
        diff = scanner.compute_diff(root_files, indexed_files, last_sync)

        if diff.deleted:
            n = store.delete_by_sources(diff.deleted)
            total_deleted += n
            if not quiet:
                click.echo(f"Removed {n} stale chunks from {len(diff.deleted)} files")

        targets = list(diff.added) + list(diff.modified)
        if targets and not quiet:
            click.echo(f"Processing {len(targets)} files under {root}")

        for fi in targets:
            total_files_touched += 1
            was_modified = any(fi.path == m.path for m in diff.modified)
            tasks, file_hash = _tasks_for_file(
                fi,
                model_id,
                account_id,
                scanner,
                include_text=sync_mode,
                task_type="embed" if sync_mode else "embed_ref",
            )
            if was_modified:
                if not tasks:
                    continue
                # Delete only body chunks that no longer exist after re-chunking;
                # preserve unchanged chunks and mark summaries stale.
                source = str(fi.path)
                try:
                    old_ids = store.get_body_chunk_ids(source)
                    new_ids = {t.chunk_id for t in tasks}
                    store.delete_by_ids(sorted(old_ids - new_ids))
                    existing = old_ids & new_ids
                    store.update_file_hash_by_ids(sorted(existing), file_hash)
                    tasks = [t for t in tasks if t.chunk_id not in existing]
                except Exception:
                    pass
                try:
                    store.mark_summary_stale(source)
                except Exception:
                    pass
            total_added_tasks += _add_tasks(tasks)

        sync_times[str(root)] = _now()

    # Auto-queue LLM summary / VLM description tasks for the touched scope.
    if summarize or describe:
        llm_tasks = _build_auto_llm_tasks(
            abs_paths, files, store, scanner,
            model_id=model_id, account_id=account_id,
            summarize=summarize, describe=describe,
        )
        added_llm = _add_tasks(llm_tasks)
        total_added_tasks += added_llm
        if added_llm and not quiet:
            click.echo(f"Queued {added_llm} LLM/VLM task(s).")

    if not sync_mode and async_tasks:
        total_added_tasks = queue.enqueue(_sort_tasks_for_queue(async_tasks))

    status["sync_times"] = sync_times
    status["state"] = "indexing" if total_added_tasks else status.get("state", "idle")
    save_status(config.mfs_home, status)

    pending_queue = queue.size() if queue is not None else 0
    if not quiet:
        click.echo(
            f"Indexed: {len(files)} files scanned, "
            f"{total_files_touched} touched, {total_deleted} deleted, "
            f"{total_added_tasks} chunks queued."
        )

    if total_added_tasks == 0 and pending_queue == 0 and not sync_tasks:
        # Still rebuild dir summaries (cheap) so they reflect the latest file set
        _rebuild_dir_summaries(abs_paths, scanner, store, config, embedder)
        save_status(config.mfs_home, {**load_status(config.mfs_home), "state": "idle"})
        return

    if not sync_mode:
        Worker(config).ensure_running()
        if not quiet:
            click.echo(
                "Worker running in background. Run `mfs status` to check progress."
            )
        return

    # Synchronous draining (with progress feedback). We process the in-memory
    # task list directly — no queue.json round-trip.
    try:
        _drain_tasks_inline(
            sync_tasks, config=config, embedder=embedder, store=store,
            quiet=quiet,
        )
    except Exception as exc:
        error(f"worker failed: {exc}")
        raise click.exceptions.Exit(1) from exc
    _rebuild_dir_summaries(abs_paths, scanner, store, config, embedder)
    if not quiet:
        click.echo("Embedding complete.")


_PROGRESS_BAR_THRESHOLD = 50


def _drain_sync(*, quiet: bool, total_hint: int) -> None:
    """Run worker_main in-process with a rich progress bar.

    For tiny operations (fewer than ``_PROGRESS_BAR_THRESHOLD`` chunks) we
    skip the bar — the bar's setup overhead dwarfs the work, and a one-line
    message reads more cleanly.
    """
    if quiet or total_hint < _PROGRESS_BAR_THRESHOLD:
        worker_main(synchronous=True)
        return

    from rich.progress import (
        BarColumn,
        Progress,
        TaskProgressColumn,
        TextColumn,
        TimeRemainingColumn,
    )

    with Progress(
        TextColumn("[bold]Embedding[/]"),
        BarColumn(),
        TaskProgressColumn(),
        TextColumn("•"),
        TimeRemainingColumn(),
        transient=False,
    ) as progress:
        task_id = progress.add_task("embed", total=total_hint)

        def _cb(batch_n: int, total_done: int) -> None:
            progress.update(task_id, completed=total_done)

        worker_main(synchronous=True, progress_cb=_cb)
        # Ensure the bar visually completes even if total_hint underestimated.
        progress.update(task_id, completed=progress.tasks[0].total or 0)


def _build_auto_llm_tasks(
    abs_paths: list[Path],
    text_files,
    store: MilvusStore,
    scanner: Scanner,
    *,
    model_id: str,
    account_id: str,
    summarize: bool,
    describe: bool,
) -> list[QueueTask]:
    """Build LLM-summary / VLM-description tasks for files under *abs_paths*.

    Skips files that already have a fresh summary/description record so that
    re-running ``mfs add --summarize`` is idempotent. ``stale`` records (e.g.
    after the source file changed) are re-emitted.
    """
    tasks: list[QueueTask] = []

    if summarize:
        # Group existing summaries by path prefix so the cost of get_llm_summaries
        # scales with roots, not per-file.
        existing: dict[str, dict] = {}
        for root in abs_paths:
            prefix = str(root) if root.is_file() else str(root) + "/"
            try:
                existing.update(store.get_llm_summaries(prefix))
            except Exception:
                pass
        for fi in text_files:
            src = str(fi.path)
            meta = existing.get(src)
            already_fresh = (
                meta is not None
                and meta.get("content_type") == "llm_summary"
                and not meta.get("stale")
            )
            if already_fresh:
                continue
            tasks.append(
                _make_llm_task(
                    fi.path,
                    task_type="llm_summarize",
                    content_type="llm_summary",
                    model_id=model_id,
                    account_id=account_id,
                    scanner=scanner,
                )
            )

    if describe:
        images = _find_image_files(abs_paths)
        # Look up existing descriptions in one batch per root prefix.
        existing_desc: dict[str, dict] = {}
        for root in abs_paths:
            prefix = str(root) if root.is_file() else str(root) + "/"
            try:
                existing_desc.update(store.get_llm_summaries(prefix))
            except Exception:
                pass
        for img in images:
            src = str(img.resolve())
            meta = existing_desc.get(src)
            already_fresh = (
                meta is not None
                and meta.get("content_type") == "vlm_description"
                and not meta.get("stale")
            )
            if already_fresh:
                continue
            tasks.append(
                _make_llm_task(
                    img,
                    task_type="vlm_describe",
                    content_type="vlm_description",
                    model_id=model_id,
                    account_id=account_id,
                    scanner=scanner,
                )
            )

    return tasks


def _queue_auto_llm_tasks(
    abs_paths: list[Path],
    text_files,
    queue: TaskQueue,
    store: MilvusStore,
    scanner: Scanner,
    *,
    model_id: str,
    account_id: str,
    summarize: bool,
    describe: bool,
) -> int:
    """Backwards-compatible wrapper: build + enqueue LLM/VLM tasks."""
    tasks = _build_auto_llm_tasks(
        abs_paths, text_files, store, scanner,
        model_id=model_id, account_id=account_id,
        summarize=summarize, describe=describe,
    )
    if not tasks:
        return 0
    return queue.enqueue(tasks)


def _drain_tasks_inline(
    tasks: list[QueueTask],
    *,
    config: Config,
    embedder,
    store: MilvusStore,
    quiet: bool,
) -> int:
    """Embed and insert *tasks* directly, bypassing the on-disk queue.

    Used by ``mfs add --sync`` so large corpora don't pay O(N²) I/O on the
    atomic queue.json rewrite (one rewrite per chunk ≈ minutes per chunk
    at 10K+ chunks). Tasks are processed in ``batch_size`` groups via the
    same ``process_batch`` path the async worker uses, so LLM / VLM task
    types keep working.
    """
    if not tasks:
        return 0

    home = ensure_mfs_home()
    logger = _setup_logging(home)
    llm_factory = _make_llm_factory(config)
    batch_size = max(1, config.embedding.batch_size)
    total = len(tasks)

    update_status(home, state="indexing")
    processed_total = 0

    def _run_batches(on_progress) -> None:
        nonlocal processed_total
        for i in range(0, total, batch_size):
            batch = tasks[i : i + batch_size]
            n = process_batch(batch, embedder, store, logger, llm_factory=llm_factory)
            processed_total += n
            st = load_status(home)
            st["processed"] = st.get("processed", 0) + n
            st["state"] = "indexing"
            save_status(home, st)
            logger.info("Processed batch of %d (total=%d)", n, processed_total)
            if on_progress is not None:
                on_progress(min(i + batch_size, total))

    try:
        if quiet or total < _PROGRESS_BAR_THRESHOLD:
            _run_batches(None)
        else:
            from rich.progress import (
                BarColumn,
                Progress,
                TaskProgressColumn,
                TextColumn,
                TimeRemainingColumn,
            )
            with Progress(
                TextColumn("[bold]Embedding[/]"),
                BarColumn(),
                TaskProgressColumn(),
                TextColumn("•"),
                TimeRemainingColumn(),
                transient=False,
            ) as progress:
                task_id = progress.add_task("embed", total=total)

                def _cb(completed: int) -> None:
                    progress.update(task_id, completed=completed)

                _run_batches(_cb)
                progress.update(task_id, completed=total)
    finally:
        update_status(home, state="idle")
    logger.info("Inline drain finished, %d chunks processed", processed_total)
    return processed_total


def _rebuild_dir_summaries(
    abs_paths: list[Path],
    scanner: Scanner,
    store: MilvusStore,
    config: Config,
    embedder,
) -> None:
    """Rebuild `is_dir=true` summary records for all ancestors of indexed paths."""
    dir_roots: list[Path] = []
    for p in abs_paths:
        dir_roots.append(p if p.is_dir() else p.parent)
    try:
        build_dir_summary_records(
            dir_roots, scanner, store,
            account_id=config.milvus.account_id,
            embedder_dim=embedder.dimension,
        )
    except Exception as exc:
        warn(f"directory summary rebuild failed: {exc}")


def _run_watch(
    abs_paths: list[Path],
    interval: str | None,
    exclude: tuple[str, ...],
    force: bool,
    sync_mode: bool,
    quiet: bool,
    config: Config,
    embedder,
    store: MilvusStore,
    scanner: Scanner,
    *,
    summarize: bool = False,
    describe: bool = False,
) -> None:
    try:
        from watchfiles import watch as wf_watch  # type: ignore[import-not-found]
    except ImportError:
        error("--watch requires the `watchfiles` package. Install it with `uv add watchfiles`.")
        raise click.exceptions.Exit(2)

    step_ms = _parse_interval_ms(interval) if interval else 1500

    # Initial sync
    _run_add_once(abs_paths, force, sync_mode, quiet, config, embedder, store, scanner,
                  summarize=summarize, describe=describe)
    # Milvus Lite is single-writer — holding the connection across idle waits
    # would block any concurrent `mfs search`. Drop it now and reopen at the
    # start of each change-driven re-index.
    store.close()
    if not quiet:
        click.echo(f"Watching {len(abs_paths)} path(s) (interval={step_ms}ms). Ctrl+C to stop.")

    try:
        for _changes in wf_watch(
            *[str(p) for p in abs_paths],
            debounce=step_ms,
            step=min(step_ms, 200),
        ):
            if not quiet:
                click.echo(f"[{time.strftime('%H:%M:%S')}] Change detected, re-indexing…")
            store.connect()
            try:
                _run_add_once(abs_paths, force=False, sync_mode=sync_mode,
                              quiet=quiet, config=config, embedder=embedder,
                              store=store, scanner=scanner,
                              summarize=summarize, describe=describe)
            finally:
                store.close()
    except KeyboardInterrupt:
        if not quiet:
            click.echo("\nWatcher stopped.")


def _parse_interval_ms(text: str) -> int:
    t = text.strip().lower()
    if t.endswith("ms"):
        return max(1, int(t[:-2]))
    if t.endswith("s"):
        return max(1, int(float(t[:-1]) * 1000))
    if t.endswith("m"):
        return max(1, int(float(t[:-1]) * 60_000))
    if t.endswith("h"):
        return max(1, int(float(t[:-1]) * 3_600_000))
    return int(t)


# ------------------------------------------------------------------ remove


@main.command(help="Remove a file or directory from the index.")
@click.argument("target", type=str)
@click.option("--quiet", is_flag=True)
def remove(target: str, quiet: bool) -> None:
    config = load_config()
    ensure_mfs_home()
    try:
        embedder = _build_embedder(config)
    except Exception as exc:
        error(str(exc))
        raise click.exceptions.Exit(2) from exc
    store = _build_store(config, embedder.dimension)

    resolved = Path(target).resolve()
    as_str = str(resolved)
    # Heuristic: if it looks like a directory (existed as one or has no extension),
    # delete by prefix; otherwise delete by exact source.
    is_dir_target = (resolved.exists() and resolved.is_dir()) or as_str.endswith("/")
    if is_dir_target:
        prefix = as_str if as_str.endswith("/") else as_str + "/"
        deleted_children = store.delete_by_prefix(prefix)
        deleted_self = store.delete_dir_record(as_str)
        if not quiet:
            click.echo(f"Removed {deleted_children} child chunks and "
                       f"{deleted_self} dir record(s) under {as_str}")
    else:
        deleted = store.delete_by_source(as_str)
        if not quiet:
            click.echo(f"Removed {deleted} chunks for {as_str}")


# ------------------------------------------------------------------ status


@main.command(help="Show indexing status and progress.")
@click.option("--json", "output_json", is_flag=True, help="JSON output")
@click.option("--needs-summary", is_flag=True, help="List indexed files without an LLM summary")
def status(output_json: bool, needs_summary: bool) -> None:
    config = load_config()
    ensure_mfs_home()
    # Read file-level state first so the worker's queue/progress info is
    # always visible even when the Milvus connection fails (Milvus Lite is a
    # single-writer store; `mfs status` run during `mfs add` would otherwise
    # error out with a scary ConnectionConfigException).
    milvus_busy = False
    counts: dict = {}
    store = None
    with _suppressed_stderr():
        try:
            embedder = _build_embedder(config)
            store = _build_store(config, embedder.dimension, retry_on_lock=True)
            counts = store.count_all()
        except Exception:
            milvus_busy = True
            store = None

    if needs_summary and store is not None:
        cwd = str(Path.cwd().resolve())
        indexed = store.get_indexed_files(cwd)
        summaries = store.get_llm_summaries(cwd)
        missing = sorted(src for src in indexed if src not in summaries)
        stale = sorted(
            src for src, m in summaries.items()
            if isinstance(m, dict) and m.get("stale")
        )
        if output_json:
            import json as _json
            click.echo(_json.dumps({"missing": missing, "stale": stale},
                                   ensure_ascii=False, indent=2))
        else:
            if not missing and not stale:
                click.echo("All indexed files have a fresh LLM summary.")
            if missing:
                click.echo(f"Missing summary ({len(missing)}):")
                for s in missing:
                    click.echo(f"  {s}")
            if stale:
                click.echo(f"Stale summary ({len(stale)}):")
                for s in stale:
                    click.echo(f"  {s}")
        return

    queue_size = _queue(config).size()
    raw = load_status(config.mfs_home)
    worker_running = Worker(config).is_running()

    merged = {
        "state": "indexing" if worker_running or queue_size else raw.get("state", "idle"),
        "total_chunks": counts.get("total_chunks", 0),
        "complete_chunks": counts.get("complete_chunks", 0),
        "pending_chunks": counts.get("pending_chunks", 0),
        "files": counts.get("files", 0),
        "dir_summaries": counts.get("dir_summaries", 0),
        "queue_size": queue_size,
        "processed": raw.get("processed", 0),
        "sync_times": raw.get("sync_times", {}),
        "worker_running": worker_running,
        "milvus_busy": milvus_busy,
    }
    click.echo(format_status(merged, output_json=output_json))


# ------------------------------------------------------------------ search


@main.command(help="Semantic search across indexed files.")
@click.argument("query", type=str)
@click.argument("path", default=None, required=False, type=click.Path())
@click.option("--top-k", default=10, help="Number of results")
@click.option("--path", "path_opt", default=None,
              help="Alias for the positional path argument (backward compat).")
@click.option("--all", "search_all", is_flag=True, help="Search across all indexed files")
@click.option("--mode", type=click.Choice(["hybrid", "semantic", "keyword"]), default="hybrid")
@click.option("--json", "output_json", is_flag=True, help="JSON output")
@click.option("--quiet", is_flag=True, help="Show one line per result, no snippet")
def search(query: str, path: str | None, top_k: int, path_opt: str | None,
           search_all: bool, mode: str, output_json: bool, quiet: bool) -> None:
    config = load_config()
    ensure_mfs_home()
    try:
        embedder = _build_embedder(config)
    except Exception as exc:
        error(str(exc))
        raise click.exceptions.Exit(2) from exc

    # Resolve positional / --path alias. Positional wins; --path kept for compat.
    scope_path = path or path_opt

    # Pipe detection. Four states — only one (headered pipe) maps onto the
    # indexed corpus; everything else Unix-pipeline-style short-circuits so we
    # don't "secretly search corpus" when the caller clearly intended to feed
    # stdin in.
    stdin_source: str | None = None
    has_pipe = stdin_has_data()
    if has_pipe:
        stdin_text = sys.stdin.read()
        headers, _body = parse_mfs_headers(stdin_text)
        if headers and headers.get("source"):
            # Branch B: headered `mfs cat` output — filter to that source.
            stdin_source = headers["source"]
        elif not stdin_text.strip():
            # Branch D: empty pipe (e.g. upstream command failed). Like
            # `grep` / `sed`, respect the pipe and return nothing.
            click.echo(format_search_results([], output_json=output_json, quiet=quiet))
            return
        else:
            # Branch C: arbitrary text without headers. Search the piped text
            # itself via temporary dense embeddings; do not fall back to the
            # indexed corpus.
            results = _search_plain_stdin(
                query,
                stdin_text,
                embedder=embedder,
                top_k=top_k,
            )
            click.echo(format_search_results(results, output_json=output_json, quiet=quiet))
            return

    # POSIX-style scoping: no pipe, no path, no --all → error. Matches grep
    # semantics (path is positional) and makes --all opt-in for "the whole
    # index", avoiding the silent cwd default that surprises pipeline callers
    # working from tmp dirs.
    if not has_pipe and not scope_path and not search_all:
        error(
            'no path specified. Use "<path>" or --all to search the index.'
        )
        raise click.exceptions.Exit(2)

    if stdin_source:
        path_filter = stdin_source
    elif search_all:
        path_filter = None
    elif scope_path:
        path_filter = str(Path(scope_path).resolve())
    else:
        # has_pipe with neither stdin_source nor search_all/scope_path — we
        # already returned above for the empty / plain-text branches, so this
        # only fires when the pipe had headers but no source (shouldn't happen
        # in practice). Fall through with no filter.
        path_filter = None

    store = _build_store(config, embedder.dimension, retry_on_lock=True)
    searcher = Searcher(store, embedder)
    mode_enum = SearchMode(mode)
    results = searcher.search(query, mode=mode_enum, path_filter=path_filter, top_k=top_k)
    click.echo(format_search_results(results, output_json=output_json, quiet=quiet))


def _search_plain_stdin(query: str, stdin_text: str, *, embedder, top_k: int) -> list:
    query = query.strip()
    if not query or not stdin_text.strip():
        return []
    chunks = chunk_plain_text(stdin_text)
    if not chunks:
        return []

    vectors = embedder.embed([query] + [ch.text for ch in chunks])
    if not vectors:
        return []
    query_vector = vectors[0]
    chunk_vectors = vectors[1:]
    scored = []
    for ch, vector in zip(chunks, chunk_vectors):
        scored.append((_cosine_similarity(query_vector, vector), ch))
    scored.sort(key=lambda item: item[0], reverse=True)

    from .store import SearchResult

    results = []
    if top_k <= 0:
        return []
    for score, ch in scored[:top_k]:
        results.append(
            SearchResult(
                source="<stdin>",
                chunk_text=ch.text,
                chunk_index=ch.chunk_index,
                start_line=ch.start_line,
                end_line=ch.end_line,
                content_type=ch.content_type,
                score=score,
                is_dir=False,
                metadata={"temporary": True},
            )
        )
    return results


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


# -------------------------------------------------------------------- grep


@main.command(help="Full-text search with smart routing.")
@click.argument("pattern", type=str)
@click.argument("path", default=None, required=False, type=click.Path())
@click.option("--path", "path_opt", default=None,
              help="Alias for the positional path argument (backward compat).")
@click.option("--all", "grep_all", is_flag=True,
              help="Grep across all indexed files (skip scope check).")
@click.option("-C", "context", default=0, type=int, help="Context lines before/after")
@click.option("-n", "line_numbers", is_flag=True,
              help="Deprecated: line numbers are always shown in the gutter.")
@click.option("-i", "case_insensitive", is_flag=True, help="Case insensitive")
@click.option("--json", "output_json", is_flag=True)
@click.option("--quiet", is_flag=True)
def grep(pattern: str, path: str | None, path_opt: str | None, grep_all: bool,
         context: int, line_numbers: bool,
         case_insensitive: bool, output_json: bool, quiet: bool) -> None:
    config = load_config()
    ensure_mfs_home()
    try:
        embedder = _build_embedder(config)
    except Exception as exc:
        error(str(exc))
        raise click.exceptions.Exit(2) from exc

    store = _build_store(config, embedder.dimension, retry_on_lock=True)
    scanner = Scanner(config)
    searcher = Searcher(store, embedder, scanner=scanner)

    scope_path = path or path_opt
    has_pipe = stdin_has_data()

    # POSIX-style scoping: no pipe, no path, no --all → error.
    if not has_pipe and not scope_path and not grep_all:
        error(
            'no path specified. Use "<path>" or --all to grep the index.'
        )
        raise click.exceptions.Exit(2)

    if scope_path:
        target: Path | None = Path(scope_path).resolve()
    else:
        # --all or stdin pipe: no path scoping — searcher.grep with path=None
        # hits every indexed file.
        target = None

    try:
        matches = searcher.grep(
            pattern,
            path=target,
            context_lines=context,
            case_insensitive=case_insensitive,
        )
    except ValueError as exc:
        error(str(exc))
        raise click.exceptions.Exit(2) from exc

    if line_numbers and not output_json:
        warn("-n is deprecated: line numbers are always shown in the gutter")
    click.echo(format_grep_results(matches, output_json=output_json))


# ----------------------------------------------------------------------- ls


@main.command(help="List directory contents with summaries.")
@click.argument("path", default=".", type=click.Path())
@click.option("--peek", "preset", flag_value="peek",
              help="One-glance view: filenames / skeleton only.")
@click.option("--skim", "preset", flag_value="skim",
              help="Overview with short summary per file (default).")
@click.option("--deep", "preset", flag_value="deep",
              help="Detailed view with extended summaries and deeper structure.")
@click.option("-W", "width", default=None, type=int,
              help="Width override: chars per paragraph / node (overrides preset).")
@click.option("-H", "height", default=None, type=int,
              help="Height override: max headings / items shown per file (overrides preset).")
@click.option("-D", "depth", default=None, type=int,
              help="Depth override: heading/structure levels to expand (overrides preset).")
@click.option("--json", "output_json", is_flag=True, help="Emit JSON output.")
def ls(path: str, preset: str | None, width: int | None, height: int | None,
       depth: int | None, output_json: bool) -> None:
    target = Path(path).resolve()
    if not target.exists():
        error(f"{target}: no such file or directory")
        raise click.exceptions.Exit(2)
    if not target.is_dir():
        error(f"{target}: not a directory (use `mfs cat` for files)")
        raise click.exceptions.Exit(2)

    resolved_preset = preset or "skim"
    params = resolve_density("directory", resolved_preset,
                             w_override=width, h_override=height, d_override=depth)
    entries = _ls_entries(target, resolved_preset,
                          width=width, height=height, depth=depth)
    cont_cap = _ls_continuation_cap(resolved_preset, height)
    click.echo(format_ls(target, entries, resolved_preset, params,
                         cont_cap=cont_cap, output_json=output_json))


def _ls_continuation_cap(preset: str, height: int | None) -> int:
    """Decide how many continuation lines ls should render after each filename.

    Kept as a helper so the rule is visible in one place — the display layer
    keeps deferring to whatever the caller asks for.
    """
    if preset == "peek":
        return 0
    if preset == "deep":
        return 12
    # skim (default). Honor explicit -H so `-H 1` and `-H 20` actually show
    # one vs many lines rather than collapsing both to 2.
    if height is not None:
        return max(0, min(height - 1, 15))
    return 2


def _ls_entries(
    target: Path,
    preset: str = "skim",
    *,
    width: int | None = None,
    height: int | None = None,
    depth: int | None = None,
) -> list[dict]:
    """Return sorted ls entries with summary text.

    The cached skim baseline (LLM summary or rule-based extract_file_summary)
    is reused when the caller asks for the default skim preset with no
    W/H/D overrides. When the caller asks for a different density, we
    re-read the file and render a fresh density view at the requested
    (W, H, D). Files not classified as "indexed" by the scanner (binary
    logs, archives, images) always fall through to an empty summary so the
    display stays clean.

    Each entry: {name, is_dir, path, summary, indexed, stale?}
    """
    from .search.summary import extract_file_summary, sort_by_priority

    has_override = any(v is not None for v in (width, height, depth))
    use_cached = preset in (None, "skim") and not has_override

    config = load_config()
    ensure_mfs_home()
    scanner = Scanner(config)

    # Best-effort: connect to Milvus to look up dir/file summaries and indexed state.
    store: MilvusStore | None = None
    indexed: dict[str, str] = {}
    try:
        embedder = _build_embedder(config)
        store = _build_store(config, embedder.dimension)
        indexed = store.get_indexed_files(str(target))
    except Exception:
        store = None

    children = [p for p in target.iterdir() if not p.name.startswith(".")]
    children = sort_by_priority(children)

    entries: list[dict] = []
    for child in children:
        item: dict = {
            "name": child.name,
            "is_dir": child.is_dir(),
            "path": str(child),
            "indexed": str(child.resolve()) in indexed,
            "summary": "",
        }
        if child.is_dir() and store is not None:
            ds = store.get_dir_summary(str(child.resolve()))
            item["summary"] = ds.chunk_text if ds else ""
        elif child.is_file():
            # Surface the LLM-summary stale flag regardless of render path.
            llm_meta: dict | None = None
            if store is not None:
                summaries = store.get_llm_summaries(str(child.resolve()))
                llm_meta = summaries.get(str(child.resolve()))
            if llm_meta and llm_meta.get("stale"):
                item["stale"] = True

            if use_cached:
                # Prefer generated LLM/VLM summary; fall back to the rule-based
                # skim extraction for plain-text/code/markdown bodies.
                if llm_meta and llm_meta.get("content_type") in (
                    "llm_summary", "vlm_description"
                ):
                    text = _fetch_llm_summary_text(store, str(child.resolve()))
                    if text:
                        item["summary"] = text
                if not item["summary"] and scanner.classify_file(child) == "indexed":
                    try:
                        item["summary"] = extract_file_summary(child)
                    except Exception:
                        item["summary"] = ""
            elif scanner.classify_file(child) == "indexed":
                # Re-extract at the caller's density; skip the cache since it
                # was computed at the skim baseline and wouldn't reflect the
                # requested W/H/D.
                try:
                    item["summary"] = density_view_for_path(
                        child, preset,
                        w_override=width, h_override=height, d_override=depth,
                    )
                except Exception:
                    item["summary"] = ""
        entries.append(item)
    return entries


def _fetch_llm_summary_text(store: MilvusStore | None, source: str) -> str:
    if store is None:
        return ""
    try:
        rows = store.client.query(
            collection_name=store._config.collection_name,
            filter=f'source == "{source}" and chunk_index == -1',
            output_fields=["chunk_text"],
            limit=1,
        )
    except Exception:
        return ""
    return rows[0].get("chunk_text", "") if rows else ""


# ---------------------------------------------------------------------- tree


@main.command(help="Show recursive directory tree with summaries.")
@click.argument("path", default=".", type=click.Path())
@click.option("--peek", "preset", flag_value="peek",
              help="One-glance view: names only, no summaries.")
@click.option("--skim", "preset", flag_value="skim",
              help="Overview with a one-line summary per node (default).")
@click.option("--deep", "preset", flag_value="deep",
              help="Detailed view with richer per-node summaries.")
@click.option("-W", "width", default=None, type=int,
              help="Width override: chars per paragraph / node (overrides preset).")
@click.option("-H", "height", default=None, type=int,
              help="Height override: max headings / items shown per file (overrides preset).")
@click.option("-D", "depth", default=None, type=int,
              help="Depth override: heading/structure levels to expand (overrides preset).")
@click.option("-L", "max_depth", default=3, type=int, help="Max directory recursion depth.")
@click.option("--json", "output_json", is_flag=True, help="Emit JSON output.")
def tree(path: str, preset: str | None, width: int | None, height: int | None,
         depth: int | None, max_depth: int, output_json: bool) -> None:
    target = Path(path).resolve()
    if not target.exists() or not target.is_dir():
        error(f"{target}: not a directory")
        raise click.exceptions.Exit(2)

    resolved_preset = preset or "skim"
    params = resolve_density("directory", resolved_preset,
                             w_override=width, h_override=height, d_override=depth)
    # For JSON consumers we pre-compute per-node summaries so the output is
    # self-contained; the terminal renderer keeps computing them lazily.
    tree_data = _tree_entries(
        target, max_depth,
        preset=resolved_preset if output_json else None,
        params=params if output_json else None,
    )
    click.echo(format_tree(target, tree_data, resolved_preset, params, output_json=output_json))


def _tree_entries(target: Path, max_depth: int, depth: int = 0,
                  scanner: Scanner | None = None,
                  preset: str | None = None,
                  params=None) -> dict:
    from .search.summary import extract_file_summary, sort_by_priority
    if scanner is None:
        scanner = Scanner(load_config())
    entry: dict = {"name": target.name or str(target), "path": str(target),
                   "is_dir": True, "children": [], "summary": "",
                   "summarizable": True}
    if depth >= max_depth:
        return entry
    try:
        children = [p for p in target.iterdir() if not p.name.startswith(".")]
    except OSError:
        return entry
    children = sort_by_priority(children)
    for child in children:
        if child.is_dir():
            entry["children"].append(
                _tree_entries(child, max_depth, depth + 1, scanner,
                              preset=preset, params=params)
            )
        else:
            summarizable = scanner.classify_file(child) == "indexed"
            child_entry = {
                "name": child.name,
                "path": str(child),
                "is_dir": False,
                "children": [],
                "summary": "",
                # Only attempt density preview for classifiable files;
                # binary logs / archives / images would produce garbage.
                "summarizable": summarizable,
            }
            # Populate summary eagerly when the caller (JSON renderer) asks
            # for it; the text renderer computes it on demand via display.
            if preset is not None and preset != "peek" and summarizable:
                try:
                    child_entry["summary"] = extract_file_summary(child)
                except Exception:
                    child_entry["summary"] = ""
            entry["children"].append(child_entry)
    return entry


# ----------------------------------------------------------------------- cat


@main.command(help="Read file content or show file overview.")
@click.argument("file", type=str)
@click.option("--peek", "preset", flag_value="peek",
              help="One-glance view: headings / skeleton only, no body text.")
@click.option("--skim", "preset", flag_value="skim",
              help="Overview view with short excerpts (default when a preset is implied).")
@click.option("--deep", "preset", flag_value="deep",
              help="Detailed view with extended excerpts per section.")
@click.option("-W", "width", default=None, type=int,
              help="Width override: chars per paragraph / value (overrides preset).")
@click.option("-H", "height", default=None, type=int,
              help="Height override: max headings / items shown (overrides preset).")
@click.option("-D", "depth", default=None, type=int,
              help="Depth override: heading/structure levels to expand (overrides preset).")
@click.option("-n", "line_range", default=None, help="Line range to show, e.g. 40:60.")
@click.option("--no-frontmatter", is_flag=True, help="Strip YAML frontmatter before display.")
@click.option("--no-meta", is_flag=True, help="Omit ::mfs: headers even when piped.")
@click.option("--meta", is_flag=True, help="Force ::mfs: headers even when output is a terminal.")
@click.option("--json", "output_json", is_flag=True, help="JSON output")
@click.option("--no-line-numbers", is_flag=True,
              help="Omit source line numbers from density views (peek/skim/deep).")
def cat(file: str, preset: str | None, width: int | None, height: int | None,
        depth: int | None, line_range: str | None,
        no_frontmatter: bool, no_meta: bool, meta: bool,
        output_json: bool, no_line_numbers: bool) -> None:
    path = Path(file).resolve()
    if not path.exists() or not path.is_file():
        error(f"{path}: not a file")
        raise click.exceptions.Exit(2)

    ext = path.suffix.lower()

    # PDFs (and future convertible formats) are binary on disk but produce
    # real Markdown after conversion. Route through the converter before
    # falling back to the binary-safety check.
    if is_convertible(ext):
        try:
            content = convert_to_markdown(path)
        except RuntimeError as exc:
            error(str(exc))
            raise click.exceptions.Exit(2) from exc
        ctype = detect_density_type(".md")
    else:
        if _path_looks_binary(path):
            error(f"{path}: looks binary; mfs cat only supports text files")
            raise click.exceptions.Exit(2)
        ctype = detect_density_type(ext)
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            error(str(exc))
            raise click.exceptions.Exit(2)

    body = content
    if no_frontmatter:
        _fm, body, _o = extract_frontmatter(content)
    source_total_lines = body.count("\n") + 1

    # Density view if preset or explicit W/H/D
    if preset or any(v is not None for v in (width, height, depth)):
        params = resolve_density(ctype, preset, width, height, depth)
        body = format_cat_density(
            body, ctype, params,
            show_line_numbers=not no_line_numbers,
            total_lines=source_total_lines,
        )
    elif line_range:
        body = _slice_by_lines(body, line_range)

    if output_json:
        lines = (
            _parse_line_range(line_range, source_total_lines)
            if line_range
            else (1, source_total_lines)
        )
        indexed = _is_indexed(path)
        file_hash = _short_file_hash(path) if indexed else ""
        click.echo(
            format_cat_result(
                str(path),
                body,
                content_type=ctype,
                lines=lines,
                indexed=indexed,
                file_hash=file_hash,
                preset=preset,
            )
        )
        return

    # Meta-header logic (pipe vs terminal).
    # We probe Milvus to set `indexed` truthfully and only emit `hash=` when
    # the file actually has chunks. The store is closed immediately so a
    # downstream `mfs search` in the same pipe can acquire the Lite lock.
    emit_meta = meta or (is_pipe() and not no_meta)
    output = body
    if emit_meta:
        indexed = _is_indexed(path)
        file_hash = _short_file_hash(path) if indexed else ""
        header = format_mfs_headers(
            source=str(path),
            indexed=indexed,
            file_hash=file_hash,
            lines=line_range,
        )
        output = header + body
    click.echo(output, nl=False)
    if not output.endswith("\n"):
        click.echo("")


def _slice_by_lines(content: str, rng: str) -> str:
    a, _, b = rng.partition(":")
    lines = content.splitlines()
    try:
        start = max(1, int(a)) if a else 1
        end = int(b) if b else len(lines)
    except ValueError:
        return content
    return "\n".join(lines[start - 1 : end])


def _parse_line_range(rng: str, total_lines: int) -> tuple[int, int]:
    a, _, b = rng.partition(":")
    try:
        start = max(1, int(a)) if a else 1
        end = min(total_lines, int(b)) if b else total_lines
    except ValueError:
        return (1, total_lines)
    return (start, max(start, end))


def _suppressed_stderr():
    """Context manager that silences fd 2 for the duration of the block.

    Milvus Lite's C++ layer writes file-open errors directly to stderr
    (bypassing Python). We suppress only while probing Milvus in `status`
    so the user sees one clean note instead of a scary "Open … failed"
    line followed by our message.
    """
    import contextlib
    import os
    import sys

    @contextlib.contextmanager
    def _cm():
        try:
            stderr_fd = sys.stderr.fileno()
        except (AttributeError, ValueError, OSError):
            yield
            return
        try:
            saved = os.dup(stderr_fd)
        except OSError:
            yield
            return
        devnull = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(devnull, stderr_fd)
            yield
        finally:
            os.dup2(saved, stderr_fd)
            os.close(saved)
            os.close(devnull)

    return _cm()


def _path_looks_binary(path: Path, sample_size: int = 4096) -> bool:
    """Return True if `path` appears to be a binary file.

    Matches the heuristic in search.summary._looks_binary but operates on
    the raw bytes so we can refuse files like /bin/ls before ever decoding.
    """
    try:
        with open(path, "rb") as fh:
            sample = fh.read(sample_size)
    except OSError:
        return False
    if not sample:
        return False
    if b"\x00" in sample:
        return True
    # Count non-printable control bytes (outside \t \n \r).
    allowed = {0x09, 0x0A, 0x0D}
    control = sum(1 for b in sample if b < 32 and b not in allowed)
    return control / len(sample) > 0.10


def _short_file_hash(path: Path) -> str:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 16), b""):
                h.update(chunk)
        return h.hexdigest()[:8]
    except OSError:
        return ""


def _is_indexed(path: Path) -> bool:
    """Best-effort check whether `path` has body chunks in Milvus.

    We close the Milvus client right after the lookup so downstream processes
    in a pipe (e.g. `mfs cat | mfs search`) can acquire the Lite file lock.
    """
    config = load_config()
    store: MilvusStore | None = None
    try:
        embedder = _build_embedder(config)
        store = _build_store(config, embedder.dimension)
        if store.is_empty():
            return False
        indexed = store.get_indexed_files(str(path.parent))
        return str(path) in indexed
    except Exception:
        return False
    finally:
        if store is not None:
            store.close()


if __name__ == "__main__":
    main()
