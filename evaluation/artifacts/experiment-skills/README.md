# Experiment Skills

These files are the prompts used to control tool access in the evaluation
runs. They are simplified evaluation versions of the public `skills/mfs/`
skill: shorter, easier to audit, and split by capability so each workflow can
be measured cleanly. They were not written as dataset-specific prompts; they
follow the same search, browse, and candidate-verification principles as the
current reusable skill.

## Code Search

The code-search evaluation used Claude Code in non-interactive mode. The
harness passed the selected skill text with `--append-system-prompt` and used
Claude Code tool restrictions to control which MFS commands were available.

| Public workflow | Experiment prompt |
| --- | --- |
| Agent shell tools | `code-search/A0_native_shell.md` |
| MFS search | `code-search/A1_mfs_search.md` |
| MFS browse | `code-search/A2_mfs_browse.md` |
| MFS search + MFS browse | `code-search/A3_mfs_search_and_browse.md` |

## Document Search

The document-search evaluation used Codex CLI in non-interactive JSON mode.
The harness prepended a small wrapper directory to `PATH` so the `mfs` command
only exposed the subcommands allowed for each workflow.

| Public workflow | Experiment prompt |
| --- | --- |
| Agent shell tools | `document-search/A0_native_shell.md` |
| Agent shell tools with strategy | `document-search/A0S_native_shell_with_strategy.md` |
| MFS search | `document-search/A1_mfs_search.md` |
| MFS browse | `document-search/A2_mfs_browse.md` |
| MFS search + MFS browse | `document-search/A3_mfs_search_and_browse.md` |

The `document-search-before-progressive-browse/` folder keeps the earlier A1
and A3 prompts from before the progressive browse instructions were tightened.
