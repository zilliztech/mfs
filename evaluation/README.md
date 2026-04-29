# Evaluation

We evaluated MFS in real end-to-end agent runs, not just isolated retrieval
calls. The goal was simple: test whether an agent can find the right file or
document more reliably, with less token usage, when it has indexed MFS search
and progressive browse tools available.

We tested two common situations:

- finding the right implementation in a large code folder, using Claude Code
- finding the right help-center article in a large documentation folder, using
  Codex CLI

MFS helps most when the user's words do not exactly match file names, function
names, or article titles. It also keeps search fast in large directories
because search runs against an index. Building that index has an upfront cost,
but it is a one-time cost for repeated agent work over the same corpus. Plain
shell tools are still excellent for exact strings and small folders.

## What We Compared

MFS has two complementary command families:

- **MFS search**: indexed semantic and keyword search, mainly through
  [`mfs search`](../docs/cli.md#mfs-search).
- **MFS browse**: compact file and directory inspection, mainly through
  [`mfs cat`](../docs/cli.md#mfs-cat), [`mfs ls`](../docs/cli.md#mfs-ls),
  and [`mfs tree`](../docs/cli.md#mfs-tree).

These are the workflows we compared:

| Public name | What the agent could use |
| --- | --- |
| Agent shell tools | the agent's built-in Bash/shell command execution with standard tools such as `grep`, `find`, `sed`, `cat`, and direct file reads |
| MFS search | agent shell tools plus `mfs search` for locating candidates |
| MFS browse | agent shell tools plus `mfs cat`, `mfs ls`, and `mfs tree` for compact inspection |
| MFS search + MFS browse | agent shell tools plus `mfs search` to locate candidates and MFS browse commands to verify them |

The result tables below use shorter labels such as `MFS search`, but those
rows still mean the agent kept its normal shell tools and gained the listed
MFS capability.

## Data Shape

The data was intentionally close to what agents see in real projects:

- Code files: a 2,000-file Python corpus sampled from
  [CodeSearchNet](https://github.com/github/CodeSearchNet).
- Documentation files: 6,221 Wix Help Center articles from
  [WixQA](https://huggingface.co/datasets/Wix/WixQA).
- Questions: short natural-language requests, often paraphrased rather than
  copied from file names or headings, which is closer to how people actually
  ask agents to search local projects and document sets.

Example code query:

```text
persist a multi-dimensional numerical array as a rasterized image
```

The expected answer was a source file containing an image-saving utility, but
the query did not say `save_image`, `torchvision`, or the exact file name.

Example documentation query:

```text
I received a manual payment from the pay button and I am unable to see the
payment history.
```

The expected answer was not just a generic Pay Button article. The agent had to
choose the article that explains where to see payment history.

## Results

### Code Search

| Workflow | Correct target files | Avg token usage |
| --- | ---: | ---: |
| Agent shell tools | 22/24 | 962 |
| MFS search | 22/24 | 516 |
| MFS search + MFS browse | 23/24 | 460 |

In the full code-search run, MFS search + MFS browse found one more target than
the agent-shell baseline and reduced average token usage by about 52%.

![Code search baseline comparison](https://github.com/user-attachments/assets/da624f61-fccc-40b9-bc07-77d6bc416e57)

### Hard Code Search

| Workflow | Correct target files | Avg token usage |
| --- | ---: | ---: |
| Agent shell tools | 7/8 | 1,734 |
| MFS search | 8/8 | 1,122 |
| MFS search + MFS browse | 8/8 | 692 |

On hard paraphrase queries, the combined workflow kept perfect accuracy and
used the lowest average token usage.

![Hard code search baseline comparison](https://github.com/user-attachments/assets/95ed7047-5c46-4f1a-aea7-97354d86252b)

### Document Search

| Workflow | Found all required articles | Avg token usage |
| --- | ---: | ---: |
| Agent shell tools | 20/40 | 53,951 |
| Agent shell tools with strategy | 22/40 | 65,094 |
| MFS search | 23/40 | 29,276 |
| MFS browse | 25/40 | 66,125 |
| MFS search + MFS browse | 25/40 | 43,170 |

The `Agent shell tools with strategy` row is a document-search control: it uses
the same shell tools as the baseline, but with extra candidate-comparison
guidance.

In the documentation run, MFS search + MFS browse matched the best completeness
score while using lower average token usage than the agent-shell baseline.

![Document search baseline comparison](https://github.com/user-attachments/assets/e224455f-1a46-41c0-9143-d93946283322)

The strongest pattern is not "MFS replaces grep." It is:

- Use agent shell tools for exact strings.
- Use MFS search when the request is conceptual or paraphrased.
- Use MFS browse when several candidates look similar and the agent needs a
  cheap way to compare them.

## How We Ran It

The code-search evaluation used Claude Code in non-interactive mode. Each task
was run with `claude -p`, the workflow prompt was injected with
`--append-system-prompt`, and tool restrictions controlled which MFS commands
were available.

The document-search evaluation used Codex CLI in non-interactive JSON mode.
Each task was run with `codex exec --json`, the prompt was sent on stdin, and
the harness parsed the JSONL event stream for final answers, command traces,
and token usage. Because Codex CLI did not expose the same fine-grained shell
tool restrictions, the harness controlled MFS access by placing a small `mfs`
wrapper script at the front of `PATH`.

Token usage is reported with a consistent fresh input/output definition so the
tables focus on context the agent actively consumed and produced. The exact
formula is documented in each scenario page.

The evaluation prompts in each scenario folder are short evaluation variants
of the public `skills/mfs/` skill. They follow the same search, browse, and
verification principles.

## Details

For the full setup, metrics, and per-scenario discussion, see the
[code search evaluation](code-search/) and the
[document search evaluation](document-search/).

For concrete examples of what changed inside an agent run, see the
[image-saving function example](code-search/examples/image-save.md), the
[manual payment history example](document-search/examples/payment-history.md),
and the
[multi-article Google Ads example](document-search/examples/google-ads.md).

Each scenario folder contains its own task manifest, run summaries, example
walkthroughs, and evaluation prompts:

```text
evaluation/
  code-search/
    artifacts/
    datasets/
    examples/
    experiment-skills/
  document-search/
    artifacts/
    datasets/
    examples/
    experiment-skills/
```

The task manifests list the exact queries and expected target files used in
the evaluation without redistributing source-code bodies or help-center article
bodies. The artifact files preserve raw run labels for auditability; the public
pages use descriptive workflow names.
