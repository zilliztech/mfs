import numpy as np

from mfs_server.common.embeddings.onnx import (
    OnnxEmbedding,
    _infer_max_length,
    _tokenizer_config_max_length,
)


class _Encoded:
    ids = [101, 102, 0]
    attention_mask = [1, 1, 0]


class _Tokenizer:
    def encode_batch(self, texts):
        return [_Encoded() for _ in texts]


class _Session:
    def __init__(self):
        self.feed = None

    def run(self, _outputs, feed):
        self.feed = feed
        rows = len(feed["input_ids"])
        return [np.tile(np.array([[3.0, 4.0]], dtype=float), (rows, 1))]


class _Input:
    def __init__(self, name, shape):
        self.name = name
        self.shape = shape


class _ShapeSession:
    def __init__(self, shape):
        self._shape = shape

    def get_inputs(self):
        return [_Input("input_ids", self._shape)]


def _embedding_with_inputs(input_names):
    emb = object.__new__(OnnxEmbedding)
    emb._tokenizer = _Tokenizer()
    emb._session = _Session()
    emb._input_names = input_names
    emb._output_names = ["dense_vecs"]
    emb._has_dense_vecs = True
    return emb


def test_onnx_encode_supplies_token_type_ids_when_model_requires_it():
    emb = _embedding_with_inputs(["input_ids", "attention_mask", "token_type_ids"])

    out = emb._encode(["hello", "world"])

    feed = emb._session.feed
    assert "token_type_ids" in feed
    np.testing.assert_array_equal(feed["token_type_ids"], np.zeros_like(feed["input_ids"]))
    np.testing.assert_allclose(out, [[0.6, 0.8], [0.6, 0.8]])


def test_onnx_encode_omits_token_type_ids_when_model_does_not_accept_it():
    emb = _embedding_with_inputs(["input_ids", "attention_mask"])

    emb._encode(["hello"])

    assert "token_type_ids" not in emb._session.feed


def test_onnx_max_length_uses_fixed_session_sequence_length():
    assert _infer_max_length(_ShapeSession(["batch", 512])) == 512


def test_onnx_max_length_keeps_default_for_dynamic_sequence_length():
    assert _infer_max_length(_ShapeSession(["batch", "sequence"])) == 8192


def test_tokenizer_config_max_length_caps_default(tmp_path):
    cfg = tmp_path / "tokenizer_config.json"
    cfg.write_text('{"model_max_length": 512}')

    assert _tokenizer_config_max_length(str(cfg), 8192) == 512


def test_tokenizer_config_max_length_ignores_unbounded_sentinel(tmp_path):
    cfg = tmp_path / "tokenizer_config.json"
    cfg.write_text('{"model_max_length": 1000000000000000019884624838656}')

    assert _tokenizer_config_max_length(str(cfg), 8192) == 8192
