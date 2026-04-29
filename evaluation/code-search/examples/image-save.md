# Example: Finding an Image-Saving Function

## User-Style Query

```text
persist a multi-dimensional numerical array as a rasterized image
```

Expected file:

```text
pytorch/vision/torchvision/utils.py
```

## Outcome

| Workflow | Predicted file | Correct? | Token usage | Turns |
| --- | --- | ---: | ---: | ---: |
| Agent shell tools | `neurodata/ndio/ndio/convert/tiff.py` | no | 1,035 | 5 |
| MFS search | `pytorch/vision/torchvision/utils.py` | yes | 2,067 | 8 |
| MFS search + MFS browse | `pytorch/vision/torchvision/utils.py` | yes | 610 | 5 |

The shell-only run found a real image writer, but it was the wrong kind of
answer. The expected target was the PyTorch/TorchVision utility that implements
`save_image`, not a TIFF conversion helper from another project. The combined
MFS workflow found the expected file and used the lowest token usage among the
successful runs.

## Why It Is Hard

The query describes behavior instead of naming the implementation. It does not
say `save_image`, `torchvision`, or `utils.py`.

A shell-only workflow found a plausible but wrong file:

```text
neurodata/ndio/ndio/convert/tiff.py
```

That file sounded relevant because it dealt with arrays and raster image
formats. This is exactly the trap: the words `array`, `raster`, and `image`
can point to many image-processing utilities, while the intended file is a
general TorchVision helper for saving tensors as images.

## What MFS Did Differently

The MFS search + MFS browse workflow used semantic search to find candidates whose
meaning matched the query, then used bounded browsing to verify the function
without reading unrelated files.

The important distinction is intent. Shell tools matched a nearby technical
domain: array-to-TIFF conversion. MFS found the implementation-level target:
a utility function that takes a tensor-like numerical array, builds an image
grid, converts it through PIL, and writes the rasterized result to disk.

## Why Browse Helped

Search alone already found the right file, but it used more token budget in
this run. Adding MFS browse made verification cheaper: the agent could inspect
just enough local context around the candidate function before answering,
instead of reading or comparing larger file regions.

This example shows the code-search value case: when a query describes behavior
instead of naming symbols, MFS can avoid a plausible but wrong keyword match and
then verify the target with less context.
