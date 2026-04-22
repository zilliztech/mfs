"""File-based task queue for async embedding.

queue.json format:
    {"tasks": [QueueTask, ...]}

Access is serialized via filelock (10s timeout). Tasks are deduped by chunk_id
on enqueue.

Task types
----------
- ``embed``        — default. ``chunk_text`` is already populated; the worker
                     just embeds it and writes the chunk record.
- ``llm_summarize`` — the worker reads ``source``, runs an LLM ``generate``
                     call, then embeds the resulting summary as a
                     ``chunk_index=-1`` record.
- ``vlm_describe``  — the worker calls ``describe_image(source)`` on a
                     vision-capable LLM provider, then embeds the resulting
                     description as a ``chunk_index=-1`` record.

Backward compatibility: tasks persisted before ``task_type`` was introduced
load fine — the field defaults to ``"embed"`` and an unknown key is dropped
during loading.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from filelock import FileLock, Timeout


@dataclass
class QueueTask:
    chunk_id: str
    source: str
    parent_dir: str
    chunk_text: str
    chunk_index: int
    start_line: int
    end_line: int
    content_type: str
    file_hash: str
    is_dir: bool
    metadata: dict
    account_id: str
    task_type: str = "embed"


LOCK_TIMEOUT = 10.0


class TaskQueue:
    """File-based task queue with filelock."""

    def __init__(self, queue_path: Path):
        self._path = queue_path
        self._lock = FileLock(str(queue_path) + ".lock", timeout=LOCK_TIMEOUT)

    # ---------------------------------------------------------------- helpers

    def _load(self) -> list[dict]:
        if not self._path.exists():
            return []
        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            return []
        tasks = data.get("tasks") if isinstance(data, dict) else None
        return tasks if isinstance(tasks, list) else []

    def _save(self, tasks: list[dict]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"tasks": tasks}, fh)
        tmp.replace(self._path)

    # ---------------------------------------------------------------- API

    def enqueue(self, tasks: list[QueueTask]) -> int:
        if not tasks:
            return 0
        try:
            with self._lock:
                existing = self._load()
                seen = {t.get("chunk_id") for t in existing}
                added = 0
                for t in tasks:
                    if t.chunk_id in seen:
                        continue
                    existing.append(asdict(t))
                    seen.add(t.chunk_id)
                    added += 1
                self._save(existing)
                return added
        except Timeout:
            raise RuntimeError(f"Timed out acquiring queue lock at {self._path}")

    def dequeue(self, batch_size: int = 50) -> list[QueueTask]:
        try:
            with self._lock:
                existing = self._load()
                if not existing:
                    return []
                batch_raw = existing[:batch_size]
                remaining = existing[batch_size:]
                self._save(remaining)
                allowed = {f.name for f in fields(QueueTask)}
                return [
                    QueueTask(**{k: v for k, v in t.items() if k in allowed})
                    for t in batch_raw
                ]
        except Timeout:
            raise RuntimeError(f"Timed out acquiring queue lock at {self._path}")

    def size(self) -> int:
        try:
            with self._lock:
                return len(self._load())
        except Timeout:
            return 0

    def is_empty(self) -> bool:
        return self.size() == 0

    def clear(self) -> None:
        with self._lock:
            self._save([])
