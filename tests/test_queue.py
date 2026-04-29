"""Queue tests."""

from __future__ import annotations

from mfs.ingest.queue import QueueTask, TaskQueue


def _make_task(cid: str) -> QueueTask:
    return QueueTask(
        chunk_id=cid,
        source="/tmp/x.md",
        parent_dir="/tmp",
        chunk_text="hello",
        chunk_index=0,
        start_line=1,
        end_line=1,
        content_type="markdown",
        file_hash="abc",
        is_dir=False,
        metadata={},
        account_id="default",
    )


def test_enqueue_and_dequeue(tmp_path):
    q = TaskQueue(tmp_path / "queue.json")
    assert q.is_empty()
    added = q.enqueue([_make_task("a"), _make_task("b")])
    assert added == 2
    assert q.size() == 2
    batch = q.dequeue(batch_size=10)
    assert len(batch) == 2
    assert q.is_empty()


def test_enqueue_deduplicates(tmp_path):
    q = TaskQueue(tmp_path / "queue.json")
    q.enqueue([_make_task("a")])
    added = q.enqueue([_make_task("a"), _make_task("b")])
    assert added == 1
    assert q.size() == 2


def test_dequeue_respects_batch_size(tmp_path):
    q = TaskQueue(tmp_path / "queue.json")
    q.enqueue([_make_task(f"c{i}") for i in range(5)])
    first = q.dequeue(batch_size=2)
    assert len(first) == 2
    assert q.size() == 3
