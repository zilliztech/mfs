# A0 Baseline - Agent Shell Tools Only

Use the agent's built-in shell tools only. Do not use `mfs`.

Good native-tool choices:

- `rg` / `grep` for exact words, identifiers, error codes, product names, or
  distinctive phrases.
- `find` for file-name patterns.
- `sed`, `nl`, `awk`, or `cat` to inspect known files and line ranges.

For natural-language questions, start with a small set of distinctive query
terms, inspect candidate files, and then choose the file or line range that
best satisfies the task prompt.

Follow the output format requested by the task prompt exactly.
