---
name: mfs
description: Progressive browsing CLI for large codebases and document collections — directory listings with auto-summaries and density-controlled file views. Complements agent shell tools (grep / glob / find / cat) — pick whichever fits each sub-task.
---

<!-- A2 (browse-only) is an ablation arm only. Do NOT ship as standalone product.
     Browse tools work as the magnifier on top of search (use A3_full for that). -->

# MFS — progressive browsing (browse lobe)

MFS is a CLI for progressively browsing a large corpus when you want to
**navigate structure** rather than search content. It does NOT replace your
agent shell tools — `grep`, `Glob`, `find`, `Read` remain fully available and
often the fastest path for exact identifiers, filename patterns, or reading
known files. Use MFS when directory-tree and section-level **structural**
angles pay off.

**All agent shell tools are available**: `Grep`, `Glob`, `Read`, `Bash`
(including `grep`, `rg`, `find`, `fd`). Use them as you normally would.
MFS browse tools are layered on top — reach for them when structure and
summaries help.

## Mental model: directories and files are naturally a tree

The corpus is a tree. Directories contain files, files contain sections,
sections contain paragraphs or functions. MFS gives you tools at every layer,
so you can drop straight into whichever layer the task needs:

- `mfs ls` / `mfs tree` — directory layer, with auto-extracted summaries per entry
- `mfs cat --peek/--skim/--deep` — file structure layer (heading / symbol skeleton)
- `mfs cat <file> -n A:B` — paragraph layer (specific lines)

You do **not** have to navigate strictly top-down. If you already know the
question is about `./docs/auth/`, start with `mfs ls ./docs/auth/`. If the
corpus is unfamiliar, `mfs tree --peek -L 2 .` gives a bird's-eye view.

## When to reach for MFS vs agent shell tools

| Sub-task | Reach for |
|---|---|
| Exact identifier / error code / literal string | agent shell `Grep` (instant) |
| Filename pattern (`**/*.py`) | agent shell `Glob` or `find` |
| Read a known file | agent shell `Read` |
| Read specific lines | agent shell `Read` offset/limit or `mfs cat -n A:B` |
| File structure overview without reading body | `mfs cat --peek` |
| Directory contents with semantic summaries | `mfs ls` / `mfs tree` |
| Unfamiliar corpus — what's even in here? | `mfs tree --peek -L 2 .` |

You typically only need ONE tool per sub-task. If the result is correct,
that is the answer — do not re-verify with another tool.

## Command reference

### `mfs cat <file> [-n A:B] [--peek | --skim | --deep] [-W N] [-H N] [-D N]`

Read file content with optional density control. Three preset density levels,
plus low-level `W / H / D` overrides.

| Mode | What you get | When |
|---|---|---|
| (no flag) | full file content | want the bytes |
| `-n A:B` | lines A through B | narrow read by line range |
| `--peek` | heading / function-signature skeleton only | "what's in this file?" — cheapest |
| `--skim` | headings + first paragraph per section | medium overview |
| `--deep` | expanded structure with most body | almost-full digest |

#### Low-level density controls (W / H / D)

Override or fine-tune the preset:

- **`-W N`**: max characters per node (controls width of each section's preview).
- **`-H N`**: max number of nodes shown (controls height — how many sections).
- **`-D N`**: depth — how many heading levels to expand (e.g. `-D 2` for `#` and `##` only).

```
mfs cat --peek ./src/auth.py               # preset peek
mfs cat --skim ./docs/auth.md -H 5         # skim, but only 5 sections
mfs cat -n 40:80 ./src/handler.py          # narrow read by lines
mfs cat ./docs/auth.md -W 200 -H 10 -D 3   # custom WHD
```

### `mfs ls <dir>` and `mfs tree <dir> [-L N]`

Directory listing with per-file auto-extracted summary (heading + first line).
`tree` is recursive with `-L` depth limit.

```
mfs ls ./docs/
mfs tree --peek -L 2 .
mfs tree --skim ./src/
```

Both commands accept `--peek / --skim / --deep` and `-W / -H / -D` to control
the per-file summary density (same semantics as `mfs cat`).

## Common patterns (situations, not steps)

- **Unfamiliar corpus** → `mfs tree --peek -L 2 .` gives a bird's-eye view
  in one call. Then zoom into the most promising subtree.
- **Candidate file chosen** → `mfs cat --peek <path>` to confirm topic via
  headings / function signatures, then jump to specific lines.
- **Sibling files might be relevant** → `mfs ls <dir-of-hit> --skim` shows
  what each file is about, at a glance.
- **Exact keyword in mind** → use native `Grep` directly. MFS browse adds no
  value for literal matches.
- **Need surrounding context** → `mfs cat -n 30:80 <path>` or native `Read`.

## Common pitfalls

- Do **not** run `mfs cat <file>` on a file you already have (from native
  `Read` or from a prior `mfs cat`). You already have those bytes.
- Do **not** `--deep` every file — it can be as large as the full content.
  Start with `--peek` and step up only if needed.
- Do **not** reach for `mfs cat --peek` when you just want to grep for a
  stable identifier — native `Grep` is instant and fine.
- Do **not** request very high `-H` / `-D` unless you actually need that
  much structure. Preset `--peek` / `--skim` is usually enough.

## Output

End your reply with exactly one line, no text after it:

```
ANSWER: <relative/file/path | short answer>
```
