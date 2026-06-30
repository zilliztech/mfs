# LangChain

Wrap MFS as a LangChain `Retriever` and you get a **single retriever over every
source you have indexed** — code, docs, Slack, Postgres, Jira, S3 — instead of one
vector store per folder. MFS owns ingest and hybrid (dense + BM25) search; the
adapter below is the whole integration.

## Install

Install `langchain-core` plus the MFS Python SDK (`mfs_sdk`, see [SDKs](../sdks.md)):

```bash
pip install langchain-core
# then install the checked-in MFS SDK from sdks/python (see the SDKs page)
```

Point the SDK at your running server (`mfs-server run` binds `127.0.0.1:13619`);
send a bearer token when the server has auth enabled.

## The retriever

```python
from typing import Any

import mfs_sdk
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever


class MFSRetriever(BaseRetriever):
    """Retrieve across MFS-indexed sources. Leave `scope` empty to search every
    connector, or set it to a path / URI prefix (e.g. "github://org/repo")."""

    api: Any
    scope: str = ""
    top_k: int = 8

    def _get_relevant_documents(self, query, *, run_manager=None):
        resp = self.api.search(q=query, path=self.scope or None, top_k=self.top_k)
        return [
            Document(
                page_content=hit.content,
                metadata={"source": hit.source, "score": hit.score, "locator": hit.locator},
            )
            for hit in resp.results
        ]


def mfs_retriever(base_url="http://127.0.0.1:13619", token=None, **kwargs):
    client = mfs_sdk.ApiClient(mfs_sdk.Configuration(host=base_url))
    if token:
        client.set_default_header("Authorization", f"Bearer {token}")
    return MFSRetriever(api=mfs_sdk.RetrievalApi(client), **kwargs)
```

Each MFS hit becomes a `Document`: the `content` snippet is the `page_content`,
and `source` / `score` / `locator` ride along in `metadata`. The `locator` lets
you reopen the exact unit later with `BrowseApi.cat` when a snippet is not enough.

## Use it

As a plain retriever:

```python
retriever = mfs_retriever(top_k=4)
docs = retriever.invoke("how do we handle clients that send too many requests")
for d in docs:
    print(d.metadata["source"], "→", d.page_content[:80])
```

In a RAG chain — drop it in anywhere a retriever is expected:

```python
from langchain.chains import create_retrieval_chain
from langchain.chains.combine_documents import create_stuff_documents_chain

chain = create_retrieval_chain(retriever, create_stuff_documents_chain(llm, prompt))
chain.invoke({"input": "summarize our rate-limiting behavior"})
```

Or hand it to an agent as a tool:

```python
from langchain_core.tools import create_retriever_tool

mfs_tool = create_retriever_tool(
    retriever, "search_mfs", "Search the team's code, docs, chat, and databases via MFS."
)
```

Scope the retriever per use — `mfs_retriever(scope="slack://team")` for chat,
`mfs_retriever(scope="github://org/repo")` for one repo, or leave it empty to fan
out across everything.
