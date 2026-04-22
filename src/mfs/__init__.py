"""MFS - Semantic file search CLI powered by Milvus."""

import logging
import warnings

# Suppress milvus_lite's pkg_resources deprecation warning before any
# pymilvus / milvus_lite import happens downstream. Matching by message
# covers both the direct UserWarning and the underlying DeprecationWarning
# paths in different setuptools versions.
warnings.filterwarnings(
    "ignore",
    message=r".*pkg_resources is deprecated.*",
)
warnings.filterwarnings(
    "ignore",
    category=UserWarning,
    module=r"milvus_lite\.?.*",
)
warnings.filterwarnings(
    "ignore",
    category=DeprecationWarning,
    module=r"pkg_resources.*",
)

# Quiet pymilvus's stdlib logger. query_iterator emits a benign WARNING on
# every call against Milvus Lite ("failed to get mvccTs from milvus server,
# use client-side ts instead") because Milvus Lite has no server-side
# mvcc timestamp; the iterator transparently falls back to client-side ts.
# Real connection / query failures still surface as exceptions, so raising
# the threshold to ERROR keeps actual problems visible.
#
# pymilvus.settings runs init_log('WARNING') via dictConfig at import time,
# which would overwrite any setLevel call made before pymilvus is imported.
# Force the import first so our override sticks.
import pymilvus  # noqa: E402, F401

logging.getLogger("pymilvus").setLevel(logging.ERROR)

__version__ = "0.1.0"
