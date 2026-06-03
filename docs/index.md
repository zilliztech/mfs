# MFS

MFS is a multi-source, file-like search layer for agents and developers.
It exposes codebases, local folders, object stores, databases, SaaS tools,
and knowledge bases through a familiar command surface: search, grep, ls,
tree, cat, and status.

The current v0.4 line is a client/server system:

- a Rust CLI named `mfs`
- a Python FastAPI server named `mfs-server`
- an OpenAPI protocol under `/v1`
- generated Python and TypeScript SDKs
- optional Rust acceleration for server hot paths

This documentation site is being migrated from the older pure-Python v0.3
docs. The first pass focuses on structure and current architecture; deeper
examples and connector-specific details will be expanded iteratively.

## Start here

- [Quickstart](getting-started.md) for the smallest local run.
- [Architecture](architecture.md) for the v0.4 client/server mental model.
- [Search and Browse](search-and-browse.md) for the core agent workflow.
- [HTTP API](api.md) and [SDKs](sdks.md) for programmatic clients.
