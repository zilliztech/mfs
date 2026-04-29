# Example: Finding an Image-Saving Function

## User-Style Query

```text
persist a multi-dimensional numerical array as a rasterized image
```

Expected file:

```text
pytorch/vision/torchvision/utils.py
```

## Why It Is Hard

The query describes behavior instead of naming the implementation. It does not
say `save_image`, `torchvision`, or `utils.py`.

A native keyword workflow found a plausible but wrong file:

```text
neurodata/ndio/ndio/convert/tiff.py
```

That file sounded relevant because it dealt with arrays and raster image
formats, but it was not the expected implementation.

## What MFS Did Differently

The MFS search + MFS browse workflow used semantic search to find candidates whose
meaning matched the query, then used bounded browsing to verify the function
without reading unrelated files.

Outcome:

| Workflow | Predicted file | Correct | Fresh I/O tokens |
| --- | --- | ---: | ---: |
| Agent shell tools | `neurodata/ndio/ndio/convert/tiff.py` | no | 1,035 |
| MFS search | `pytorch/vision/torchvision/utils.py` | yes | 2,067 |
| MFS search + MFS browse | `pytorch/vision/torchvision/utils.py` | yes | 610 |

The combined workflow found the right file and used lower fresh I/O token cost
than both alternatives.
