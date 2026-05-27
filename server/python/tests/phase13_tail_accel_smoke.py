"""Phase 13 — native tail_lines parity + edge cases (Rust accel). No network/keys.

Verifies mfs_server_rs.tail_lines (read-from-EOF) matches the naive last-n-lines over
files with/without a trailing newline, unicode, fewer-than-n lines, n=0, and a file large
enough to span the backward-read chunk boundary. Also checks the pure-Python fallback.
"""
import os
import tempfile

from mfs_server.common import accel

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, cond):
    results.append(bool(cond)); print(f"  [{OK if cond else FAIL}] {name}"); return cond


def _expected(text, n):
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    return lines[-n:] if n > 0 else []


def _pyfallback(path, n):
    # mirror accel.tail_lines' pure-Python branch regardless of HAVE_NATIVE
    if n <= 0:
        return []
    buf = b""
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END); pos = f.tell(); chunk = 65536
        while pos > 0 and buf.count(b"\n") <= n:
            read = min(chunk, pos); pos -= read; f.seek(pos); buf = f.read(read) + buf
    text = buf.decode("utf-8", errors="replace")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    return lines[-n:]


def main():
    check("native extension loaded", accel.HAVE_NATIVE)
    import mfs_server_rs as rs
    d = tempfile.mkdtemp(prefix="mfs_tail_")
    cases = {
        "trailing_nl": "".join(f"line{i}\n" for i in range(200)),
        "no_trailing_nl": "\n".join(f"row{i}" for i in range(200)),
        "few_lines": "a\nb\nc\n",
        "unicode": "".join(f"行{i} café\n" for i in range(50)),
        "big": "".join(f"shard {i:06d} payload xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx\n" for i in range(5000)),
        "empty": "",
    }
    paths = {}
    for name, content in cases.items():
        p = f"{d}/{name}.txt"; open(p, "w").write(content); paths[name] = p

    for name, content in cases.items():
        for n in (0, 1, 5, 20, 1000):
            exp = _expected(content, n)
            nat = rs.tail_lines(paths[name], n)
            check(f"{name} n={n}: native == expected", nat == exp)
            check(f"{name} n={n}: py-fallback == expected", _pyfallback(paths[name], n) == exp)

    import shutil; shutil.rmtree(d, ignore_errors=True)
    passed = sum(results)
    print(f"\n{'='*46}\n  tail_lines accel parity: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
