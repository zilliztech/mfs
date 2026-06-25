# CLI Backends

Open Tag treats the backend as the Brain: a non-interactive CLI process that can
read the prompt, use the workspace, call MFS helpers, and return a Slack-ready
answer. Choose the backend explicitly for each deployment.

## Built-In Backend: Claude Print Mode

Use this backend when the operator has a working `claude` CLI session:

```bash
python scripts/opentag_agent.py \
  --backend claude \
  --question "Summarize this thread and list the next action." \
  --channel-id "$SLACK_CHANNEL_ID" \
  --thread-file /tmp/thread.txt \
  --workdir /path/to/repo
```

The runner invokes:

```bash
claude -p \
  --dangerously-skip-permissions \
  --add-dir <workdir> \
  --add-dir <skill-dir> \
  --add-dir <memory-root> \
  <prompt>
```

Availability depends on the operator's account and local CLI setup.

## Built-In Backend: Codex Exec

Use this backend when the operator has a working `codex` CLI session and wants
the runtime agent to inspect files, call MFS helpers, run tests, prepare code
changes, or execute explicitly requested workspace tasks.

```bash
python scripts/opentag_agent.py \
  --backend codex \
  --question "Where is the Slack connector implemented? Cite sources." \
  --channel-id "$SLACK_CHANNEL_ID" \
  --thread-file /tmp/thread.txt \
  --workdir /path/to/repo
```

The runner invokes:

```bash
codex exec \
  --dangerously-bypass-approvals-and-sandbox \
  -c shell_environment_policy.inherit=all \
  -C <workdir> \
  --add-dir <skill-dir> \
  --add-dir <memory-root> \
  --skip-git-repo-check \
  --output-last-message <tmp-output-file> \
  <prompt>
```

Use this only in a trusted workspace. It intentionally mirrors a high-autonomy
Slack agent demo, not a locked-down production deployment.

No `OPENAI_API_KEY` is required by this skill path. The backend uses the local
Codex CLI session and inherits the environment needed for MFS.

## Backend Selection

- Use the backend that is already authenticated and approved for the operator's
  workspace.
- Keep the Slack bridge thin: backend-specific behavior belongs in
  `opentag_agent.py`, not in Slack event handling.
