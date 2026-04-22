"""Indexing pipeline: scanner, chunker, task queue, embedding worker."""

from __future__ import annotations

from .chunker import (
    Chunk,
    chunk_file,
    chunks_for_path,
    extract_frontmatter,
    generate_chunk_id,
    hash_text,
)
from .queue import QueueTask, TaskQueue
from .scanner import FileInfo, Scanner, SyncDiff
from .worker import Worker, load_status, save_status, worker_main

__all__ = [
    "Chunk",
    "FileInfo",
    "QueueTask",
    "Scanner",
    "SyncDiff",
    "TaskQueue",
    "Worker",
    "chunk_file",
    "chunks_for_path",
    "extract_frontmatter",
    "generate_chunk_id",
    "hash_text",
    "load_status",
    "save_status",
    "worker_main",
]
