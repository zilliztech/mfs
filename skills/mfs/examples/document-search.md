# Document Search Examples

Use these patterns for local docs, help centers, specs, policies, and Markdown
knowledge bases.

## Answer a Natural-Language Question

Task:

```text
How do I publish the documentation site?
```

Workflow:

```bash
mfs search "publish documentation site" ./docs --top-k 10
mfs cat --peek -H 20 -D 3 <candidate-doc>
mfs cat -n <start>:<end> <candidate-doc>
```

Reasoning:

Documentation wording often differs from the user's question. Search locates
the article or page; browse verifies the heading and exact procedure.

## Compare Similar Articles

Task:

```text
Find the right article for a domain that is connected but the site is not live.
```

Workflow:

```bash
mfs search "domain connected but site not live" <docs-root> --top-k 20
mfs cat --peek -H 20 -D 3 <candidate-a>
mfs cat --peek -H 20 -D 3 <candidate-b>
mfs cat --skim -H 12 -D 3 -W 160 <best-candidate>
```

Reasoning:

When several titles share the same vocabulary, compare document-level purpose.
A specific troubleshooting page usually beats a broad overview if the user asks
about a concrete failure.

## Multi-Part Documentation Request

Task:

```text
Find docs for setting up a campaign and how billing works.
```

Workflow:

```bash
mfs search "set up campaign billing subscription daily spend credits" <docs-root> --top-k 20
mfs cat --peek -H 20 -D 3 <campaign-candidate>
mfs cat --peek -H 20 -D 3 <billing-candidate>
mfs cat -n <start>:<end> <campaign-candidate>
mfs cat -n <start>:<end> <billing-candidate>
```

Reasoning:

The prompt asks for two facts. It may require two pages. Do not stop after the
first correct-looking document.

## Exact Command in Docs

Task:

```text
Find where the docs mention mkdocs build --strict.
```

Workflow:

```bash
grep -R -n "mkdocs build --strict" ./docs
```

Or:

```bash
mfs grep "mkdocs build --strict" ./docs
```

Reasoning:

Exact commands and option names are literal search tasks.
