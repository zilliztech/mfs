---
name: native-local-search
description: Native command-line search and verification over large local code or document collections.
---

# Native Local Search Skill

Use this skill when a task asks you to find a file, section, article, or
evidence passage in a large local corpus using only standard shell tools.

Do not use `mfs` commands in this arm. Use agent shell tools such as `find`, `rg`,
`grep`, `sed`, `head`, `tail`, `awk`, and `cat`.

## Decision Tree

Start by classifying the sub-task:

- Exact identifier, error code, literal title, unique phrase, URL, ticket ID,
  or line you already know -> use `rg` / `grep`.
- Filename or title pattern -> use `find`, `rg --files`, or path filtering.
- Natural-language question, behavior, policy, procedure, feature, or
  implementation intent -> extract likely entities, nouns, verbs, product
  names, platform names, and short phrases, then search those terms with `rg`.
- Known file and only need confirmation -> read a bounded section with
  `sed -n A,Bp`, `head`, `tail`, or `rg -n -C`.

## Search Patterns

For unknown targets, start broad but concrete:

```bash
rg -n -i "<important phrase|keyword|product name>" <corpus-root>
rg --files <corpus-root> | rg -i "<title|topic|product>"
```

When the prompt is paraphrased, run a few complementary searches instead of
one long query. Combine:

- Product, module, package, platform, language, or document area.
- The requested action or symptom.
- Distinct user-facing terms from the prompt.
- Known synonyms, but keep them conservative.

For large result sets, narrow with path/title filters or inspect only the most
promising files.

## Candidate Selection and Verification

Treat native search hits as first-stage locators. Do not blindly trust the
first match. Inspect enough of the top distinct candidate files to choose the
result that best satisfies the task prompt.

Think at the file/article level, not only at the line level. If several hits
come from the same file, merge them mentally into one candidate. Compare
distinct candidates by title, path, URL/slug when present, headings, and
nearby context.

Prefer a result when:

- Its path/title/topic matches the requested scope.
- The matching passage directly addresses the question or concept.
- For code, the file contains the implementation, symbol, config, or test
  relevant to the request.
- For documents, the article or section would answer the user question, not
  just mention a locally related phrase.
- If one candidate is a broad overview and another is a specific task,
  procedure, troubleshooting page, or reference page for the requested action,
  verify the specific candidate before choosing the broad one.
- If the query contains a clear product, module, package, platform, language,
  framework, or document area, prefer candidates from the same context unless
  the content clearly says otherwise.

Use bounded reads for verification:

```bash
rg -n -C 3 -i "<phrase>" <candidate-path>
sed -n '1,120p' <candidate-path>
sed -n 'A,Bp' <candidate-path>
```

Avoid reading whole large files unless a bounded read cannot answer the
comparison.

## Completeness Check

Before final output, check whether the prompt asks for more than one target.
Multiple targets are often implied by two entities, two actions, a setup plus a
troubleshooting step, migration from one thing to another, or an answer that
needs both a policy/background page and a procedure page.

If multiple files/articles are plausibly needed, return all clearly supported
paths requested by the prompt. It is better to include a small number of
well-supported complementary files than to stop after the first correct one.

Follow the output format requested by the task prompt exactly.
