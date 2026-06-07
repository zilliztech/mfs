from __future__ import annotations

from mfs_server.common.retrieval import _lit as retrieval_lit
from mfs_server.common.retrieval import build_filter
from mfs_server.config import ServerConfig
from mfs_server.storage.milvus import MilvusStore
from mfs_server.storage.milvus import _lit as storage_lit


def test_milvus_literal_escapes_control_characters() -> None:
    value = 'path\\with"quote\nand\ttab\rand\x01control'
    expected = 'path\\\\with\\"quote\\nand\\ttab\\rand\\u0001control'

    assert retrieval_lit(value) == expected
    assert storage_lit(value) == expected


def test_build_filter_escapes_special_path_segments() -> None:
    expr = build_filter(
        "default",
        connector_uri="file://client/root",
        object_prefix='file://client/root/name\nwith\ttab"quote',
    )

    assert "\n" not in expr
    assert "\t" not in expr
    assert 'name\\nwith\\ttab\\"quote' in expr
    assert 'connector_uri == "file://client/root"' in expr


def test_delete_by_object_escapes_object_uri_filter() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.filters: list[str] = []

        def has_collection(self, _name: str) -> bool:
            return True

        def delete(self, *, collection_name: str, filter: str) -> None:
            self.filters.append(filter)

    store = MilvusStore(ServerConfig())
    fake = FakeClient()
    store.client = fake

    store.delete_by_object(
        "default",
        "file://client/root",
        'file://client/root/name\nwith\ttab"quote',
    )

    assert len(fake.filters) == 1
    flt = fake.filters[0]
    assert "\n" not in flt
    assert "\t" not in flt
    assert 'object_uri == "file://client/root/name\\nwith\\ttab\\"quote"' in flt
