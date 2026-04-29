# Document Search Evaluation

## What We Tested

We asked Codex CLI to answer help-center retrieval questions. The corpus
contained 6,221 Wix Help Center articles from
[WixQA](https://huggingface.co/datasets/Wix/WixQA), indexed into 45,036 chunks.
Indexing the full corpus took 25 minutes and 28 seconds in the test
environment.

The questions were expert-written support questions. They looked like user
requests, not article titles.

Example:

```text
What is the pricing for email marketing on Wix?
```

The hard part is choosing the article that actually answers the user's intent.
Many nearby articles share words like `email marketing`, `plan`, `balance`,
and `campaign`, but only some explain pricing and upgrade flow.

## What We Compared

| Workflow | Tools available | Skill prompt |
| --- | --- | --- |
| Agent shell tools | the agent's built-in Bash/shell command execution with standard tools such as `grep`, `find`, `sed`, `cat`, and direct file reads | no extra skill; captured as [`A0_native_shell.md`](experiment-skills/A0_native_shell.md) |
| Agent shell tools with strategy | agent shell tools plus explicit candidate-comparison guidance | [`A0S_native_shell_with_strategy.md`](experiment-skills/A0S_native_shell_with_strategy.md) |
| MFS search | agent shell tools plus `mfs search` for locating candidate articles | [`A1_mfs_search.md`](experiment-skills/A1_mfs_search.md) |
| MFS browse | agent shell tools plus MFS browse commands for compact article and directory inspection | [`A2_mfs_browse.md`](experiment-skills/A2_mfs_browse.md) |
| MFS search + MFS browse | agent shell tools plus `mfs search` for locating candidates and MFS browse commands for verification | [`A3_mfs_search_and_browse.md`](experiment-skills/A3_mfs_search_and_browse.md) |

The result tables below use shorter labels such as `MFS browse`, but those
rows still mean the agent kept its normal shell tools and gained the listed
MFS capability.

The test had 40 questions:

- 30 questions expected one article.
- 10 questions expected multiple articles.

For each question, we checked:

- whether the agent found at least one expected article
- whether it found all expected articles
- how many commands it ran and how much token usage it incurred

## Result

| Workflow | Found at least one | Found all required | Avg token usage | Avg commands | Avg wall time |
| --- | ---: | ---: | ---: | ---: | ---: |
| Agent shell tools | 27/40 | 20/40 | 53,951 | 7.2 | **47.2s** |
| Agent shell tools with strategy | 28/40 | 22/40 | 65,094 | 8.1 | 54.5s |
| MFS search | **31/40** | 23/40 | **29,276** | **4.7** | 54.5s |
| MFS browse | **31/40** | **25/40** | 66,125 | 11.8 | 103.7s |
| MFS search + MFS browse | **31/40** | **25/40** | 43,170 | 6.5 | 87.2s |

The combined workflow reached the best full-answer score while using far fewer
commands and lower token usage than browse-heavy exploration.

![Document search baseline comparison](https://github.com/user-attachments/assets/e224455f-1a46-41c0-9143-d93946283322)

The combined MFS search + MFS browse workflow matched the best completeness
score and used lower average token cost than the agent-shell baseline.

## Why This Matters

The native workflow often found a related article, but not always the article
that answered the full user request. MFS search improved the candidate set, and
MFS browse helped the agent compare nearby articles without reading every page
in full.

The remaining challenge is multi-article completeness. Some questions require
two documents, and the agent still has to recognize that one answer source is
not enough.

## Concrete Examples

- [Email marketing pricing](examples/email-marketing-pricing.md): the
  shell-only run selected a monthly-balance article; MFS search + MFS browse
  returned both expected pricing/campaign articles while cutting token usage
  by about 62%.
- [Bookings upgrade](examples/bookings-upgrade.md): the shell-only run chose
  an article about adding Wix Bookings; MFS search + MFS browse chose the
  upgrade article that matched the user's plan-limit blocker.

## Takeaway

MFS search gives the agent a better candidate set. MFS browse makes it cheaper
to compare candidates before reading exact lines. Together, they are most useful
when documentation has many adjacent pages with overlapping vocabulary.

## Run Notes

The run used commit `5187cf2` and Codex CLI with the GPT-5.5 Codex profile.
The main WixQA full-corpus runs were completed on April 28, 2026.

The harness ran Codex CLI in non-interactive JSON mode with `codex exec
--json`. For A0S and MFS workflows, the skill prompt listed in the comparison
table above was prepended to the task prompt sent on stdin; A0 received only
the task prompt. The harness parsed the JSONL event stream for final answers,
command traces, and token usage, and controlled MFS access by placing a small
`mfs` wrapper at the front of `PATH`. This made the run an end-to-end agent
test rather than a standalone retrieval call.

Each task had a 180-second timeout; timed-out tasks would count as misses. Token
usage is `input_tokens - cached_input_tokens + output_tokens` from the Codex
CLI event stream. Cached input/cache-read tokens are excluded because they
mostly reflect provider-side cache reuse across repeated non-interactive runs,
not fresh corpus context the agent had to consume. Reasoning tokens are
retained as a secondary metric.
