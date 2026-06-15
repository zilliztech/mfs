"""SummaryWorker pool (§6.4.3).

Each worker pulls a ready (job_id, dir_uri) from the queue, folds its children content
(child file excerpts + already-computed sub-dir summaries), calls the summary LLM, writes
the result back into the dir node, decrements the parent's pending (pushing it when it hits
zero), and emits a directory_summary Chunk into the shared chunks_q. A child document reuses
the Object Lane's converted_md from the artifact cache (currency token); a child image goes
through the VLM client (transformation-cache memoized); code / markdown are read directly.
"""

from __future__ import annotations

import os

from ...common.converter import CONVERT_EXTS
from ..producers.base import read_bytes, read_text


async def _child_text(coord, job_id: str, child_uri: str, okind: str) -> str:
    """Content excerpt for one child file, capped at per_file_max_kb. A document reuses the
    Object Lane's converted_md artifact when the currency token matches (else it is converted
    once and cached for both lanes); an image goes through the VLM client (transformation-cache
    memoized); code / markdown are read directly."""
    plugin = coord.job_plugins.get(job_id)
    if plugin is None:
        return ""
    cap = coord.cfg.summary.per_file_max_kb * 1024
    ext = os.path.splitext(child_uri)[1].lower()
    if okind == "image":
        # Fold an image only when descriptions are produced at all ([description] on) AND the
        # directory summary opts into them. With [description] off there is no VLM provider /
        # budget, so firing describe() here would error or stall.
        if not coord.description_enabled or not coord.cfg.summary.include_image_description:
            return ""
        raw = await read_bytes(plugin, child_uri)
        # share the description gate so a folded-in image draws from the same VLM in-flight
        # budget as the Object Lane ImageChunksProducer (§5.5).
        async with coord.description_gate:
            out = await coord.vlm.describe(raw, ext)
        return out[:cap]
    if okind == "document" and ext in CONVERT_EXTS:
        raw = await read_bytes(plugin, child_uri)
        # Reuse the Object Lane's converted_md when it matches this source + converter version
        # (currency token), else convert and cache it so the Object Lane reuses it in turn.
        if coord.artifacts is not None:
            currency = coord.converter.currency(raw)
            builder = coord.builders.get(job_id)
            full_uri = (builder.connector_uri if builder else "") + child_uri
            art = await coord.artifacts.get_artifact_fresh(
                coord.namespace_id, full_uri, "converted_md", currency
            )
            if art is not None:
                return art.decode("utf-8", errors="replace")[:cap]
            md = await coord.converter.convert(raw, ext)
            await coord.artifacts.put_artifact(
                coord.namespace_id, full_uri, "converted_md", md.encode(), currency
            )
            return md[:cap]
        return (await coord.converter.convert(raw, ext))[:cap]
    if okind in ("document", "code", "text_blob"):
        return (await read_text(plugin, child_uri))[:cap]
    return ""  # binary / structured: not folded into a directory summary


async def fold_and_summarize(coord, job_id: str, dir_uri: str) -> None:
    """Process one ready directory: fold children -> summarize -> bookkeeping -> emit."""
    builder = coord.builders.get(job_id)
    if builder is None:
        return
    node = builder.tree.get(dir_uri)
    if node is None:
        return

    summ = ""
    try:
        parts: list[str] = []
        for child_uri, okind in node.children_files:
            try:
                txt = await _child_text(coord, job_id, child_uri, okind)
            except Exception:  # noqa: BLE001 — a vanished/unreadable child must not sink the dir
                txt = ""
            if txt.strip():
                parts.append(f"## file {os.path.basename(child_uri)}\n{txt}")
        for sub_uri in node.children_dirs:
            sub = builder.tree.get(sub_uri)
            if sub and sub.summary and sub.summary.strip():
                parts.append(f"## subdirectory {sub_uri}\n{sub.summary}")
        listing = "\n\n".join(parts)[: coord.cfg.summary.max_input_kb * 1024]
        if listing.strip():
            # acquire the summary gate even though the SummaryWorker pool is already sized by
            # [summary].concurrency — so any other caller of summary.summarize shares the same
            # provider in-flight budget (§5.5, forward-safety).
            async with coord.summary_gate:
                summ = await coord.summary.summarize(listing, "directory_summary")
    except Exception as e:  # noqa: BLE001 — never leave a dir un-finalized (would wedge the job)
        print(f"mfs-server: WARNING directory summary {dir_uri} failed: {e}", flush=True)
        summ = ""

    # bookkeeping always runs: write back, decrement parent pending, push parent if ready.
    node.summary = summ
    if node.parent and node.parent in builder.tree:
        pnode = builder.tree[node.parent]
        pnode.pending -= 1
        if pnode.pending == 0:
            coord.queue.push(job_id, node.parent, pnode.depth)
    await coord.emit_dir_summary(job_id, builder.connector_uri, dir_uri, summ)


async def run_summary_worker(coord, worker_id: int) -> None:
    """One SummaryWorker coroutine: drain ready dirs forever."""
    while True:
        job_id, dir_uri = await coord.queue.ready_q.get()
        try:
            await fold_and_summarize(coord, job_id, dir_uri)
        except Exception as e:  # noqa: BLE001 — last-resort guard so the worker never dies
            print(f"mfs-server: WARNING summary worker {worker_id} error: {e}", flush=True)
