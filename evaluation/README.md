# Evaluation

MFS is built around a simple workflow for agents:

1. Use search to find likely files.
2. Use browse to inspect just enough surrounding context.
3. Read exact line ranges before answering.

The evaluations here test whether that workflow helps in real end-to-end agent
runs. We tested two common situations:

- finding the right implementation in a large code folder, using Claude Code
- finding the right help-center article in a large documentation folder, using
  Codex CLI

The short version: MFS is most useful when the user's words do not exactly
match the file names, function names, or article titles. Plain shell tools are
still excellent for exact strings and small folders.

## What We Compared

These are the workflows we compared:

| Public name | What the agent could use |
| --- | --- |
| Agent shell tools | the agent's built-in Bash/shell command execution with standard tools such as `grep`, `find`, `sed`, `cat`, and direct file reads |
| Agent shell tools with strategy | agent shell tools plus explicit instructions to compare candidates carefully |
| MFS search | `mfs search` for locating candidates, then normal reads |
| MFS browse | normal search plus `mfs cat`, `mfs ls`, and `mfs tree` for compact inspection |
| MFS search + MFS browse | `mfs search` to locate candidates, then MFS browse commands to verify them |

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
| Code search | MFS search + MFS browse | Found 23/24 targets, with about 52% lower fresh I/O token cost than agent shell tools |
| Document search | MFS search + MFS browse | Matched the best full-answer score while using fewer commands and lower fresh I/O token cost than browse-heavy exploration |

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

Fresh I/O token cost is the unified headline cost metric. For Claude Code, it
is `input_tokens + output_tokens`. For Codex CLI, it is
`input_tokens - cached_input_tokens + output_tokens`. Cached input/cache-read
tokens are excluded because they mostly reflect provider-side cache reuse
across repeated non-interactive runs, not fresh corpus context the agent had
to consume.

The prompts under `evaluation/artifacts/experiment-skills/` are simplified
evaluation versions of the public `skills/mfs/` skill. They were kept short so
each workflow could isolate one capability during the experiment, but they
follow the same search, browse, and candidate-verification principles as the
user-facing skill.

## Read More

- [Code search evaluation](code-search.md)
- [Document search evaluation](document-search.md)
- [Code example: image-saving function](examples/code-image-save.md)
- [Document example: manual payment history](examples/document-payment-history.md)
- [Document example: multi-article Google Ads question](examples/document-google-ads.md)
- [Raw log publishing notes](raw-log-publishing.md)

Machine-readable summaries are kept under `evaluation/artifacts/`. Those files
preserve raw run labels for auditability; the public pages use descriptive
workflow names.
