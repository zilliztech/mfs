# Search and Browse

MFS is designed around a simple agent loop:

1. Search broadly for candidates.
2. Browse the candidate neighborhood.
3. Read exact content before answering or editing.

```bash
mfs search "where is connector auth configured" . --top-k 5
mfs ls ./server/python/src/mfs_server/connectors
mfs cat ./server/python/src/mfs_server/server/connector_schemas.py --range 1:120
```

## Search

Use search when the user's wording may not match exact filenames or tokens.
The server can combine semantic retrieval, keyword retrieval, and metadata
filters depending on the source and query.

## Grep

Use grep when the literal term matters:

```bash
mfs grep "MFS_API_TOKEN" .
```

For local code editing, `rg` is still the best first tool when you already know
the exact token. MFS grep matters most when the search spans indexed sources or
remote connectors.

## Browse

`ls`, `tree`, and `cat` are verification tools. They let an agent inspect
context without reading whole corpora into its prompt.
