# Code Search Evaluation

## What We Tested

We asked an agent to find the one Python file that matched a natural-language
description. The corpus had 2,000 Python files from a CodeSearchNet subset.

The tasks were split into three levels:

| Level | What the query looked like |
| --- | --- |
| Easy | close to a docstring, symbol, or obvious file purpose |
| Medium | paraphrased, but still had useful domain words |
| Hard | conceptual, with weak literal anchors and plausible false positives |

Example hard query:

```text
persist a multi-dimensional numerical array as a rasterized image
```

The correct file was:

```text
pytorch/vision/torchvision/utils.py
```

That file contains the image-saving implementation. A pure keyword workflow can
be pulled toward other files that mention arrays and raster formats, such as
TIFF conversion utilities.

## What We Compared

| Workflow | Tools available |
| --- | --- |
| Native shell | shell search and normal file reads |
| MFS search | `mfs search`, then normal file reads |
| MFS search + browse | `mfs search`, then `mfs cat --peek/--skim/-n` |

Each run ended with one predicted source file. We measured whether the file was
correct, how many turns the agent took, and how much file-output context it
consumed.

## Result

| Workflow | Correct | Timeouts | Avg direct file-output tokens | Avg turns | Avg wall time |
| --- | ---: | ---: | ---: | ---: | ---: |
| Native shell | 22/24 | 1 | 962 | 5.8 | 28.8s |
| MFS search | 22/24 | 2 | 516 | 3.2 | 33.0s |
| MFS search + browse | 23/24 | 1 | 460 | 3.2 | 25.5s |

The combined workflow found one more target than native shell and used about
52% less direct file-output context. It also took fewer turns on average.

## Where MFS Helped

On easy queries, native shell was already strong. The difference became clearer
on hard paraphrase tasks:

| Hard-query workflow | Correct | Avg direct file-output tokens |
| --- | ---: | ---: |
| Native shell | 7/8 | 1,734 |
| MFS search | 8/8 | 1,122 |
| MFS search + browse | 8/8 | 692 |

MFS helped because semantic search gave the agent better first candidates, and
browse let it inspect only the relevant shape or line range instead of reading
large files.

## Concrete Example

See [Code example: image-saving function](examples/code-image-save.md).

In that case, native shell selected a plausible TIFF writer. The MFS search +
browse workflow found the expected `torchvision.utils.save_image`
implementation and verified the relevant line window.

