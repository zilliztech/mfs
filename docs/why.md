# Why MFS

Agents already know how to use a shell. They can run `grep`, `cat`, `find`, and
`ls`, but those tools only understand literal text and directory structure.
Modern workspaces contain notes, docs, transcripts, PDFs, source code, and
support files where the right wording is often unknown in advance.

MFS adds semantic retrieval without asking the user to move files into a new
system.

## The problem

An agent working in a large folder usually has two bad options:

- run literal search and miss paraphrased or conceptual matches
- read too many files and spend context on irrelevant text

This becomes worse for memory logs, support documents, design specs, and skill
trees. The files are useful precisely because they are human-readable, but the
agent still needs an index to find the right local region quickly.

## The MFS answer

MFS combines two command families:

| Need | Command family | What it gives the agent |
| --- | --- | --- |
| Find likely candidates | `mfs search`, `mfs grep` | flat retrieval over indexed files |
| Verify local context | `mfs ls`, `mfs tree`, `mfs cat` | structured views with bounded output |

Semantic search is not enough by itself. Browsing is not enough by itself. The
useful workflow alternates them.

## Why a CLI

A CLI is the lowest-friction integration point for agent tools:

- no server lifecycle to manage
- no client SDK to import
- no project files generated in the target repo
- JSON output for automation
- normal terminal output for humans

For an agent, `mfs search "..." . --json` is just another shell command. For a
developer, it is still usable directly at the terminal.

## Why Milvus

MFS needs more than a toy vector store. It needs dense retrieval, sparse keyword
retrieval, metadata filtering, and a path to local or managed deployment.

Milvus gives MFS three backend shapes:

- Milvus Lite for zero-config local use
- self-hosted Milvus for larger or shared environments
- Zilliz Cloud for managed infrastructure

The files remain the durable state. The Milvus collection can be rebuilt.
