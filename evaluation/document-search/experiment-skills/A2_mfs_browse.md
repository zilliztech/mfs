---
name: mfs-browse
description: Progressive browsing over indexed codebases and document collections.
---

# MFS Browse Lobe

Use this skill when a task benefits from navigating structure at controlled
density. Browse is useful for known files/directories, unfamiliar corpus
layout, comparing nearby files, and narrowing from headings to line ranges.

Do not use `mfs search`, `mfs grep`, or `mfs add` in this arm.

## Decision Tree

Start by classifying the sub-task:

- Need a high-level map of an unfamiliar corpus or directory -> `mfs tree`.
- Need to compare files/articles under a known directory -> `mfs ls`.
- Need a known file's structure -> `mfs cat --peek`.
- Need a concise but richer known-file overview -> `mfs cat --skim`.
- Need exact surrounding lines once a section is known -> `mfs cat -n A:B`.
- Need exact literal matching -> native `rg` / `grep`.
- Need filename patterns -> native `find`.

## Browse Patterns

Directory overview:

```bash
mfs tree --peek -L 2 <dir>
mfs ls <dir> --skim
```

Known file overview:

```bash
mfs cat --peek <path>
mfs cat --skim <path>
```

Line-window read:

```bash
mfs cat <path> -n <start>:<end>
```

For code, `--peek` should reveal symbols, classes, functions, and headings.
For documents, `--peek` / `--skim` should reveal titles, headings, steps,
FAQs, sections, or article structure.

## Candidate Selection

Use browse to reduce context before reading full files. Compare candidate
titles/headings/sections when multiple files look similar. Avoid dumping full
files unless the task cannot be solved from structure or narrow line windows.

Native tools remain available for exact text matching and file-name lookup.

Follow the output format requested by the task prompt exactly.
