---
name: mfs
description: Progressive browsing CLI for large codebases and document collections. Complements agent shell tools (grep / glob / find / cat) — pick whichever fits each sub-task.
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

- `mfs ls` / `mfs tree PATH -L N` — directory layer
- `mfs cat PATH --peek` / `mfs cat PATH --skim` — file structure layer
- `mfs cat PATH --range A:B` — paragraph layer (specific lines)

You do **not** have to navigate strictly top-down. If you already know the
question is about `./docs/auth/`, start with `mfs ls ./docs/auth/`. If the
corpus is unfamiliar, `mfs tree . -L 2` gives a bird's-eye view.

## When to reach for MFS vs agent shell tools

| Sub-task | Reach for |
|---|---|
| Exact identifier / error code / literal string | agent shell `Grep` (instant) |
| Filename pattern (`**/*.py`) | agent shell `Glob` or `find` |
| Read a known file | agent shell `Read` |
| Read specific lines | agent shell `Read` offset/limit or `mfs cat PATH --range A:B` |
| File structure overview without reading body | `mfs cat PATH --peek` |
| Directory contents and bounded tree shape | `mfs ls` / `mfs tree` |
| Unfamiliar corpus — what's even in here? | `mfs tree . -L 2` |

You typically only need ONE tool per sub-task. If the result is correct,
that is the answer — do not re-verify with another tool.

## Command reference

### `mfs cat PATH [--range A:B] [--locator JSON] [--peek | --skim]`

Read file content with optional line/record targeting or density presets.

| Mode | What you get | When |
|---|---|---|
| (no flag) | full file content | want the bytes |
| `--range A:B` | lines A through B | narrow read by line range |
| `--locator JSON` | exact hit or structured record | reopen a locator returned by search or grep |
| `--peek` | heading / function-signature skeleton only | "what's in this file?" — cheapest |
| `--skim` | headings + first paragraph per section | medium overview |

```
mfs cat ./src/auth.py --peek
mfs cat ./docs/auth.md --skim
mfs cat ./src/handler.py --range 40:80
mfs cat ./src/handler.py --locator '{"lines":[40,80]}'
```

### `mfs ls <dir>` and `mfs tree <dir> [-L N]`

Directory listing and recursive tree browsing. `tree` accepts `-L N` or
`--depth N` for a bounded depth.

```
mfs ls ./docs/
mfs tree . -L 2
mfs tree ./src/ --depth 3
```

## Common patterns (situations, not steps)

- **Unfamiliar corpus** → `mfs tree . -L 2` gives a bird's-eye view
  in one call. Then zoom into the most promising subtree.
- **Candidate file chosen** → `mfs cat <path> --peek` to confirm topic via
  headings / function signatures, then jump to specific lines.
- **Sibling files might be relevant** → `mfs ls <dir-of-hit>` shows the
  nearby files.
- **Exact keyword in mind** → use native `Grep` directly. MFS browse adds no
  value for literal matches.
- **Need surrounding context** → `mfs cat <path> --range 30:80` or native
  `Read`.

## Common pitfalls

- Do **not** run `mfs cat <file>` on a file you already have (from native
  `Read` or from a prior `mfs cat`). You already have those bytes.
- Do **not** use unsupported density-tuning flags such as `--deep`, `-H`,
  `-D`, or `-W`. Use `--peek`, `--skim`, or `--range`.
- Do **not** reach for `mfs cat PATH --peek` when you just want to grep for a
  stable identifier — native `Grep` is instant and fine.

## Output

End your reply with exactly one line, no text after it:

```
ANSWER: <relative/file/path | short answer>
```
