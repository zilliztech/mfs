"""ONNX embedding via onnxruntime (runs on CPU, no GPU required).

Default provider for MFS — no API key needed. Default model is the int8-
quantized BGE-M3 multilingual export.
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import suppress
from functools import partial
from pathlib import Path


def _infer_max_length(session, default: int = 8192) -> int:
    try:
        for inp in session.get_inputs():
            if inp.name != "input_ids":
                continue
            shape = getattr(inp, "shape", None) or []
            if len(shape) > 1 and isinstance(shape[1], int) and shape[1] > 0:
                return min(default, shape[1])
    except Exception:
        pass
    return default


def _tokenizer_config_max_length(path: str | None, default: int) -> int:
    if not path:
        return default
    try:
        value = json.loads(Path(path).read_text()).get("model_max_length")
    except Exception:
        return default
    if isinstance(value, int) and 0 < value < 1_000_000_000:
        return min(default, value)
    return default


class OnnxEmbedding:
    """ONNX Runtime embedding provider.

    Supports two ONNX model formats:
    - Models with ``dense_vecs`` output (e.g. gpahal/bge-m3-onnx-int8) — used directly
    - Models with ``last_hidden_state`` output — CLS pooling + L2 normalize applied
    """

    _DEFAULT_BATCH_SIZE = 32

    def __init__(
        self,
        model: str = "gpahal/bge-m3-onnx-int8",
        *,
        batch_size: int = 0,
    ) -> None:
        try:
            import onnxruntime as ort
        except ImportError as exc:
            raise ImportError(
                "ONNX embedding provider requires onnxruntime. "
                "Install with: uv sync (it is a core dep)."
            ) from exc

        from huggingface_hub import hf_hub_download, list_repo_files
        from tokenizers import Tokenizer

        # Cache model files under $MFS_HOME/onnx-cache so they survive container
        # restarts when /data is a volume.
        self._cache_dir = Path(os.environ.get("MFS_HOME") or (Path.home() / ".mfs")) / "onnx-cache"
        self._cache_dir.mkdir(parents=True, exist_ok=True)

        # Try offline first (local cache only, no network requests).
        # local_files_only=True skips HEAD calls — critical for sandboxed /
        # offline environments where DNS is blocked.
        tok_path, model_path, tok_cfg_path = self._download_model_files(
            model, hf_hub_download, list_repo_files
        )

        self._tokenizer = Tokenizer.from_file(tok_path)
        self._tokenizer.enable_padding(pad_id=1, pad_token="<pad>")

        self._session = ort.InferenceSession(model_path)
        self._input_names = [i.name for i in self._session.get_inputs()]
        self._output_names = [o.name for o in self._session.get_outputs()]
        max_length = _tokenizer_config_max_length(tok_cfg_path, _infer_max_length(self._session))
        self._tokenizer.enable_truncation(max_length=max_length)
        self._has_dense_vecs = "dense_vecs" in self._output_names
        self._model = model

        # Detect dimension from a probe embedding
        probe = self._encode(["hello"])
        self._dimension = len(probe[0])
        self._batch_size = batch_size if batch_size > 0 else self._DEFAULT_BATCH_SIZE

    def _download_model_files(self, model, hf_hub_download, list_repo_files):
        """Download tokenizer + ONNX model, preferring local cache (offline).

        Returns (tok_path, model_path). All hits go under self._cache_dir.
        """
        kw = {"cache_dir": str(self._cache_dir)}

        # --- Attempt 1: offline from cache (no network at all) ---
        try:
            tok_path = hf_hub_download(model, "tokenizer.json", local_files_only=True, **kw)
            # Try well-known ONNX filenames to avoid list_repo_files() network call
            model_path = None
            onnx_file = None
            for candidate in ("model_quantized.onnx", "model.onnx"):
                try:
                    model_path = hf_hub_download(model, candidate, local_files_only=True, **kw)
                    onnx_file = candidate
                    break
                except Exception:
                    continue
            if model_path is None:
                raise FileNotFoundError("No cached ONNX model found")
            tok_cfg_path = None
            with suppress(Exception):
                hf_hub_download(model, onnx_file + "_data", local_files_only=True, **kw)
            with suppress(Exception):
                tok_cfg_path = hf_hub_download(
                    model, "tokenizer_config.json", local_files_only=True, **kw
                )
            return tok_path, model_path, tok_cfg_path
        except Exception:
            pass

        # --- Attempt 2: online download (first run or cache evicted) ---
        tok_path = hf_hub_download(model, "tokenizer.json", **kw)
        tok_cfg_path = None
        with suppress(Exception):
            tok_cfg_path = hf_hub_download(model, "tokenizer_config.json", **kw)
        repo_files = list_repo_files(model)
        onnx_files = [f for f in repo_files if f.endswith(".onnx")]
        if not onnx_files:
            raise ValueError(f"No .onnx files found in {model}")
        # Prefer model_quantized.onnx > model.onnx > first .onnx file
        if "model_quantized.onnx" in onnx_files:
            onnx_file = "model_quantized.onnx"
        elif "model.onnx" in onnx_files:
            onnx_file = "model.onnx"
        else:
            onnx_file = onnx_files[0]
        # Also download external data file if present
        data_file = onnx_file + "_data"
        if data_file in repo_files:
            hf_hub_download(model, data_file, **kw)
        model_path = hf_hub_download(model, onnx_file, **kw)
        return tok_path, model_path, tok_cfg_path

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def dimension(self) -> int:
        return self._dimension

    async def embed(self, texts: list[str]) -> list[list[float]]:
        from .utils import batched_embed

        return await batched_embed(texts, self._embed_batch, self._batch_size)

    async def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, partial(self._encode, texts))

    def _encode(self, texts: list[str]) -> list[list[float]]:
        import numpy as np

        encoded = self._tokenizer.encode_batch(texts)
        input_ids = np.array([e.ids for e in encoded])
        attention_mask = np.array([e.attention_mask for e in encoded])
        feed = {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
        }
        if "token_type_ids" in self._input_names:
            feed["token_type_ids"] = np.zeros_like(input_ids)
        outputs = self._session.run(None, feed)

        if self._has_dense_vecs:
            # Model outputs pre-pooled dense vectors (e.g. bge-m3-onnx-int8)
            idx = self._output_names.index("dense_vecs")
            embeddings = outputs[idx]
        else:
            # Fall back to CLS pooling on last_hidden_state
            idx = self._output_names.index("last_hidden_state")
            embeddings = outputs[idx][:, 0, :]

        # L2 normalize
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        normalized = embeddings / norms
        return normalized.tolist()
