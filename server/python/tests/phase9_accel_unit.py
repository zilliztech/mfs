"""Phase 9 — Rust acceleration (mfs_server_rs) unit + parity tests. No network/keys.

Verifies the native extension is loaded AND that native output is identical to the
pure-Python fallback (so the server behaves the same with or without the wheel).
Run: cd server/python && .venv/bin/python tests/phase9_accel_unit.py
"""
import json
import os
import tempfile

from mfs_server.common import accel

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, cond):
    results.append((name, bool(cond)))
    print(f"  [{OK if cond else FAIL}] {name}")
    return cond


# pure-Python reference implementations (independent of accel's fallback)
def py_scan(root, ignore):
    out = []
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if not any(s in os.path.join(dp, d) for s in ignore)]
        for fn in fns:
            full = os.path.join(dp, fn)
            if any(s in full for s in ignore):
                continue
            out.append("/" + os.path.relpath(full, root).replace("\\", "/"))
    return sorted(out)


def main():
    check("native extension loaded (HAVE_NATIVE)", accel.HAVE_NATIVE)

    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, "sub"))
    os.makedirs(os.path.join(d, ".git"))
    open(os.path.join(d, "a.txt"), "w").write("hello world\nFOO bar\nhello again\n")
    open(os.path.join(d, "sub", "b.py"), "w").write("def foo():\n    return 42\n")
    open(os.path.join(d, ".git", "ignored"), "w").write("x\n")

    # scan_dir parity + ignore pruning
    paths = sorted(p for p, _, _ in accel.scan_dir(d, ["/.git/"]))
    check("scan_dir matches pure-Python ref + prunes .git",
          paths == py_scan(d, ["/.git/"]) and all("/.git/" not in p for p in paths))
    check("scan_dir returns size+mtime tuples",
          all(isinstance(s, int) and isinstance(mt, int) for _, s, mt in accel.scan_dir(d, [])))

    # grep: literal, case-insensitive
    af = os.path.join(d, "a.txt")
    g_ci = accel.linear_grep_file(af, "hello", True, False, 100)
    check("grep literal ci -> 2 hits w/ line nums", [ln for ln, _ in g_ci] == [1, 3])
    g_cs = accel.linear_grep_file(af, "hello", False, False, 100)
    check("grep literal case-sensitive -> 2 lowercase hits", [ln for ln, _ in g_cs] == [1, 3])
    g_foo = accel.linear_grep_file(af, "FOO", False, False, 100)
    check("grep case-sensitive 'FOO' -> line 2 only", [ln for ln, _ in g_foo] == [2])
    # grep: regex
    g_rx = accel.linear_grep_file(os.path.join(d, "sub", "b.py"), r"def \w+", False, True, 100)
    check("grep regex 'def \\w+' -> line 1", [ln for ln, _ in g_rx] == [1])
    g_max = accel.linear_grep_file(af, "hello", True, False, 1)
    check("grep max_matches respected", len(g_max) == 1)

    # jsonl
    jf = os.path.join(d, "r.jsonl")
    with open(jf, "w") as f:
        f.write(json.dumps({"subject": "Login bug", "body": "broke", "id": 1}) + "\n")
        f.write("\n")  # blank line ignored
        f.write(json.dumps({"subject": "Theme", "id": 2}) + "\n")
    check("jsonl_record_count ignores blank lines", accel.jsonl_record_count(jf) == 2)
    texts = accel.jsonl_field_texts(jf, ["subject", "body"], 100)
    check("jsonl_field_texts renders fields, skips missing",
          texts == ["subject: Login bug\nbody: broke", "subject: Theme"])

    passed = sum(1 for _, c in results if c)
    print(f"\n{'='*44}\n  {passed}/{len(results)} checks passed (native={accel.HAVE_NATIVE})")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
