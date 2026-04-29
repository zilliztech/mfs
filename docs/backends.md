# Milvus Backends

MFS can use Milvus Lite, self-hosted Milvus, or Zilliz Cloud.

## Milvus Lite

Milvus Lite is the default.

```toml
[milvus]
uri = "~/.mfs/milvus.db"
collection_name = "mfs_chunks"
```

Use it when:

- you want zero setup
- you index local folders for yourself
- one writer at a time is enough

Milvus Lite stores the database file under `~/.mfs/` by default.

## Self-hosted Milvus

```toml
[milvus]
uri = "http://localhost:19530"
collection_name = "mfs_chunks"
```

Use it when:

- the corpus is larger
- multiple processes or users need the same server
- you already operate Milvus locally or in a cluster

## Zilliz Cloud

```toml
[milvus]
uri = "https://your-endpoint.zillizcloud.com"
token = "..."
collection_name = "mfs_chunks"
```

Use it when:

- you want managed Milvus infrastructure
- the index should survive local machine cleanup
- several machines should share the same retrieval backend

The index is still derived from files. If the collection is removed, the
recovery path is to re-run `mfs add` on the source folders.

## Collection naming

The default collection name is `mfs_chunks`. Change it when you need isolated
indexes:

```bash
mfs config set milvus.collection_name my_project_chunks
```

`account_id` is stored on records and can be used to separate logical accounts
inside the same collection.
