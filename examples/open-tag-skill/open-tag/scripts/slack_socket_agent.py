#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler


MENTION_RE = re.compile(r"<@[^>]+>")


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def skill_dir() -> Path:
    return Path(__file__).resolve().parents[1]


def default_workdir() -> Path:
    return Path(os.getenv("OPENTAG_WORKDIR", str(Path.cwd())))


def strip_mention(text: str) -> str:
    stripped = MENTION_RE.sub("", text).strip()
    return stripped or "Find the most relevant context for this thread and summarize it."


def build_thread_text(client: Any, channel: str, thread_ts: str) -> str:
    response = client.conversations_replies(channel=channel, ts=thread_ts, limit=30)
    lines = []
    for message in response.get("messages", []):
        user = message.get("user") or message.get("bot_id") or "unknown"
        text = message.get("text", "")
        lines.append(f"{user}: {text}")
    return "\n".join(lines)


def run_backend(backend: str, channel: str, question: str, thread_text: str, timeout: int) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".txt", encoding="utf-8", delete=False) as f:
        f.write(thread_text)
        thread_file = Path(f.name)

    cmd = [
        "python3",
        str(skill_dir() / "scripts" / "opentag_agent.py"),
        "--backend",
        backend,
        "--channel-id",
        channel,
        "--question",
        question,
        "--thread-file",
        str(thread_file),
        "--skill-dir",
        str(skill_dir()),
        "--workdir",
        str(default_workdir()),
        "--timeout",
        str(timeout),
    ]
    try:
        result = subprocess.run(
            cmd,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout + 10,
        )
        output = result.stdout.strip()
        if result.returncode != 0:
            return f"Open Tag backend failed with exit code {result.returncode}:\n```text\n{output[-3000:]}\n```"
        if len(output) > 12000:
            output = output[-12000:]
        return output or "Open Tag finished without output."
    finally:
        try:
            thread_file.unlink()
        except OSError:
            pass


def suggested_bot_name(backend: str) -> str:
    if os.getenv("OPENTAG_BOT_NAME"):
        return os.environ["OPENTAG_BOT_NAME"]
    return {"claude": "OpenClaude", "codex": "OpenCodex"}.get(backend, "OpenTag")


def print_live_summary(backend: str) -> None:
    bot = suggested_bot_name(backend)
    scopes = [s.strip() for s in os.getenv("MFS_ALLOWED_SCOPES", "").split(",") if s.strip()]
    channel = os.getenv("SLACK_CHANNEL_ID", "(not set)")
    invoke = {
        "claude": "claude -p --dangerously-skip-permissions",
        "codex": "codex exec --dangerously-bypass-approvals-and-sandbox",
    }[backend]

    print("=" * 64)
    print(f"Open Tag is live as @{bot}")
    print(f"  Brain   : {backend}  ->  {invoke}")
    print(f"  Memory  : {len(scopes)} permitted MFS scope(s):")
    for scope in scopes or ["(none — set MFS_ALLOWED_SCOPES)"]:
        print(f"            - {scope}")
    print(f"  Slack   : listening for @mentions in channel {channel}")
    print("")
    print("  Anyone who can @mention the bot in that channel can drive the")
    print("  backend, which runs with your shell and inherited environment.")
    print("  Slack text flows into the prompt, so treat every mention as")
    print("  untrusted input: use an isolated channel on a non-production host.")
    print(f"  Invite the bot only where it should respond: /invite @{bot}")
    print("=" * 64)


def create_app(backend: str, timeout: int) -> App:
    app = App(token=require_env("SLACK_BOT_TOKEN"))

    @app.event("app_mention")
    def handle_mention(event: dict[str, Any], client: Any, logger: Any) -> None:
        channel = event["channel"]
        thread_ts = event.get("thread_ts") or event["ts"]
        question = strip_mention(event.get("text", ""))

        status = client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=f"Open Tag is working on this with `{backend}`.",
        )

        try:
            thread_text = build_thread_text(client, channel, thread_ts)
            answer = run_backend(backend, channel, question, thread_text, timeout)
            client.chat_update(
                channel=channel,
                ts=status["ts"],
                text=answer,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Open Tag failed")
            answer = f"Open Tag failed: `{type(exc).__name__}: {exc}`"
            client.chat_update(channel=channel, ts=status["ts"], text=answer)

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Open Tag Slack Socket Mode bridge.")
    parser.add_argument(
        "--backend",
        choices=["claude", "codex"],
        default=os.getenv("OPENTAG_BACKEND"),
    )
    parser.add_argument(
        "--timeout", type=int, default=int(os.getenv("OPENTAG_TIMEOUT_SECONDS", "420"))
    )
    args = parser.parse_args()
    if not args.backend:
        parser.error("--backend or OPENTAG_BACKEND is required")

    app = create_app(args.backend, args.timeout)
    print_live_summary(args.backend)
    SocketModeHandler(app, require_env("SLACK_APP_TOKEN")).start()


if __name__ == "__main__":
    main()
