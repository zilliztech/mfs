# Evaluation

MFS is built around a simple workflow for agents:

1. Use search to find likely files.
2. Use browse to inspect just enough surrounding context.
3. Read exact line ranges before answering.

The evaluations here test whether that workflow helps on realistic local-file
tasks. We tested two common situations:

- finding the right implementation in a large code folder
- finding the right help-center article in a large documentation folder

The short version: MFS is most useful when the user's words do not exactly
match the file names, function names, or article titles. Plain shell tools are
still excellent for exact strings and small folders.

## What We Compared

We avoided internal experiment labels in the public write-up. These are the
workflows we compared:

| Public name | What the agent could use |
| --- | --- |
| Native shell | normal shell search and file reads |
| Native shell with strategy | normal shell tools plus explicit instructions to compare candidates carefully |
| MFS search | `mfs search` for locating candidates, then normal reads |
| MFS browse | normal search plus `mfs cat`, `mfs ls`, and `mfs tree` for compact inspection |
| MFS search + browse | `mfs search` to locate and MFS browse commands to verify |

## Data Shape

The data was intentionally close to what agents see in real projects:

- Code files: a 2,000-file Python corpus sampled from CodeSearchNet.
- Documentation files: 6,221 Wix Help Center articles from WixQA.
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
| Code search | MFS search + browse | Found 23/24 targets, with about 52% less direct file-reading output than native shell |
| Document search | MFS search + browse | Matched the best full-answer score while using fewer commands and less input than browse-heavy exploration |
| Tool-level document retrieval | MFS search | Found relevant articles in top 10 for 32/40 questions, compared with 10/40 for native keyword search |

The strongest pattern is not "MFS replaces grep." It is:

- Use native tools for exact strings.
- Use MFS search when the request is conceptual or paraphrased.
- Use MFS browse when several candidates look similar and the agent needs a
  cheap way to compare them.

## Read More

- [Code search evaluation](code-search.md)
- [Document search evaluation](document-search.md)
- [Code example: image-saving function](examples/code-image-save.md)
- [Document example: manual payment history](examples/document-payment-history.md)
- [Document example: multi-article Google Ads question](examples/document-google-ads.md)
- [Raw log publishing notes](raw-log-publishing.md)

Machine-readable summaries are kept under `evaluation/artifacts/`.

