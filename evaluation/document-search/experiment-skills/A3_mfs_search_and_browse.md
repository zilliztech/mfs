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
- Known file structure or section navigation -> `mfs cat PATH --peek` /
  `mfs cat PATH --skim`.
- Need surrounding context around a search hit -> `mfs cat PATH --range A:B`.

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
mfs cat <candidate-path> --peek
mfs cat <candidate-path> --skim
mfs cat <candidate-path> --range <start>:<end>
```

Use progressive browsing as a budget ladder:

- `--peek` is for title, outline, headings, symbols, and file shape.
- `--skim` is for short excerpts under relevant headings.
- `--range A:B` is for final confirmation around a known line range.
- `--locator JSON` reopens an exact search or grep hit when JSON results
  provide a locator.

Do not read a whole large file when `--peek`, `--skim`, or a line window can
answer the comparison.

## Code vs Document Hints

For code:

- Prefer the chunk that contains the actual implementation, symbol, function,
  class, config, or test relevant to the request.
- Use browse to inspect surrounding functions or module structure.

For documents:

- Prefer the article/section whose title, headings, and snippet directly
  answer the user question.
- Search may return several related pages; compare top 3-5 distinct candidate
  files before choosing when titles are similar.
- Use `mfs cat PATH --peek` to compare article outlines, then
  `mfs cat PATH --skim` on the strongest candidates to verify that they
  contain the right procedure, policy, FAQ, example, or section.
- If one candidate is a broad overview and another is a specific task,
  troubleshooting, setup, or reference page for the requested action, inspect
  the specific candidate before choosing the broad one.
- If the query contains a clear product, module, package, platform, language,
  framework, or document area, prefer candidates from the same context unless
  the content clearly says otherwise.

## Candidate Selection

Think at the file/article level, not only at the chunk level. If several search
hits come from the same file, merge them mentally into one candidate. Compare
distinct files by title, path, URL/slug when present, headings, and strongest
matching snippets.

For article/file-level answers, a locally relevant paragraph in a loosely
related file is weaker than a file whose main topic directly answers the user
request. When two candidates are close, inspect both outlines and relevant
sections before deciding.

## Completeness Check

Before final output, check whether the prompt asks for more than one target.
Multiple targets are often implied by two entities, two actions, a setup plus a
troubleshooting step, migration from one thing to another, or an answer that
needs both a policy/background page and a procedure page.

If multiple files/articles are plausibly needed, return all clearly supported
paths requested by the prompt. It is better to include a small number of
well-supported complementary files than to stop after the first correct one.

## Anti-Patterns

- Do not use `mfs ls` or `mfs tree` as the first step when the target is an
  unknown semantic concept; search first.
- Do not re-run native grep just to prove a successful search hit exists.
- Do not read whole large files when a heading overview or line window is
  enough.
- Do not blindly choose rank 1 when the task expects an article/file-level
  answer and several top candidates are related.
- Do not stop after finding one correct-looking file if the prompt contains
  multiple entities, actions, or constraints that may require another file.

Follow the output format requested by the task prompt exactly.
