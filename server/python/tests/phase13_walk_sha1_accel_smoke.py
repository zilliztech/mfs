"""Phase 13 — native walk_tree (gitignore) + sha1_files parity (Rust accel). No keys.

walk_tree (ignore crate) must select EXACTLY the same files as os.walk + pathspec
gitwildmatch — same relpaths, sizes, mtimes, inodes — over a tree with dir-ignores, globs,
nested paths, and a negation. sha1_files must match hashlib (and report None for unreadable).
"""

import hashlib
import os
import tempfile

import pathspec

from mfs_server.common import accel

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, cond):
    results.append(bool(cond))
    print(f"  [{OK if cond else FAIL}] {name}")
    return cond


def _py_walk(root, patterns):
    """The os.walk + pathspec reference (= the connector's prior behavior)."""
    spec = pathspec.PathSpec.from_lines("gitwildmatch", patterns)
    out = {}
    for dp, dirs, files in os.walk(root):
        kept = []
        for d in dirs:
            rel = os.path.relpath(os.path.join(dp, d), root).replace(os.sep, "/") + "/"
            if not spec.match_file(rel):
                kept.append(d)
        dirs[:] = kept
        for fn in files:
            full = os.path.join(dp, fn)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if spec.match_file(rel):
                continue
            st = os.stat(full)
            out["/" + rel] = (st.st_size, st.st_mtime_ns, st.st_ino)
    return out


def main():
    check("native extension loaded", accel.HAVE_NATIVE)
    import mfs_server_rs as rs

    root = tempfile.mkdtemp(prefix="mfs_walk_")
    os.makedirs(f"{root}/node_modules/pkg", exist_ok=True)
    os.makedirs(f"{root}/build", exist_ok=True)
    os.makedirs(f"{root}/.git", exist_ok=True)
    os.makedirs(f"{root}/src/sub", exist_ok=True)
    files = {
        "a.py": "print(1)\n",
        "b.log": "noise\n",
        "keep.log": "kept by negation\n",
        "secret_key.txt": "x\n",
        "node_modules/pkg/index.js": "//\n",
        "build/out.o": "bin\n",
        ".git/config": "[core]\n",
        "src/app.py": "def f(): pass\n",
        "src/sub/util.py": "u\n",
        "src/scratch.tmp": "t\n",
        "readme.md": "# r\n",
    }
    for rel, c in files.items():
        open(f"{root}/{rel}", "w").write(c)
    patterns = ["*.log", "!keep.log", "node_modules/", "build/", ".git/", "secret_*", "**/*.tmp"]

    native = {rel: (sz, mt, ino) for rel, sz, mt, ino in rs.walk_tree(root, patterns)}
    ref = _py_walk(root, patterns)
    check("walk_tree: identical file SET vs os.walk+pathspec", set(native) == set(ref))
    check(
        "walk_tree: ignored globs/dirs excluded (b.log, node_modules, build, .git, secret_, .tmp)",
        not any(
            x in native
            for x in (
                "/b.log",
                "/node_modules/pkg/index.js",
                "/build/out.o",
                "/.git/config",
                "/secret_key.txt",
                "/src/scratch.tmp",
            )
        ),
    )
    check("walk_tree: negated keep.log included", "/keep.log" in native)
    check(
        "walk_tree: normal files included",
        {"/a.py", "/src/app.py", "/src/sub/util.py", "/readme.md"} <= set(native),
    )
    check(
        "walk_tree: size/mtime_ns/inode match os.stat for every file",
        all(native[k] == ref[k] for k in ref),
    )

    # sha1_files parity
    sf_paths = [f"{root}/{r}" for r in ("a.py", "src/app.py", "readme.md")]
    open(f"{root}/empty.txt", "w").close()
    sf_paths.append(f"{root}/empty.txt")
    with open(f"{root}/bin.dat", "wb") as f:
        f.write(bytes(range(256)) * 32)
    sf_paths.append(f"{root}/bin.dat")
    sf_paths.append(f"{root}/does_not_exist")  # unreadable -> None
    got = accel.sha1_files(sf_paths)
    exp = {}
    for p in sf_paths:
        try:
            h = hashlib.sha1()
            with open(p, "rb") as f:
                for ch in iter(lambda: f.read(65536), b""):
                    h.update(ch)
            exp[p] = h.hexdigest()
        except OSError:
            exp[p] = None
    check("sha1_files: matches hashlib for all (incl. empty + binary)", got == exp)
    check("sha1_files: unreadable path -> None", got[f"{root}/does_not_exist"] is None)

    import shutil

    shutil.rmtree(root, ignore_errors=True)
    passed = sum(results)
    print(f"\n{'=' * 46}\n  walk_tree + sha1_files parity: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
