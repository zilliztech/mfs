"""Search logic: orchestrates store + embedder for search/grep commands."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .. import constants as C
from ..embedder import EmbeddingProvider
from ..ingest.scanner import Scanner
from ..store import MilvusStore, SearchResult


class SearchMode(Enum):
    HYBRID = "hybrid"
    SEMANTIC = "semantic"
    KEYWORD = "keyword"


@dataclass
class GrepMatch:
    source: str
    line_number: int
    line_text: str
    context_before: list[str] = field(default_factory=list)
    context_after: list[str] = field(default_factory=list)


class Searcher:
    """Orchestrates search and grep operations."""

    def __init__(self, store: MilvusStore, embedder: EmbeddingProvider, scanner: Scanner | None = None):
        self._store = store
        self._embedder = embedder
        self._scanner = scanner

    # --------------------------------------------------------------- search

    def search(
        self,
        query: str,
        mode: SearchMode = SearchMode.HYBRID,
        path_filter: str | None = None,
        top_k: int = 10,
    ) -> list[SearchResult]:
        query = query.strip()
        if not query:
            return []

        # Preflight: hybrid / BM25 kernels assert on an empty sparse index
        # (NaN / Inf from zero-document IDF) -- bail out with clean empty
        # results so all three modes behave identically on a fresh store.
        try:
            if self._store.is_empty():
                return []
        except Exception:
            # If even this lightweight probe fails, fall through to the
            # regular path and let the normal error handling take over.
            pass

        if mode == SearchMode.KEYWORD:
            results = self._store.keyword_search(query, path_filter, top_k=top_k)
        else:
            query_vector = self._embedder.embed([query])[0]
            if mode == SearchMode.SEMANTIC:
                results = self._store.semantic_search(query_vector, path_filter, top_k=top_k)
            else:
                results = self._store.hybrid_search(query_vector, query, path_filter, top_k=top_k)
        return self._post_process(results)

    # ----------------------------------------------------------------- grep

    def grep(
        self,
        pattern: str,
        path: Path | None = None,
        context_lines: int = 0,
        case_insensitive: bool = False,
        include_unindexed: bool = True,
    ) -> list[GrepMatch]:
        flags = re.IGNORECASE if case_insensitive else 0
        try:
            regex = re.compile(pattern, flags)
        except re.error as exc:
            raise ValueError(f"Invalid pattern: {exc}") from exc

        # path=None now means "whole index" (CLI --all flag). Callers wanting
        # cwd scoping must pass Path.cwd() explicitly — matches grep POSIX
        # semantics where path is positional, not implicit.
        if path is not None:
            path = path.resolve()
            path_filter: str | None = str(path)
        else:
            path_filter = None

        indexed_sources: list[str] = []
        try:
            indexed_sources = self._store.bm25_prefilter(
                pattern, path_filter, top_k=200
            )
        except Exception:
            indexed_sources = []

        matches: list[GrepMatch] = []
        scanned: set[str] = set()
        for src in indexed_sources:
            if src in scanned:
                continue
            scanned.add(src)
            if not Path(src).exists():
                continue
            matches.extend(_grep_file(Path(src), regex, context_lines))

        # Also scan not-indexed files under `path` via subprocess grep if requested.
        # Only meaningful when we have a concrete path — --all / whole-index grep
        # relies on the BM25 prefilter + full-index fallback below.
        if include_unindexed and path is not None and path.exists() and path.is_dir():
            unindexed = _list_unindexed_files(path, self._scanner)
            # Avoid double-grepping files already covered by BM25.
            unindexed = [p for p in unindexed if str(p) not in scanned]
            if unindexed:
                matches.extend(_system_grep(pattern, unindexed, regex, context_lines, case_insensitive))

        # If we found no indexed candidates, also scan the full index set under path
        # to catch exact matches the BM25 tokenizer missed.
        if not matches and path is not None and path.exists() and path.is_dir() and self._scanner is not None:
            all_indexed = self._scanner.scan([path])
            for f in all_indexed:
                if str(f.path) in scanned:
                    continue
                matches.extend(_grep_file(f.path, regex, context_lines))

        return matches

    # ------------------------------------------------------------- internals

    def _post_process(self, results: list[SearchResult]) -> list[SearchResult]:
        valid: list[SearchResult] = []
        stale: list[str] = []
        for r in results:
            if r.is_dir:
                valid.append(r)
                continue
            if r.source and Path(r.source).exists():
                valid.append(r)
            else:
                stale.append(r.source)
        if stale:
            try:
                self._store.delete_by_sources(stale)
            except Exception:
                pass
        return valid


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _grep_file(path: Path, regex: re.Pattern[str], context_lines: int) -> list[GrepMatch]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    matches: list[GrepMatch] = []
    for i, line in enumerate(lines):
        if regex.search(line):
            before = lines[max(0, i - context_lines) : i]
            after = lines[i + 1 : i + 1 + context_lines]
            matches.append(
                GrepMatch(
                    source=str(path),
                    line_number=i + 1,
                    line_text=line,
                    context_before=before,
                    context_after=after,
                )
            )
    return matches


def _list_unindexed_files(root: Path, scanner: Scanner | None) -> list[Path]:
    if scanner is None:
        return []
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dp = Path(dirpath)
        dirnames[:] = [d for d in dirnames if d not in C.IGNORED_DIRNAMES]
        for fn in filenames:
            fp = dp / fn
            cls = scanner.classify_file(fp)
            if cls == "not_indexed":
                files.append(fp)
    return files


def _system_grep(
    pattern: str,
    paths: list[Path],
    regex: re.Pattern[str],
    context_lines: int,
    case_insensitive: bool,
) -> list[GrepMatch]:
    """Shell out to grep for the not-indexed file set.

    Falls back to a pure-Python regex scan if `grep` is unavailable.
    """
    if not paths:
        return []
    try:
        # -I  → skip binary files silently (prevents "grep: (standard input):
        #        binary file matches" lines from polluting stderr).
        # -s  → suppress permission / missing-file error messages.
        args = ["grep", "-n", "-H", "-I", "-s"]
        if case_insensitive:
            args.append("-i")
        if context_lines > 0:
            args += ["-C", str(context_lines)]
        args += ["-E", pattern, "--"] + [str(p) for p in paths]
        result = subprocess.run(
            args, capture_output=True, text=True, check=False, timeout=30
        )
        matches: list[GrepMatch] = []
        for line in result.stdout.splitlines():
            # Format: "path:lineno:content" (or "path-lineno-content" for context lines)
            parsed = _parse_grep_line(line)
            if parsed:
                matches.append(parsed)
        return matches
    except (FileNotFoundError, subprocess.SubprocessError):
        matches = []
        for p in paths:
            matches.extend(_grep_file(p, regex, context_lines))
        return matches


def _parse_grep_line(line: str) -> GrepMatch | None:
    if not line:
        return None
    m = re.match(r"^(.*?):(\d+):(.*)$", line)
    if not m:
        return None
    return GrepMatch(
        source=m.group(1),
        line_number=int(m.group(2)),
        line_text=m.group(3),
    )
