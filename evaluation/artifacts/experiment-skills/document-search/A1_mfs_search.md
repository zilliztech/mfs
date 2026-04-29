---
name: mfs-search
description: Semantic search over indexed codebases and document collections.
---

# MFS Search Lobe

Use this skill when a task asks you to find a file, section, article, chunk,
or evidence passage in a large local corpus. MFS search is most useful when
the user's wording is conceptual or paraphrased and the target text may use
different words.

## Decision Tree

Start by classifying the sub-task:

- Natural-language concept, user question, behavior, policy, procedure,
  feature, or implementation intent -> use `mfs search`.
- Exact identifier, error code, literal title, unique phrase, URL, ticket ID,
  or line you already know -> use native `rg` / `grep`.
- Filename pattern -> use native `find`.
- Known file and only need to read bytes -> use native `sed` / `cat` in this
  arm; do not use MFS browse commands here.

## Search Patterns

Search the whole index when you do not know the file or document:

```bash
mfs search "<natural-language query>" --all --top-k 20
```

If the task gives a known directory or file scope, search inside that scope:

```bash
mfs search "<natural-language query>" <path> --top-k 20
```

For code, queries can describe a function, behavior, data flow, or component.
For documents, queries can describe a user question, policy issue, procedure,
or article topic.

## Candidate Selection and Verification

Treat MFS search as the first-stage locator. It finds candidate files,
articles, sections, or chunks; it is not always the final judge. Do not blindly
trust only rank 1. Inspect enough of the returned top-k results to choose the
result that best satisfies the task prompt.

Think at the file/article level, not only at the chunk level. If several hits
come from the same file, merge them mentally into one candidate. Compare
distinct candidate files by title, path, URL/slug when present, headings, and
the best matching snippets.

Prefer a result when:

- Its path/title/topic matches the requested scope.
- Its snippet directly addresses the question or concept.
- For code, the chunk contains the implementation or symbol location.
- For documents, the chunk belongs to the article/section that would answer
  the user question, not just a loosely related page.
- If one candidate is a broad overview and another is a specific task,
  procedure, troubleshooting page, or API reference for the requested action,
  verify the specific candidate before choosing the broad one.
- If the query contains a clear product, module, package, platform, language,
  framework, or document area, prefer candidates from the same context unless
  the content clearly says otherwise.

If several candidates are close, compare the top 3-5 distinct files and choose
the best supported one. Native tools may be used for exact verification or
reading known files, but do not use `mfs cat`, `mfs ls`, or `mfs tree` in this
arm.

For article/file-level document tasks, prefer the candidate whose title, URL,
headings, and overall article topic match the user request. A locally relevant
paragraph in a loosely related article is weaker than an article whose main
topic directly answers the question. Use native `sed`, `cat`, or `rg` to
inspect promising candidate files when the search snippet alone is not enough.

## Completeness Check

Before final output, check whether the prompt asks for more than one target.
Multiple targets are often implied by two entities, two actions, a setup plus a
troubleshooting step, migration from one thing to another, or an answer that
needs both a policy/background page and a procedure page.

If multiple files/articles are plausibly needed, return all clearly supported
paths requested by the prompt. It is better to include a small number of
well-supported complementary files than to stop after the first correct one.

Follow the output format requested by the task prompt exactly.
