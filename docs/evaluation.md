# Evaluation

This page summarizes the evidence currently available for MFS in search and
browse workloads. The evaluation is based on end-to-end agent runs: an agent
received a natural-language task, used the tools allowed by the harness, and
returned a target file or documentation article.

!!! caution "Read these numbers as agent-level evidence"
    These runs are not generic product guarantees. They measure specific
    agents, model profiles, prompts, corpora, task sets, timeouts, and token
    accounting rules. Use them to understand where MFS helped in these
    workloads, not as a promise that every corpus or agent will show the same
    result.

```text
User request
  -> agent workflow prompt
  -> shell tools, MFS search, and/or MFS browse
  -> final target file or article
  -> JSONL result summary and curated example trace
```

The full corpora and raw transcripts stay outside the documentation site. This
page links only to curated example pages and compact JSONL summaries in the
repository.

## Benchmark Shape

| Scenario | Corpus and tasks | Agent-level setup | Workflows compared |
| --- | --- | --- | --- |
| Code search | 2,000 Python files sampled from [CodeSearchNet](https://github.com/github/CodeSearchNet); 24 tasks split into 8 easy, 8 medium, and 8 hard queries. | Claude Code 2.1.119 with `claude-sonnet-4-6`; non-interactive `claude -p`; 180-second timeout per task. | Agent shell tools, MFS search, MFS search + MFS browse. |
| Document search | 6,221 Wix Help Center articles from [WixQA](https://huggingface.co/datasets/Wix/WixQA), indexed into 45,036 chunks; 40 questions, including 30 single-article and 10 multi-article tasks. | Codex CLI 0.125.0 with the GPT-5.5 Codex profile; non-interactive `codex exec --json`; 180-second timeout per task. | Agent shell tools, agent shell tools with strategy, MFS search, MFS browse, MFS search + MFS browse. |

The document-search index build for the full WixQA corpus took 25 minutes and
28 seconds in the test environment.

## Workflow Labels

| Public label | What the agent could use |
| --- | --- |
| Agent shell tools | The agent's built-in Bash or shell command execution with tools such as `grep`, `find`, `sed`, `cat`, and direct file reads. |
| Agent shell tools with strategy | The document-search shell baseline plus explicit candidate-comparison guidance. |
| MFS search | Agent shell tools plus `mfs search` for indexed candidate discovery. |
| MFS browse | Agent shell tools plus compact inspection commands such as `mfs cat`, `mfs ls`, and `mfs tree`. |
| MFS search + MFS browse | Agent shell tools plus indexed search for candidates and MFS browse commands for verification. |

Rows named `MFS search` or `MFS browse` do not mean the agent lost normal shell
tools. They mean the agent kept its shell tools and gained the listed MFS
capability.

Current copyable browse commands use the syntax documented in
[CLI](cli.md#browse-and-read) and [Search and Browse](search-and-browse.md):

```bash
mfs cat PATH --range A:B
mfs cat PATH --locator '{"lines":[A,B]}'
mfs head PATH -n N
mfs tail PATH --lines N
mfs cat PATH --peek
mfs cat PATH --skim
mfs ls PATH
mfs tree PATH -L N
```

The JSONL traces linked below are historical evidence from the recorded runs.
They are intentionally left unchanged, so some trace commands may show older
browse syntax. Use the README, example pages, and prompt files for current
commands to copy.

## Headline Results

### Code Search

Each code-search task expected one Python source file. Timed-out tasks counted
as misses. Token usage is `input_tokens + output_tokens` for the Claude Code
run.

| Workflow | Correct target files | Timeouts | Avg token usage | Avg wall time |
| --- | ---: | ---: | ---: | ---: |
| Agent shell tools | 22/24 | 1 | 962 | 28.8s |
| MFS search | 22/24 | 2 | 516 | 33.0s |
| MFS search + MFS browse | 23/24 | 1 | 460 | 25.5s |

The combined workflow found one more target file than the shell baseline while
using the lowest average token usage in this run.

### Hard Code Search

The hard subset used paraphrased queries with weak literal anchors and
plausible false positives.

| Workflow | Correct target files | Timeouts | Avg token usage |
| --- | ---: | ---: | ---: |
| Agent shell tools | 7/8 | 0 | 1,734 |
| MFS search | 8/8 | 0 | 1,122 |
| MFS search + MFS browse | 8/8 | 0 | 692 |

This is the clearest code-search value case: the query often described behavior
instead of naming the symbol, file, or package.

### Document Search

Document-search tasks asked the agent to identify one or more Wix Help Center
articles. Token usage is `input_tokens - cached_input_tokens + output_tokens`
from the Codex CLI event stream; reasoning tokens were retained as a secondary
metric in the artifact but are not the headline token column here.

| Workflow | Found at least one | Found all required | Timeouts | Avg token usage | Avg commands | Avg wall time |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Agent shell tools | 27/40 | 20/40 | 0 | 53,951 | 7.2 | 47.2s |
| Agent shell tools with strategy | 28/40 | 22/40 | 0 | 65,094 | 8.1 | 54.5s |
| MFS search | 31/40 | 23/40 | 0 | 29,276 | 4.7 | 54.5s |
| MFS browse | 31/40 | 25/40 | 0 | 66,125 | 11.8 | 103.7s |
| MFS search + MFS browse | 31/40 | 25/40 | 0 | 43,170 | 6.5 | 87.2s |

MFS search improved the first candidate set and used the lowest average token
usage. MFS search + MFS browse matched the best full-answer score while using
fewer commands and lower token usage than browse-heavy exploration.

### Retrieval-Only Document Results

The retrieval summary is not an agent final-answer test. It measures whether
the expected article appears in a ranked candidate set before the agent reads
and decides.

| Method | Questions | Hit@1 | Hit@5 | Hit@10 | All expected in top 10 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Native keyword | 40 | 1 | 4 | 10 | n/a |
| MFS top10 dedup | 40 | 14 | 28 | 32 | 27 |
| MFS top20 dedup | 40 | 14 | 31 | 36 | 34 |

The retrieval-only evidence explains why agent runs improved but also why the
agent still matters: a better candidate set does not guarantee that the final
answer includes every required article.

## Concrete Examples

| Example | What changed | Evidence |
| --- | --- | --- |
| Code image-save query | Shell tools selected `neurodata/ndio/ndio/convert/tiff.py`, a plausible image writer but not the expected file. MFS search + browse selected `pytorch/vision/torchvision/utils.py` with 610 tokens. | [Example page](https://github.com/zilliztech/mfs/blob/main/evaluation/code-search/examples/image-save.md), [shell trace](https://github.com/zilliztech/mfs/blob/main/evaluation/code-search/examples/image-save-shell-trace.jsonl), [MFS trace](https://github.com/zilliztech/mfs/blob/main/evaluation/code-search/examples/image-save-mfs-trace.jsonl). |
| Document email marketing pricing | Shell tools selected a monthly-balance article with 93,188 tokens. MFS search + browse returned both expected email-marketing pricing/campaign articles with 35,783 tokens. | [Example page](https://github.com/zilliztech/mfs/blob/main/evaluation/document-search/examples/email-marketing-pricing.md), [shell trace](https://github.com/zilliztech/mfs/blob/main/evaluation/document-search/examples/email-marketing-pricing-shell-trace.jsonl), [MFS trace](https://github.com/zilliztech/mfs/blob/main/evaluation/document-search/examples/email-marketing-pricing-mfs-trace.jsonl). |
| Document Bookings upgrade | Shell tools selected an article about adding Wix Bookings with 38,293 tokens. MFS search + browse selected the upgrade article that matched the plan-limit blocker with 23,288 tokens. | [Example page](https://github.com/zilliztech/mfs/blob/main/evaluation/document-search/examples/bookings-upgrade.md), [shell trace](https://github.com/zilliztech/mfs/blob/main/evaluation/document-search/examples/bookings-upgrade-shell-trace.jsonl), [MFS trace](https://github.com/zilliztech/mfs/blob/main/evaluation/document-search/examples/bookings-upgrade-mfs-trace.jsonl). |

## How To Interpret The Evidence

MFS helped most when the user's wording was conceptual or paraphrased, when
many nearby files or articles shared the same vocabulary, and when the agent
needed to compare several candidates before committing to an answer. In those
cases, indexed search made the candidate set better and browse made verification
cheaper.

Shell tools remain strong when the query contains exact strings, symbol names,
unique filenames, or when the corpus is small enough that indexing overhead is
not worth paying. The code-search easy subset is a useful reminder: shell tools
already found all 8 easy targets, while MFS mainly reduced token usage.

MFS browse is most useful after search has narrowed the field. In the
document-search run, browse-only exploration reached the best completeness score
but used 66,125 average tokens and 11.8 average commands. The combined workflow
kept the same full-answer score with lower token and command usage.

## Remaining Limits

- The evaluations cover one CodeSearchNet Python subset and one WixQA help
  center corpus. They do not prove results for every programming language,
  repository shape, document type, or task distribution.
- Multi-article document completeness remains hard. The document-search task
  set included 10 questions that expected two articles, and the best
  agent-level workflows found all required articles for 25 of 40 questions.
- Retrieval quality and final-answer quality are related but different. The
  MFS top20 retrieval summary put all expected articles in the top 10 for 34 of
  40 questions, while the best agent-level workflows completed 25 of 40.
- The document corpus had a measured index build cost. Repeated search and
  browse over the same corpus can amortize that cost, but one-off exact-string
  searches may still be better served by shell tools.
- The public evidence uses curated examples and compact summaries. Raw corpora
  and full transcripts are intentionally not embedded in this documentation
  page.

## Repository Evidence

The evaluation artifacts live outside the MkDocs `docs/` tree, so these links
open the repository evidence directly.

| Evidence | Repository path |
| --- | --- |
| Evaluation overview | [`evaluation/README.md`](https://github.com/zilliztech/mfs/blob/main/evaluation/README.md) |
| Code-search scenario README | [`evaluation/code-search/README.md`](https://github.com/zilliztech/mfs/blob/main/evaluation/code-search/README.md) |
| Code-search task manifest | [`evaluation/code-search/datasets/tasks.jsonl`](https://github.com/zilliztech/mfs/blob/main/evaluation/code-search/datasets/tasks.jsonl) |
| Code-search result summary | [`evaluation/code-search/artifacts/results_summary.jsonl`](https://github.com/zilliztech/mfs/blob/main/evaluation/code-search/artifacts/results_summary.jsonl) |
| Document-search scenario README | [`evaluation/document-search/README.md`](https://github.com/zilliztech/mfs/blob/main/evaluation/document-search/README.md) |
| Document-search task manifest | [`evaluation/document-search/datasets/tasks.jsonl`](https://github.com/zilliztech/mfs/blob/main/evaluation/document-search/datasets/tasks.jsonl) |
| Document-search result summary | [`evaluation/document-search/artifacts/results_summary.jsonl`](https://github.com/zilliztech/mfs/blob/main/evaluation/document-search/artifacts/results_summary.jsonl) |
| Document-search retrieval summary | [`evaluation/document-search/artifacts/retrieval_summary.jsonl`](https://github.com/zilliztech/mfs/blob/main/evaluation/document-search/artifacts/retrieval_summary.jsonl) |
