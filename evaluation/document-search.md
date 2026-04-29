# Document Search Evaluation

## What We Tested

We asked Codex CLI to answer help-center retrieval questions. The corpus
contained 6,221 Wix Help Center articles from
[WixQA](https://huggingface.co/datasets/Wix/WixQA), indexed into 45,036 chunks.
Indexing the full corpus took 25 minutes and 28 seconds in the test
environment.

The run used commit `5187cf2` and Codex CLI with the GPT-5.5 Codex profile.
The main WixQA full-corpus runs were completed on April 28, 2026.

The questions were expert-written support questions. They looked like user
requests, not article titles.

Example:

```text
I received a manual payment from the pay button and I am unable to see the
payment history.
```

The hard part is choosing the article that actually answers the user's intent.
Many nearby articles share words like `payment`, `manual`, and `Pay Button`,
but only some explain payment history.

## What We Compared

| Workflow | Tools available |
| --- | --- |
| Agent shell tools | the agent's built-in Bash/shell command execution with standard tools such as `grep`, `find`, `sed`, `cat`, and direct file reads |
| Agent shell tools with strategy | agent shell tools plus explicit candidate-comparison guidance |
| MFS search | `mfs search` to locate articles, then normal reads |
| MFS browse | normal search plus MFS browse commands |
| MFS search + MFS browse | `mfs search` to locate candidates, then MFS browse commands to verify them |

The test had 40 questions:

- 30 questions expected one article.
- 10 questions expected multiple articles.

For each question, we checked:

- whether the agent found at least one expected article
- whether it found all expected articles
- how many commands and fresh I/O tokens it used

## Result

| Workflow | Found at least one | Found all required | Avg fresh I/O tokens | Avg commands | Avg wall time |
| --- | ---: | ---: | ---: | ---: | ---: |
| Agent shell tools | 27/40 | 20/40 | 53,951 | 7.2 | 47.2s |
| Agent shell tools with strategy | 28/40 | 22/40 | 65,094 | 8.1 | 54.5s |
| MFS search | 31/40 | 23/40 | 29,276 | 4.7 | 54.5s |
| MFS browse | 31/40 | 25/40 | 66,125 | 11.8 | 103.7s |
| MFS search + MFS browse | 31/40 | 25/40 | 43,170 | 6.5 | 87.2s |

The combined workflow reached the best full-answer score while using far fewer
commands and lower fresh I/O token cost than browse-heavy exploration.

![Document search baseline comparison](https://github.com/user-attachments/assets/e224455f-1a46-41c0-9143-d93946283322)

The combined MFS search + MFS browse workflow matched the best completeness
score and used lower average token cost than the agent-shell baseline.

Fresh I/O tokens are `input_tokens - cached_input_tokens + output_tokens` from
the Codex CLI event stream. Cached input is excluded because it can vary
heavily across repeated non-interactive runs, while fresh I/O better captures
the context and output the agent actually had to process. Reasoning tokens are
retained in the raw transcripts as a secondary metric; they do not change the
main comparison.

## Why This Matters

The native workflow often found a related article, but not always the article
that answered the full user request. MFS search improved the candidate set, and
MFS browse helped the agent compare nearby articles without reading every page
in full.

The remaining challenge is multi-article completeness. Some questions require
two documents, and the agent still has to recognize that one answer source is
not enough.

## Concrete Examples

- [Manual payment history](examples/document-payment-history.md): keyword
  search was pulled toward Pay Button and manual payment setup articles; MFS
  search surfaced the article that explains payment history.
- [Google Ads multi-article question](examples/document-google-ads.md): the
  question needed two articles, and the combined MFS workflow returned both.

## Takeaway

MFS search gives the agent a better candidate set. MFS browse makes it cheaper
to compare candidates before reading exact lines. Together, they are most useful
when documentation has many adjacent pages with overlapping vocabulary.
