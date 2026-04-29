# Memory and Transcript Search Examples

Use these patterns for memory logs, decision records, meeting notes, session
transcripts, and other chronological text.

## Recover a Prior Decision

Task:

```text
Find the prior decision about whether queue payloads should store raw chunks.
```

Workflow:

```bash
mfs search "decision whether queue payloads store raw chunks" <memory-root> --top-k 10
mfs cat --skim -H 12 -D 3 -W 160 <candidate-log>
mfs cat -n <start>:<end> <candidate-log>
```

Reasoning:

Memory questions usually need surrounding context, not only the matching
sentence. Use search to find the episode and line ranges to recover the actual
decision.

## Search by Exact Phrase or Identifier

Task:

```text
Find mentions of queue.json.
```

Workflow:

```bash
rg "queue\\.json" <memory-root>
```

If the memory folder includes mixed file formats:

```bash
mfs grep "queue\\.json" <memory-root>
```

Reasoning:

Use literal search for exact filenames, IDs, and quoted phrases.

## Timeline-Oriented Question

Task:

```text
When did we first discuss PDF support?
```

Workflow:

```bash
mfs search "first discussed PDF support" <memory-root> --top-k 20
mfs search "PDF support docx conversion cache" <memory-root> --top-k 20
mfs cat --peek -H 20 -D 3 <candidate-log>
mfs cat -n <start>:<end> <candidate-log>
```

Reasoning:

Chronological answers often need the earliest relevant log, not just the best
semantic match. Compare candidate dates or filenames when logs are dated.

## Summarize a Thread or Session

Task:

```text
Summarize what we decided about search and browse.
```

Workflow:

```bash
mfs search "decided about search and browse workflow" <transcript-root> --top-k 10
mfs cat --skim -H 12 -D 3 -W 180 <candidate-transcript>
mfs cat -n <start>:<end> <candidate-transcript>
```

Reasoning:

Use MFS to locate the relevant part of the transcript. Then summarize only
after reading the surrounding turns.
