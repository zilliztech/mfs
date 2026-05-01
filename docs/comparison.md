# How MFS Compares

MFS overlaps with semantic grep tools, context databases, and agent
filesystems, but it is intentionally narrower than all three. It keeps existing
files as the source of truth and adds a Milvus-backed search and browse layer
for agents.

## The Short Version

| Project | Main job | Source of truth | Best fit |
| --- | --- | --- | --- |
| MFS | Search and browse existing folders | User files, with Milvus as a derived index | Agents searching memory files, skills, docs, code, transcripts, and knowledge bases |
| [OpenViking](https://github.com/volcengine/OpenViking) | Context database for agents | OpenViking-managed context | Teams that want a managed context model for memory, resources, and skills |
| [mgrep](https://github.com/mixedbread-ai/mgrep) | Semantic grep CLI | Mixedbread-powered search index | Fast semantic candidate search from a query |
| [AgentFS](https://github.com/tursodatabase/agentfs) | Agent filesystem and state substrate | SQLite/Turso-backed agent filesystem | Agent-owned filesystem state, isolation, snapshots, and audit trails |

## Compared with OpenViking

OpenViking is closer to a full context database for agents. It is useful when a
team wants a managed context model rather than only a search layer over existing
files. MFS chooses a narrower integration point: files remain ordinary files,
and Milvus is a derived index over them, without requiring heavy file copying or
migration into a new context system. A key retrieval difference is that
summary-first search can miss fine-grained details, such as error codes, config
keys, function names, flags, IDs, and small decisions. MFS instead searches
original body chunks first, then uses progressive browse around the best
candidates to inspect nearby structure and context. This follows a familiar
human search pattern: search broadly, preview likely results, then read the
right neighborhood. Optional LLM summaries help with orientation, but they are
not the primary search surface.

## Compared with mgrep

mgrep is a focused semantic grep tool. It is useful when the main task is to
find likely matches quickly from a query. MFS covers that first step, but also
keeps the follow-up workflow in the same CLI: inspect directories, skim file
structure, compare nearby candidates, and read exact line ranges before
answering. The difference is less about search quality alone and more about the
full search-then-verify loop an agent usually needs.

## Compared with AgentFS

AgentFS is a real filesystem substrate for agent state, including mounting,
snapshots, and audit-oriented storage. It is useful when the goal is to manage
agent-owned state as a filesystem. MFS works at a different layer: it does not
mount or replace the user's filesystem. It keeps existing project and knowledge
folders intact, then adds retrieval and progressive browsing on top.

## Why This Shape

MFS chooses a small surface area:

- existing files stay readable, editable, and Git-friendly
- Milvus stores a rebuildable search index, not the canonical data
- search works over original body chunks instead of generated summaries alone
- browse commands expose structure without reading whole files
- the CLI and Skill fit agents that already know how to run shell commands

That shape makes MFS useful as infrastructure under memory systems, skill
managers, codebase agents, transcript search, and document search without asking
those systems to adopt a new storage model.
