# Code Search Benchmark

## Story

Code agents already have strong native tools. On straightforward queries with
literal docstrings or descriptive paths, native grep is hard to beat. The
interesting question is what happens when the query becomes conceptual:
the words in the request no longer line up exactly with identifiers, comments,
or filenames.

This benchmark tests that point. MFS helps most on hard paraphrase tasks, where
semantic search gives the agent a better first candidate and browse lets it
confirm context without falling back to long grep loops.

## Setup

| Item | Value |
| --- | --- |
| Corpus | CodeSearchNet Python subset |
| Size | 2,000 files |
| Tasks | 24 queries |
| Hardness | 8 easy, 8 medium, 8 hard |
| Agent | Claude Code |
| Model | Sonnet |
| Baseline | A0 native tools |
| MFS arms | A1 search-only, A3 search + browse |
| Result file | `results_summary.jsonl` |

Hardness was based on how much lexical help the query gives the agent:

- Easy: close to docstrings or obvious symbols.
- Medium: paraphrased but still contains useful domain terms.
- Hard: conceptual descriptions with weak literal anchors and more
  false-positive keyword matches.

## Headline

| Arm | Correct | Timeouts | pure_io | linear | with_read | Turns | Wall |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| A0 native | 22/24 | 1 | 962 | 10,386 | 89,842 | 5.8 | 28.8s |
| A1 search | 22/24 | 2 | 516 | 9,735 | 65,197 | 3.2 | 33.0s |
| A3 search + browse | 23/24 | 1 | 460 | 2,760 | 68,355 | 3.2 | 25.5s |

A3 had the best accuracy and the lowest pure I/O cost. Compared with native
A0, it reduced pure I/O by 52% and turns by 45%.

## By Difficulty

| Tier | A0 Correct | A1 Correct | A3 Correct | A0 pure_io | A1 pure_io | A3 pure_io |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Easy | 8/8 | 8/8 | 8/8 | 280 | 174 | 194 |
| Medium | 7/8 | 6/8 | 7/8 | 872 | 252 | 493 |
| Hard | 7/8 | 8/8 | 8/8 | 1,734 | 1,122 | 692 |

Hard queries are where MFS is clearest: A3 kept 8/8 accuracy while cutting
hard-tier pure I/O by 60% and turns by 54% versus A0.

## Selected Trace

See [q19 hard comparison](selected_transcripts/code-q19-hard-comparison.md).
The native baseline selected a plausible TIFF writer, while A3 found the
expected `torchvision.utils.save_image` implementation.

## Takeaway

For easy code search, native tools are already strong. For harder agent
navigation, MFS changes the workflow: the agent spends fewer turns exploring
keyword false positives and more often lands on the file that answers the
intent.
