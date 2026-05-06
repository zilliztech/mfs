"""Local embedding via sentence-transformers (runs on CPU/GPU).

Requires: ``pip install 'mfs-cli[local]'`` or ``uv add 'mfs-cli[local]'``
No API key needed.
"""

from __future__ import annotations


def _detect_device() -> str:
    """Detect best available device: CUDA > MPS > CPU."""
    try:
        import torch
    except ImportError as exc:
        raise ImportError(
            "Local embedding provider requires sentence-transformers (which pulls in torch). "
            "Install with: pip install 'mfs-cli[local]' or: uv add 'mfs-cli[local]'"
        ) from exc

    if torch.cuda.is_available():
        return "cuda"
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


class LocalEmbedding:
    """sentence-transformers embedding provider."""

    _DEFAULT_BATCH_SIZE = 512

    def __init__(
        self,
        model: str = "all-MiniLM-L6-v2",
        *,
        batch_size: int = 0,
    ) -> None:
        import io
        import os
        import sys

        # Suppress noisy "Loading weights" tqdm bar and safetensors LOAD REPORT
        prev_tqdm = os.environ.get("TQDM_DISABLE")
        os.environ["TQDM_DISABLE"] = "1"
        old_stderr = sys.stderr
        try:
            sys.stderr = io.StringIO()
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise ImportError(
                    "Local embedding provider requires sentence-transformers. "
                    "Install with: pip install 'mfs-cli[local]' or: uv add 'mfs-cli[local]'"
                ) from exc

            self._st_model = SentenceTransformer(
                model, device=_detect_device(), trust_remote_code=True
            )
        finally:
            sys.stderr = old_stderr
            if prev_tqdm is None:
                os.environ.pop("TQDM_DISABLE", None)
            else:
                os.environ["TQDM_DISABLE"] = prev_tqdm
        self._model = model
        self._dimension = self._st_model.get_sentence_embedding_dimension() or 384
        self._batch_size = batch_size if batch_size > 0 else self._DEFAULT_BATCH_SIZE

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, texts: list[str]) -> list[list[float]]:
        from .utils import batched_embed

        return batched_embed(texts, self._embed_batch, self._batch_size)

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        embeddings = self._st_model.encode(texts, normalize_embeddings=True)
        return embeddings.tolist()
