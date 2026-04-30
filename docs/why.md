# Why MFS

Agent systems are becoming file-heavy. A useful agent no longer works only from
the current prompt; it often carries a local ecosystem of memories, skills,
transcripts, project notes, runbooks, and code.

Those files are valuable because they are ordinary files. Developers can edit
them, diff them, commit them, and move them between tools. But ordinary shell
search gives agents only two extremes:

- exact matching with `grep`, which misses paraphrased or conceptual requests
- broad reads with `cat`, which waste context and still miss nearby structure

MFS fills the layer between those extremes.

## The Agent File Problem

An agent working over a large workspace usually needs to answer questions like:

- Which memory file recorded the previous decision about a migration?
- Which SKILL reference explains when to use a tool?
- Which JSONL transcript contains the raw turn behind a summarized note?
- Which source file implements a behavior described in natural language?
- Which runbook or PDF mentions a policy without using the user's exact words?

The hard part is not that files are inaccessible. The hard part is that there
are too many of them, and the useful one may be named or worded differently from
the current query.

## The MFS Answer

MFS gives agents two complementary command families.

| Need | Commands | What the agent gets |
| --- | --- | --- |
| Locate candidates | `mfs search`, `mfs grep` | Fast global retrieval over indexed files, combining semantic and keyword signals. |
| Inspect context | `mfs ls`, `mfs tree`, `mfs cat` | Bounded, structured views of directories and files before reading exact lines. |

The two modes solve different problems:

- **Search is flat and global.** It finds where the answer might be across a
  corpus of memory, docs, code, and transcripts.
- **Browse is hierarchical and local.** It shows what sits around a result:
  neighboring files, headings, symbols, rows, keys, and line ranges.

An agent should not have to choose between "semantic RAG over chunks" and
"manually browsing the filesystem." MFS packages both paths into one CLI.

## Why This Matters for Memory and Skills

Memory systems often produce two kinds of files:

- curated Markdown summaries, rules, decisions, and project notes
- raw JSONL or transcript logs that preserve exact wording

These need different access patterns. Markdown summaries are good semantic
targets. Raw JSONL is often better for exact recovery, structure inspection, and
grep. MFS supports both: index the text-like files, grep the raw archives, and
use `mfs cat --peek/--skim` to inspect structure before expanding the exact
region.

Skill trees have a similar shape. `SKILL.md` files contain high-level
instructions, while references and examples contain details. An agent can search
for the likely skill, skim the surrounding reference directory, and then read the
exact lines it should follow.

## Why a CLI

A CLI is the lowest-friction integration surface for shell-based agents:

- no server lifecycle for the agent to manage
- no SDK import or framework lock-in
- JSON output when the caller is a program
- normal terminal output when the caller is a human
- no generated files inside the target project

For an agent, this is just another tool call:

```bash
mfs search "memory rollover rules" ~/.codex/memories --json
mfs tree --peek -L 2 ./skills
mfs cat -n 40:90 ./skills/mfs/SKILL.md
```

## Why Milvus

MFS needs more than a small local vector toy. It needs:

- dense vector retrieval for meaning
- sparse keyword retrieval for exact tokens
- metadata filters for path, account, content type, and line ranges
- a local mode for single-user workflows
- a managed mode for larger shared corpora

Milvus gives MFS those deployment shapes:

- **Milvus Lite** for zero-config local indexing
- **Milvus server** for self-hosted larger deployments
- **Zilliz Cloud** for managed infrastructure

The Milvus collection is still derived state. Files remain the durable source.
