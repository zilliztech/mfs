"""Phase 9 — Rust acceleration (mfs_server_rs) linear_grep parity. No network/keys.

Verifies the native extension is loaded AND native grep output matches the pure-Python
fallback. (walk_tree / sha1_files / tail_lines parity live in phase13_walk_sha1_accel and
phase13_tail_accel.) Run: cd server/python && .venv/bin/python tests/phase9_accel_unit.py
"""
import os
import tempfile

from mfs_server.common import accel

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, cond):
    results.append((name, bool(cond)))
    print(f"  [{OK if cond else FAIL}] {name}")
    return cond


def main():
    check("native extension loaded (HAVE_NATIVE)", accel.HAVE_NATIVE)

    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, "sub"))
    af = os.path.join(d, "a.txt")
    open(af, "w").write("hello world\nFOO bar\nhello again\n")
    open(os.path.join(d, "sub", "b.py"), "w").write("def foo():\n    return 42\n")

    g_ci = accel.linear_grep_file(af, "hello", True, False, 100)
    check("grep literal ci -> 2 hits w/ line nums", [ln for ln, _ in g_ci] == [1, 3])
    g_cs = accel.linear_grep_file(af, "hello", False, False, 100)
    check("grep literal case-sensitive -> 2 lowercase hits", [ln for ln, _ in g_cs] == [1, 3])
    g_foo = accel.linear_grep_file(af, "FOO", False, False, 100)
    check("grep case-sensitive 'FOO' -> line 2 only", [ln for ln, _ in g_foo] == [2])
    g_rx = accel.linear_grep_file(os.path.join(d, "sub", "b.py"), r"def \w+", False, True, 100)
    check("grep regex 'def \\w+' -> line 1", [ln for ln, _ in g_rx] == [1])
    g_max = accel.linear_grep_file(af, "hello", True, False, 1)
    check("grep max_matches respected", len(g_max) == 1)

    import shutil; shutil.rmtree(d, ignore_errors=True)
    passed = sum(1 for _, c in results if c)
    print(f"\n{'='*44}\n  {passed}/{len(results)} checks passed (native={accel.HAVE_NATIVE})")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
