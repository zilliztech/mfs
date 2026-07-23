"""ReadService pure helpers: density view skeleton + locator match guard +
read-path size caps, usable by ReadService and its tests without a connector
stack."""

from __future__ import annotations

import re

from ...producers.render import resolve_path

_HEAD_CACHE_N = 100  # rows pre-cached per structured object to speed `head`
_BARE_CAT_MAX_BYTES = 5 * 1024 * 1024  # bare `cat` (no range) rejects objects larger than this
_GREP_LINEAR_SCAN_MAX = 200  # cap on not-indexed files a single grep scans linearly

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


def _locator_matches(rec: dict, ocfg, idx: int, locator: dict) -> bool:
    if "_row" in locator:
        return idx == int(locator["_row"])
    # "lines" is the framework-reserved key for body/code chunks and is never a
    # structured-record PK - never compare it against the row. The cat router
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
