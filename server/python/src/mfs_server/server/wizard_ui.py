"""Shared UI helpers for mfs-server's interactive wizards (setup + connector add).

Built on rich (layout + colour) and questionary (arrow-key selects, validating
text input). Both packages are core deps so neither wizard has to detect
optional installs.

Conventions enforced here so both wizards feel the same:

- Section headers use a coloured rich Panel with a `Step N/M · <name>` title
  and an indented description block.
- Provider / backend choices use arrow-key `select()` with each option's
  trade-off shown inline (no need to remember which providers need extras).
- Required text input rejects empty input by re-prompting with an inline
  error — no silent acceptance of blanks that would surface as cryptic
  server-startup failures later.
- Integer prompts wrap the int() parse in a validator so 'abc' produces an
  inline 'please enter a number' instead of a Python traceback.
- Ctrl-C / EOF (questionary returns None) raises KeyboardInterrupt so callers
  can abort cleanly without writing a half-finished config.

All prompts go to stderr-via-console; stdout is reserved for explicit
artefacts (the path of the toml written, the API token banner).
"""

from __future__ import annotations

from typing import Callable, Iterable

import questionary
from questionary import Choice
from rich.console import Console
from rich.panel import Panel
from rich.text import Text


# Single console instance so wrapping / clearing behaves consistently.
console = Console()


# Brand palette — anchored on the logo teal #007674. The TUI uses the brighter
# variants so contrast holds on dark terminal backgrounds; #007674 itself is
# too dim there. Mirrors docs/stylesheets/terminal.css.
_BRAND_TEAL = "#26A69A"  # primary bright — qmark, question, pointer, border
_BRAND_AMBER = "#E89B3C"  # accent — selected/answer, emphasis
_BRAND_ERROR = "#E74C3C"  # semantic red — warn
_BRAND_MUTED = "#8B9BB4"  # muted — info / dim
_BRAND_DIM = "#a8a8a8"  # very dim — note


# Palette — kept restrained so it reads on both dark and light terminals.
_STYLE = questionary.Style(
    [
        ("qmark", f"fg:{_BRAND_TEAL} bold"),  # the "?" before each prompt
        ("question", f"fg:{_BRAND_TEAL} bold"),
        ("pointer", f"fg:{_BRAND_TEAL} bold"),
        ("highlighted", f"fg:{_BRAND_TEAL}"),
        ("selected", f"fg:{_BRAND_AMBER}"),
        ("answer", f"fg:{_BRAND_AMBER} bold"),
        ("instruction", f"fg:{_BRAND_MUTED} italic"),
    ]
)


def clear() -> None:
    """Clear the terminal — called once at wizard start to avoid stale tmux/
    shell content above the first prompt."""
    console.clear()


def banner(title: str, lines: list[str] | None = None) -> None:
    """Tiny top-of-wizard banner (one-line title + optional dim subtitle)."""
    console.print()
    console.print(Text(title, style=f"bold {_BRAND_TEAL}"))
    for ln in lines or []:
        console.print(Text(f"  {ln}", style=_BRAND_MUTED))
    console.print()


def section(
    title: str,
    body: str = "",
    *,
    step: int | None = None,
    total: int | None = None,
) -> None:
    """Render a section header in a rich Panel."""
    label = f"{title}"
    if step is not None and total is not None:
        label = f"Step {step}/{total} · {title}"
    txt = Text(body.strip(), style=_BRAND_DIM) if body else Text("")
    console.print()
    console.print(
        Panel(
            txt,
            title=f"[bold {_BRAND_TEAL}]{label}[/bold {_BRAND_TEAL}]",
            title_align="left",
            border_style=_BRAND_TEAL,
            padding=(1, 2) if body else (0, 2),
        )
    )


def select(
    label: str,
    choices: list[tuple[str, str]],
    *,
    default: str,
    instruction: str = "(↑↓ to move · Enter to confirm)",
) -> str:
    """Arrow-key single-select. choices = [(value, hint), ...]."""
    q_choices = [Choice(title=(f"{v}    {hint}" if hint else v), value=v) for v, hint in choices]
    answer = questionary.select(
        label,
        choices=q_choices,
        default=default if any(c.value == default for c in q_choices) else None,
        instruction=instruction,
        style=_STYLE,
        qmark="?",
    ).ask()
    if answer is None:
        raise KeyboardInterrupt
    return answer


def text(
    label: str,
    *,
    default: str = "",
    required: bool = False,
    secret: bool = False,
    validate: Callable[[str], str | None] | None = None,
    hint: str | None = None,
) -> str:
    """Free-form text input.

    required=True forbids empty input (re-prompts inline).
    secret=True hides the input as it is typed.
    validate: optional fn returning an error string when invalid, None when OK.
    """

    def _validator(v: str) -> bool | str:
        stripped = v.strip()
        if not stripped:
            if required:
                return "this field is required"
            return True  # blank means "use default"
        if validate is not None:
            err = validate(stripped)
            if err:
                return err
        return True

    fn = questionary.password if secret else questionary.text
    kwargs: dict = {
        "default": default,
        "validate": _validator,
        "style": _STYLE,
        "qmark": "?",
    }
    if hint:
        kwargs["instruction"] = f"({hint})"
    answer = fn(label, **kwargs).ask()
    if answer is None:
        raise KeyboardInterrupt
    return answer.strip()


def password(
    label: str, *, default: str = "", required: bool = False, hint: str | None = None
) -> str:
    return text(label, default=default, required=required, secret=True, hint=hint)


def confirm(label: str, *, default: bool = False) -> bool:
    answer = questionary.confirm(label, default=default, style=_STYLE, qmark="?").ask()
    if answer is None:
        raise KeyboardInterrupt
    return answer


def int_text(
    label: str, *, default: int, min_v: int = 1, max_v: int = 10_000_000, required: bool = False
) -> int:
    """Integer prompt — accepts blank when not required (keeps default)."""

    def _v(s: str) -> str | None:
        s = s.strip()
        if not s:
            return None if not required else "this field is required"
        try:
            n = int(s)
        except ValueError:
            return f"please enter a whole number (got {s!r})"
        if n < min_v or n > max_v:
            return f"out of range [{min_v:,}, {max_v:,}]"
        return None

    val = text(label, default=str(default), validate=_v)
    return int(val) if val else default


def info(text_: str, style: str | None = None) -> None:
    """One-line inline hint (italic dim by default)."""
    color = style or _BRAND_MUTED
    console.print(Text(f"  {text_}", style=f"italic {color}"))


def warn(text_: str) -> None:
    console.print(Text(f"  ! {text_}", style=f"bold {_BRAND_ERROR}"))


def emphasis(text_: str) -> None:
    console.print(Text(f"  {text_}", style=f"bold {_BRAND_AMBER}"))


def note(text_: str) -> None:
    console.print(Text(f"  {text_}", style=_BRAND_DIM))


def list_kv(items: Iterable[tuple[str, str]], indent: str = "    ") -> None:
    """Print a list of (label, value) pairs (for summary screens)."""
    for k, v in items:
        console.print(Text(indent) + Text(f"{k}: ", style=_BRAND_MUTED) + Text(v, style="bold"))
