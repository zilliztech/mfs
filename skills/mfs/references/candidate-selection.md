# Candidate Selection

Search returns chunks. Many tasks require choosing the right file, article, or
section. Do not treat the top chunk as the whole answer when the task is
file-level or multi-part.

## Compare Distinct Files

When top results include repeated hits from the same file, collapse them
mentally into one candidate.

Compare candidates using:

- path and filename
- title or heading
- search snippet
- surrounding line range
- outline from `mfs cat --peek`
- short excerpts from `mfs cat --skim`

Useful pattern:

```bash
mfs search "<query>" <path> --top-k 20
mfs cat --peek -H 20 -D 3 <candidate-a>
mfs cat --peek -H 20 -D 3 <candidate-b>
mfs cat -n <start>:<end> <best-candidate>
```

## Prefer Main Topic Over Incidental Mentions

A broad overview can contain a paragraph that matches the query. A specific
task, troubleshooting, API, reference, or implementation file may be the better
answer if its main topic is the requested action.

Prefer the candidate whose title/path/headings directly match the task.

## Handle Adjacent Candidates

Adjacent candidates often share vocabulary:

- setup vs troubleshooting
- overview vs how-to
- API reference vs implementation
- desktop vs mobile
- old system vs migration target
- generic feature page vs product-specific page

When adjacent candidates appear, inspect at least two outlines before choosing.

## Multi-Part Prompts

Check for signs that more than one file is needed:

- the prompt asks about two entities or products
- there is a setup step plus a troubleshooting step
- a migration mentions source and target systems
- a policy/background answer and a procedure are both needed
- search results repeatedly point to two complementary files

If multiple files are clearly supported, return all required paths or use all
required evidence.

## Code-Specific Selection

For code, prefer candidates that contain the actual implementation or test
relevant to the task.

Strong signals:

- package/module path matches the request
- symbol name, class, function, or config key matches the intent
- surrounding code implements the behavior, not only mentions it
- tests show expected behavior when implementation is ambiguous

If search finds a related helper but not the owner module, inspect imports,
callers, or nearby module structure with native tools and `mfs cat --peek`.

## Document-Specific Selection

For documents, prefer candidates whose document-level purpose answers the user
question.

Strong signals:

- title directly names the task or issue
- headings contain the requested procedure or policy
- snippet answers the user's exact situation
- path or slug names the same product, platform, feature, or document area

Do not choose a document only because one paragraph has matching words if a
more specific document exists nearby.
