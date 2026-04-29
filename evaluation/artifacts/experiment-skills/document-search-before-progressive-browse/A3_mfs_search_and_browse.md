---
name: mfs-full
description: Semantic search plus progressive browsing over codebases and document collections.
---

# MFS Full Skill

Use both MFS capabilities together:

- Search is the finder for semantic or paraphrased intent.
- Browse is the magnifier for structure, neighboring context, and final
  verification.

Native tools still matter for exact literals, file-name patterns, and simple
known-file reads.

## Decision Tree

Start by classifying the sub-task:

- Natural-language concept, user question, behavior, policy, procedure,
  feature, or implementation intent -> start with `mfs search`.
- Exact identifier, error code, URL, ticket ID, or unique phrase -> native
  `rg` / `grep`.
- Filename pattern -> native `find`.
- Known file structure or section navigation -> `mfs cat --peek` / `--skim`.
- Need surrounding context around a search hit -> `mfs cat -n A:B`.

## Recommended Flow

For unknown target:

```bash
mfs search "<natural-language query>" --all --top-k 20
```

For known scope:

```bash
mfs search "<natural-language query>" <path> --top-k 20
```

Then inspect the top candidates. If the best snippet is enough, answer from
the returned path and line range. If candidates are close or the snippet is
too narrow, use browse:

```bash
mfs cat --peek <candidate-path>
mfs cat --skim <candidate-path>
mfs cat <candidate-path> -n <start>:<end>
```

## Code vs Document Hints

For code:

- Prefer the chunk that contains the actual implementation, symbol, function,
  class, config, or test relevant to the request.
- Use browse to inspect surrounding functions or module structure.

For documents:

- Prefer the article/section whose title, headings, and snippet directly
  answer the user question.
- Search may return several related pages; compare top 3-5 candidates before
  choosing when titles are similar.
- Use `--peek` / `--skim` to verify that a candidate article contains the
  right procedure, policy, FAQ, or section before returning it.

## Anti-Patterns

- Do not use `mfs ls` or `mfs tree` as the first step when the target is an
  unknown semantic concept; search first.
- Do not re-run native grep just to prove a successful search hit exists.
- Do not read whole large files when a heading overview or line window is
  enough.
- Do not blindly choose rank 1 when the task expects an article/file-level
  answer and several top candidates are related.

Follow the output format requested by the task prompt exactly.
