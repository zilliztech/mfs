# Memory and Transcript Search Examples

Use these patterns for memory folders, decision records, session transcripts,
and other chronological project history.

Memory corpora are usually mixed:

- JSONL / NDJSON files: raw transcript or event streams, often one event per
  line.
- Markdown files: summarized memories, rules, decisions, and curated notes.
- Plain text logs: exports, debugging notes, and command transcripts.

Treat raw JSONL and summarized Markdown differently. JSONL is best for exact
source recovery. Markdown memories are often better for semantic discovery.

## Inspect a Raw JSONL Transcript

Task:

```text
Find the rough shape of a raw session transcript before searching inside it.
```

Workflow:

```bash
mfs cat --peek -H 20 -W 220 <session.jsonl>
mfs cat --skim -H 40 -W 240 <session.jsonl>
```

Reasoning:

For JSONL, MFS shows a line-level structural overview with source line numbers.
This is useful for seeing the event stream shape before drilling into exact
line ranges.

JSONL `-D` is accepted by the command-line interface but does not recursively
expand each JSON object today. Use `-H` to control how many events are shown
and `-W` to control how much of each event line is visible.

## Recover Exact Turns from JSONL

Task:

```text
Find the original turns where the user discussed queue payloads.
```

Workflow:

```bash
grep -R -n -E "queue payload|queue\\.json|raw chunks" <transcript-root>
mfs grep -i "queue payload|queue\\.json|raw chunks" <transcript-root>
mfs cat -n <start>:<end> <candidate-session.jsonl>
```

Reasoning:

Use literal search for exact filenames, IDs, JSON keys, and quoted phrases in
raw transcript files. After finding a hit, use `mfs cat -n` to recover the
surrounding event window. `mfs grep` is useful when the folder mixes indexed
Markdown memories with non-indexed JSONL transcript files.

## Search Summarized Markdown Memories

Task:

```text
Find the prior decision about whether queue payloads should store raw chunks.
```

Workflow:

```bash
mfs search "decision whether queue payloads store raw chunks" <memory-root> --top-k 10
mfs cat --skim -H 12 -D 3 -W 160 <candidate-memory.md>
mfs cat -n <start>:<end> <candidate-memory.md>
```

Reasoning:

Summarized Markdown memories often use different wording from the original
conversation. Semantic search is useful for finding the decision record, while
`mfs cat -n` verifies the exact passage before answering.

## Bridge Markdown Memory to Raw Transcript

Task:

```text
Recover the original conversation behind a summarized memory.
```

Workflow:

```bash
mfs search "PDF support docx conversion cache decision" <memory-root> --top-k 10
mfs cat --skim -H 12 -D 3 -W 180 <candidate-memory.md>
grep -R -n -E "PDF support|DOCX|conversion cache|pymupdf4llm|python-docx" <transcript-root>
mfs cat -n <start>:<end> <candidate-session.jsonl>
```

Reasoning:

Use the Markdown memory to identify the likely topic and vocabulary. Then use
literal search over raw JSONL transcripts to find the original turn window.
This avoids relying only on a summarized memory when exact wording matters.

## Timeline-Oriented Question

Task:

```text
When did we first discuss PDF support?
```

Workflow:

```bash
mfs search "first discussed PDF support" <memory-root> --top-k 20
mfs search "PDF support docx conversion cache" <memory-root> --top-k 20
mfs cat --peek -H 20 -D 3 <candidate-memory.md>
grep -R -n -E "PDF support|DOCX|pymupdf4llm|python-docx" <transcript-root>
mfs cat -n <start>:<end> <candidate-session.jsonl>
```

Reasoning:

Chronological answers often need the earliest relevant log, not just the best
semantic match. Compare candidate dates, filenames, and transcript line ranges
when logs are dated.

## Summarize a Thread or Session

Task:

```text
Summarize what we decided about search and browse.
```

Workflow:

```bash
mfs search "decided about search and browse workflow" <memory-root> --top-k 10
mfs cat --skim -H 12 -D 3 -W 180 <candidate-memory.md>
mfs cat --peek -H 30 -W 220 <candidate-session.jsonl>
mfs cat -n <start>:<end> <candidate-session.jsonl>
```

Reasoning:

Use MFS to locate the relevant memory or transcript. Then summarize only after
reading the surrounding turns. For JSONL transcript files, `--peek` and
`--skim` provide event-level orientation; line ranges provide the final source
window.

## JSON vs JSONL Browse Depth

Use `-D` for ordinary JSON files when you need deeper nested keys:

```bash
mfs cat --skim -D 3 -H 30 -W 160 <state.json>
```

For JSONL and NDJSON, `-D` is not a recursive object-depth control in the
current implementation. Prefer:

```bash
mfs cat --skim -H 40 -W 240 <session.jsonl>
grep -n '"type"\\|"role"\\|"content"\\|"tool"' <session.jsonl>
mfs cat -n <start>:<end> <session.jsonl>
```

This gives a practical transcript workflow: line-level overview, exact key or
phrase search, then a verified event window.
