# Document Search Evaluation

## What We Tested

We tested MFS on a help-center retrieval task. The corpus contained 6,221 Wix
Help Center articles from WixQA, indexed into 45,036 chunks. Indexing the full
corpus took 25 minutes and 28 seconds in the test environment.

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
| Native shell | normal shell search and file reads |
| Native shell with strategy | normal shell tools plus explicit candidate-comparison guidance |
| MFS search | `mfs search` to locate articles, then normal reads |
| MFS browse | normal search plus MFS browse commands |
| MFS search + browse | `mfs search` to locate and MFS browse commands to verify |

The test had 40 questions:

- 30 questions expected one article.
- 10 questions expected multiple articles.

For each question, we checked:

- whether the agent found at least one expected article
- whether it found all expected articles
- how many commands and input tokens it used

## Result

| Workflow | Found at least one | Found all required | Avg effective input | Avg commands | Avg wall time |
| --- | ---: | ---: | ---: | ---: | ---: |
| Native shell | 27/40 | 20/40 | 52,809 | 7.2 | 47.2s |
| Native shell with strategy | 28/40 | 22/40 | 63,661 | 8.1 | 54.5s |
| MFS search | 31/40 | 23/40 | 28,240 | 4.7 | 54.5s |
| MFS browse | 31/40 | 25/40 | 63,958 | 11.8 | 103.7s |
| MFS search + browse | 31/40 | 25/40 | 41,734 | 6.5 | 87.2s |

The combined workflow reached the best full-answer score while using far fewer
commands and less input than browse-heavy exploration.

## Why This Matters

The tool-level retrieval gap was large:

| Retrieval method | Relevant article in top 10 |
| --- | ---: |
| Native keyword search | 10/40 |
| MFS article-level search | 32/40 |

With top 20 MFS results, at least one relevant article appeared for 36/40
questions. The remaining work is agent judgment: choosing the right article
among adjacent candidates and noticing when the question needs multiple
articles.

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

