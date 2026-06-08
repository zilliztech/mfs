"""TextChunksProducer — file / code / document okind.

code  -> Chonkie CodeChunker (AST/tree-sitter), reused from processors.text.chunk_body.
other -> Chonkie RecursiveChunker with markdown-friendly RecursiveRules (heading >
         paragraph > sentence, §5.3) so headings aren't split away from their content.

The `document` okind covers every "ends up as markdown" object — raw .md, PDF/DOCX/HTML
converted to markdown, and web/github markdown — so the markdown rules apply broadly.
Binary documents (CONVERT_EXTS) go through the converter first; web/github text is also
persisted as a converted_md artifact so `mfs cat` can read it.
"""

from __future__ import annotations

from functools import lru_cache
from typing import AsyncIterator

from chonkie import RecursiveChunker, RecursiveLevel, RecursiveRules

from ...common.converter import CONVERT_EXTS
from ...processors.text import _offset_to_line, chunk_body
from .base import (
    CONTENT_MAX,
    Chunk,
    EndOfTask,
    ObjectTask,
    ProducedItem,
    ProducerContext,
    read_bytes,
    read_text,
)

# Heading-first recursive rules for markdown / document content. RecursiveChunker
# falls through level by level: split on headings first, then paragraphs, then
# sentences, so a heading keeps its surrounding context in one chunk.
_MARKDOWN_RULES = RecursiveRules(
    levels=[
        RecursiveLevel(delimiters=["\n# ", "\n## ", "\n### "]),
        RecursiveLevel(delimiters=["\n\n"]),
        RecursiveLevel(delimiters=[". ", "! ", "? "]),
    ]
)


@lru_cache(maxsize=16)
def _markdown_chunker(chunk_size: int) -> RecursiveChunker:
    return RecursiveChunker(chunk_size=chunk_size, rules=_MARKDOWN_RULES)


def _chunk_document(content: str, chunk_size: int) -> list[tuple[str, list[int]]]:
    """Markdown-aware recursive chunking; returns (text, [start_line, end_line])."""
    if not content.strip():
        return []
    chunker = _markdown_chunker(chunk_size)
    chunks = chunker(content)
    out: list[tuple[str, list[int]]] = []
    for c in chunks:
        if not c.text.strip():
            continue
        out.append(
            (
                c.text,
                [_offset_to_line(content, c.start_index), _offset_to_line(content, c.end_index)],
            )
        )
    return out


def chunk_text_body(
    content: str, okind: str, ext: str, chunk_size: int
) -> list[tuple[str, list[int]]]:
    """Dispatch by okind: code -> CodeChunker (via processors.text), else markdown rules."""
    if okind == "code":
        return chunk_body(content, okind, ext, chunk_size)
    return _chunk_document(content, chunk_size)


class TextChunksProducer:
    """document / code / text_blob -> body chunks."""

    def __init__(self, ctx: ProducerContext):
        self.ctx = ctx

    async def produce(self, task: ObjectTask) -> AsyncIterator[ProducedItem]:
        ns = self.ctx.namespace_id
        full_uri = task.full_uri
        ext = task.ext
        okind = task.okind
        ocfg = task.config()

        if okind == "document" and ext in CONVERT_EXTS:
            raw = await read_bytes(task.plugin, task.object_uri)
            text = await self.ctx.converter.convert(raw, ext)
            await self.ctx.artifacts.put_artifact(ns, full_uri, "converted_md", text.encode())
        else:
            text = await read_text(task.plugin, task.object_uri)
            # web/github markdown isn't backed by a source file `cat` can re-read, so
            # persist it as a converted_md artifact for `mfs cat`/`head`.
            if task.connector_uri.startswith(("web://", "github://")):
                await self.ctx.artifacts.put_artifact(ns, full_uri, "converted_md", text.encode())

        pairs = chunk_text_body(text, okind, ext, self.ctx.cfg.chunking.chunk_size)
        chunk_max = ocfg.chunk_max
        truncated = len(pairs) > chunk_max
        if truncated:
            pairs = pairs[:chunk_max]

        for ctext, lines in pairs:
            # Body chunks: per-chunk identity is the line range, stored under the
            # reserved "lines" locator key.
            yield Chunk(
                content=ctext,
                chunk_kind="body",
                locator={"lines": lines},
                uri=full_uri,
                connector_job_id=task.connector_job_id,
                partial=len(ctext) > CONTENT_MAX,
            )
        yield EndOfTask(partial=truncated)
