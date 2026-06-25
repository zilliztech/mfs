# Memory And Optional Seed Notes

Open Tag's Memory is MFS retrieval from sources the operator has indexed and
authorized, such as Slack history, repositories, docs, issues, databases, object
stores, or web crawls.

The local helper is only a deterministic seed-note mechanism for demos. It
stores small Markdown files so a validation task can prove that extra context
outside the current Slack thread can be indexed by MFS and retrieved by the
backend. Do not present it as the product memory model.

Default root:

```text
~/.mfs/opentag-memory/
```

Per-channel file:

```text
~/.mfs/opentag-memory/slack/<channel-id>/memory.md
```

Recommended shape:

```markdown
# Open Tag Memory: <channel-id>

## Preferences
- Answers should be concise.

## Decisions
- 2026-06-24: Use the engineering repo as one permitted demo source.

## Facts
- The current Slack channel is isolated for Open Tag validation.
```

Rules:

- Prefer indexing real permitted sources through MFS for realistic context.
- Keep seed notes factual and small. Do not paste full Slack threads or private
  customer details.
- After writing seed notes, run `scripts/opentag_memory.py sync` or pass `--sync`
  to `remember` so MFS can search them later.
