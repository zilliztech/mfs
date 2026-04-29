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

These are the workflows we compared:

| Public name | What the agent could use |
| --- | --- |
| Agent shell tools | the agent's built-in Bash/shell command execution with standard tools such as `grep`, `find`, `sed`, `cat`, and direct file reads |
| MFS search | `mfs search` for locating candidates, then normal reads |
| MFS browse | normal search plus `mfs cat`, `mfs ls`, and `mfs tree` for compact inspection |
| MFS search + MFS browse | `mfs search` to locate candidates, then MFS browse commands to verify them |

The document-search evaluation also included an agent-shell baseline with
extra candidate-comparison guidance. That check helps separate MFS's tool
value from prompt-strategy effects, but it is not the main product workflow.

## Data Shape

The data was intentionally close to what agents see in real projects:

- Code files: a 2,000-file Python corpus sampled from
  [CodeSearchNet](https://github.com/github/CodeSearchNet).
- Documentation files: 6,221 Wix Help Center articles from
  [WixQA](https://huggingface.co/datasets/Wix/WixQA).
- Questions: short natural-language requests, often paraphrased rather than
  copied from file names or headings.

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

## Results at a Glance

| Scenario | Best workflow | Result |
| --- | --- | --- |
| Code search | MFS search + MFS browse | Found 23/24 targets, with about 52% lower token usage than agent shell tools |
| Document search | MFS search + MFS browse | Matched the best full-answer score while using fewer commands and lower token usage than browse-heavy exploration |

![Code search baseline comparison](https://github.com/user-attachments/assets/da624f61-fccc-40b9-bc07-77d6bc416e57)

In the full code-search run, MFS search + MFS browse found one more target than
the agent-shell baseline and reduced average token usage by about 52%.

![Hard code search baseline comparison](https://github.com/user-attachments/assets/95ed7047-5c46-4f1a-aea7-97354d86252b)

On hard paraphrase queries, the combined workflow kept perfect accuracy and
used the lowest average token usage.

![Document search baseline comparison](https://github.com/user-attachments/assets/e224455f-1a46-41c0-9143-d93946283322)

In the documentation run, MFS search + MFS browse matched the best completeness
score while using lower average token usage than the agent-shell baseline.

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

The two agent-shell document workflows differ only in prompting: Agent shell
tools is the bare baseline, while Agent shell tools with strategy receives the
same kind of generic candidate-comparison guidance as the MFS workflows, but
with all MFS commands blocked.

Token usage is reported with a unified fresh I/O definition. For Claude Code,
it is `input_tokens + output_tokens`. For Codex CLI, it is
`input_tokens - cached_input_tokens + output_tokens`. Cached input/cache-read
tokens are excluded because they mostly reflect provider-side cache reuse
across repeated non-interactive runs, not fresh corpus context the agent had
to consume.

The prompts under `evaluation/artifacts/experiment-skills/` are short
evaluation variants of the public `skills/mfs/` skill. They follow the same
search, browse, and verification principles.

## Details

For the full setup, metrics, and per-scenario discussion, see the
[code search evaluation](code-search.md) and the
[document search evaluation](document-search.md).

For concrete examples of what changed inside an agent run, see the
[image-saving function example](examples/code-image-save.md), the
[manual payment history example](examples/document-payment-history.md), and
the [multi-article Google Ads example](examples/document-google-ads.md).

Machine-readable summaries are kept under `evaluation/artifacts/`. Raw log
publishing notes are in [raw-log-publishing.md](raw-log-publishing.md). The
artifact files preserve raw run labels for auditability; the public pages use
descriptive workflow names.
