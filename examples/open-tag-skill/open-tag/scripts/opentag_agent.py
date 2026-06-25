#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def default_skill_dir() -> Path:
    return Path(__file__).resolve().parents[1]


def default_workdir() -> Path:
    return Path.cwd()


def default_memory_root() -> Path:
    return Path(os.getenv("OPENTAG_MEMORY_ROOT", str(Path.home() / ".mfs" / "opentag-memory")))


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def build_prompt(
    *,
    skill_dir: Path,
    workdir: Path,
    memory_root: Path,
    channel_id: str,
    question: str,
    thread_text: str,
    allowed_scopes: str,
) -> str:
    return f"""
You are being invoked by an Open Tag Slack bridge.

First read and follow the runtime instructions at:
{skill_dir / "references" / "runtime-agent.md"}

The user-facing setup skill is:
{skill_dir / "SKILL.md"}

Runtime context:
- Channel id: {channel_id}
- Workspace/repo root: {workdir}
- Memory root: {memory_root}
- Allowed MFS scopes: {allowed_scopes}
- MFS URL: {os.getenv("MFS_URL", "http://127.0.0.1:13619")}

Available helper scripts:
- {skill_dir / "scripts" / "mfs_search.py"}
- {skill_dir / "scripts" / "mfs_cat.py"}
- {skill_dir / "scripts" / "opentag_memory.py"}

User question:
{question}

Slack thread context:
{thread_text}

Return only the final Slack-ready answer.
Do not add a Sources section by default. Include citations only when the user
explicitly asks for sources/citations, or when a source-backed factual claim
needs provenance. For command execution tasks, report the command result and
stdout/stderr status; source citations are not needed.
""".strip()


def run_codex_once(
    prompt: str, *, skill_dir: Path, workdir: Path, memory_root: Path, timeout: int
) -> tuple[int, str]:
    memory_root.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("r", suffix=".txt", encoding="utf-8", delete=False) as f:
        output_path = Path(f.name)
    cmd = [
        "codex",
        "exec",
        "--dangerously-bypass-approvals-and-sandbox",
        "-c",
        "shell_environment_policy.inherit=all",
        "-C",
        str(workdir),
        "--add-dir",
        str(skill_dir),
        "--add-dir",
        str(memory_root),
        "--skip-git-repo-check",
        "--output-last-message",
        str(output_path),
        prompt,
    ]
    try:
        result = subprocess.run(
            cmd,
            check=False,
            timeout=timeout,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        if output_path.exists() and output_path.read_text(encoding="utf-8").strip():
            return result.returncode, output_path.read_text(encoding="utf-8").strip()
        return result.returncode, (result.stdout or "").strip()
    finally:
        try:
            output_path.unlink()
        except OSError:
            pass


def retryable_backend_failure(output: str) -> bool:
    lowered = output.lower()
    return "selected model is at capacity" in lowered or "rate limit" in lowered


def run_codex(
    prompt: str, *, skill_dir: Path, workdir: Path, memory_root: Path, timeout: int
) -> int:
    attempts = max(1, int(os.getenv("OPENTAG_BACKEND_ATTEMPTS", "3")))
    last_code = 1
    last_output = ""
    for attempt in range(1, attempts + 1):
        last_code, last_output = run_codex_once(
            prompt,
            skill_dir=skill_dir,
            workdir=workdir,
            memory_root=memory_root,
            timeout=timeout,
        )
        if last_code == 0:
            if last_output:
                print(last_output)
            return 0
        if not retryable_backend_failure(last_output) or attempt == attempts:
            break
        time.sleep(min(2 * attempt, 8))

    if last_output:
        print(last_output[-4000:].strip())
    return last_code


def run_claude(
    prompt: str, *, skill_dir: Path, workdir: Path, memory_root: Path, timeout: int
) -> int:
    memory_root.mkdir(parents=True, exist_ok=True)
    # Pass the prompt on stdin, not as a trailing positional: `claude --add-dir`
    # is variadic and would otherwise swallow the prompt as another directory.
    cmd = [
        "claude",
        "-p",
        "--dangerously-skip-permissions",
        "--add-dir",
        str(workdir),
        "--add-dir",
        str(skill_dir),
        "--add-dir",
        str(memory_root),
    ]
    result = subprocess.run(
        cmd,
        input=prompt,
        check=False,
        timeout=timeout,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if result.stdout:
        print(result.stdout.strip())
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Open Tag through a CLI agent backend.")
    parser.add_argument(
        "--backend",
        choices=["claude", "codex"],
        default=os.getenv("OPENTAG_BACKEND"),
    )
    parser.add_argument("--question", required=True)
    parser.add_argument("--channel-id", required=True)
    parser.add_argument("--thread-file", type=Path, required=True)
    parser.add_argument("--skill-dir", type=Path, default=default_skill_dir())
    parser.add_argument(
        "--workdir",
        type=Path,
        default=Path(os.getenv("OPENTAG_WORKDIR", default_workdir())),
    )
    parser.add_argument("--memory-root", type=Path, default=default_memory_root())
    parser.add_argument(
        "--timeout", type=int, default=int(os.getenv("OPENTAG_TIMEOUT_SECONDS", "420"))
    )
    args = parser.parse_args()
    if not args.backend:
        parser.error("--backend or OPENTAG_BACKEND is required")

    workdir = args.workdir.resolve()
    allowed_scopes = os.getenv("MFS_ALLOWED_SCOPES") or f"file://local{workdir}"
    os.environ["MFS_ALLOWED_SCOPES"] = allowed_scopes

    prompt = build_prompt(
        skill_dir=args.skill_dir.resolve(),
        workdir=workdir,
        memory_root=args.memory_root.expanduser().resolve(),
        channel_id=args.channel_id,
        question=args.question,
        thread_text=read_text(args.thread_file),
        allowed_scopes=allowed_scopes,
    )

    try:
        if args.backend == "codex":
            return run_codex(
                prompt,
                skill_dir=args.skill_dir.resolve(),
                workdir=args.workdir.resolve(),
                memory_root=args.memory_root.expanduser().resolve(),
                timeout=args.timeout,
            )
        return run_claude(
            prompt,
            skill_dir=args.skill_dir.resolve(),
            workdir=args.workdir.resolve(),
            memory_root=args.memory_root.expanduser().resolve(),
            timeout=args.timeout,
        )
    except subprocess.TimeoutExpired:
        print(f"Open Tag backend timed out after {args.timeout}s", file=sys.stderr)
        return 124


if __name__ == "__main__":
    raise SystemExit(main())
