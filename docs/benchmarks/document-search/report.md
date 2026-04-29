# Document Search Benchmark

## Story

Support documentation is different from code. The user's wording is often a
paraphrase of the article title, and multiple nearby articles can share the
same vocabulary. The hard part is not only finding a matching paragraph; it is
choosing the right article and, for multi-step questions, returning all
required articles.

This benchmark evaluates MFS on that workflow. Search is the locator. Browse is
the compact verifier: it lets the agent compare article outlines and inspect
small line windows instead of reading whole pages.

## Setup

| Item | Value |
| --- | --- |
| Dataset | WixQA |
| Corpus | 6,221 Wix Help Center articles |
| Indexed chunks | 45,036 |
| Index wall time | 25m 28s |
| Tasks | 40 expert-written questions |
| Hardness | 30 single-document, 10 multi-document |
| Agent | Codex CLI |
| Command profile | `codex --profile zilliz --yolo` |
| Result file | `results_summary.jsonl` |
| Retrieval file | `retrieval_summary.jsonl` |

The multi-document tasks are the harder slice. They require the agent to notice
that one article is not enough, then return companion articles that cover the
full user request.

## Arms

| Arm | Tools |
| --- | --- |
| A0 | Native commands, no extra retrieval strategy |
| A0S | Native commands plus generic local-search strategy |
| A1 v2 | MFS semantic search plus native reads |
| A2 | MFS browse plus native search |
| A3 v2 | MFS semantic search plus MFS browse |

## Headline

| Arm | hit_any | hit_all | Effective input | Commands | Wall |
| --- | ---: | ---: | ---: | ---: | ---: |
| A0 native | 27/40 | 20/40 | 52,809 | 7.2 | 47.2s |
| A0S native + strategy | 28/40 | 22/40 | 63,661 | 8.1 | 54.5s |
| A1 v2 search | 31/40 | 23/40 | 28,240 | 4.7 | 54.5s |
| A2 browse-heavy | 31/40 | 25/40 | 63,958 | 11.8 | 103.7s |
| A3 v2 search + browse | 31/40 | 25/40 | 41,734 | 6.5 | 87.2s |

Two comparisons matter:

- A0S shows that generic search strategy helps, but MFS search still improves
  accuracy while using less than half the effective input tokens.
- A3 v2 matches A2 on hit_all, but uses 35% fewer effective input tokens, 45%
  fewer commands, and less wall time.

## Single vs Multi-Document

| Arm | Single hit_all | Multi hit_any | Multi hit_all |
| --- | ---: | ---: | ---: |
| A0 | 19/30 | 8/10 | 1/10 |
| A0S | 20/30 | 8/10 | 2/10 |
| A1 v2 | 22/30 | 9/10 | 1/10 |
| A2 | 22/30 | 9/10 | 3/10 |
| A3 v2 | 22/30 | 9/10 | 3/10 |

The remaining weakness is multi-document completeness. Agents often find one
correct article but omit a companion article. Browse helps here, but the
decision still depends on the agent recognizing that the question has multiple
targets.

## Tool-Level Retrieval

| Method | Result |
| --- | ---: |
| Native keyword top10 | 10/40 |
| MFS article-level top10 deduped | 32/40 |
| MFS article-level top20 deduped | 36/40 |

The tool-level gap is large: MFS retrieval finds the relevant article much more
often than keyword search. Agent-level results are lower because the agent must
still choose among adjacent articles and decide whether multiple articles are
needed.

## Selected Traces

- [WixQA 0034](selected_transcripts/wixqa-0034-adjacent-article-selection.md):
  adjacent article selection around manual payments and payment history.
- [WixQA 0126](selected_transcripts/wixqa-0126-multi-doc-success.md):
  a multi-document Google Ads question where A3 v2 returns both required
  articles.

## Takeaway

MFS improves document retrieval in two ways. Search gives the agent a better
candidate set than native keyword matching, and browse makes verification
cheaper than reading whole articles. The combined A3 workflow reaches the best
hit_all score while using far fewer commands and tokens than a browse-heavy
baseline.
