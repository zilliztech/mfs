# Why MFS

Agents increasingly work with large folders of files: code, Markdown notes,
JSONL transcripts, design docs, connector configs, and generated artifacts.
Plain shell tools are excellent when the agent already knows the exact token,
but weaker when the request is conceptual or spread across systems.

MFS keeps the original sources as the source of truth and adds a searchable
index on top:

- semantic search for concepts and paraphrases
- keyword and literal search for identifiers and errors
- bounded browse commands for verification before edits
- connector support for sources beyond the local filesystem

The product goal is not to replace the shell. It is to give agents a better
search and read surface before they decide what to inspect or change.
