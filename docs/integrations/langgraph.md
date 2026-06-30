# LangGraph

In LangGraph, MFS is a **retrieval node**: one step in the graph fans a query out
across every indexed source and puts the results into state. It reuses the same
`MFSRetriever` from the [LangChain](langchain.md) page — define that first.

## Install

```bash
pip install langgraph langchain-core
# plus the MFS Python SDK (mfs_sdk) — see the SDKs page
```

## A retrieve → generate graph

```python
from typing import TypedDict

from langgraph.graph import START, END, StateGraph

retriever = mfs_retriever(top_k=4)  # from the LangChain page


class State(TypedDict):
    question: str
    context: str
    answer: str


def retrieve(state: State) -> State:
    hits = retriever.invoke(state["question"])
    return {"context": "\n\n".join(h.page_content for h in hits)}


def generate(state: State) -> State:
    # standard LangChain generation — swap in your LLM and prompt
    answer = llm.invoke(f"Answer using only this context:\n{state['context']}\n\nQ: {state['question']}")
    return {"answer": answer.content}


graph = StateGraph(State)
graph.add_node("retrieve", retrieve)
graph.add_node("generate", generate)
graph.add_edge(START, "retrieve")
graph.add_edge("retrieve", "generate")
graph.add_edge("generate", END)
app = graph.compile()

app.invoke({"question": "what happens when a client exceeds the rate limit?"})
```

The `retrieve` node is the only MFS-specific part — everything else is ordinary
LangGraph. Because MFS searches every connected source in one call, that single
node covers code, docs, chat, and databases at once; there is no per-source
retriever to wire up or keep in sync.

## As a tool instead of a fixed node

For an agentic graph, expose MFS as a tool the model calls when it decides it
needs context, rather than a mandatory step:

```python
from langchain_core.tools import create_retriever_tool

mfs_tool = create_retriever_tool(
    retriever, "search_mfs", "Search the team's code, docs, chat, and databases via MFS."
)
# bind mfs_tool to your model / ToolNode as usual
```

Scope a node or tool with `mfs_retriever(scope="…")` when a step should look at
one source, or leave it open to search everything.
