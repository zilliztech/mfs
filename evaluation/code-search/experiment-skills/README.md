# Code Search Evaluation Prompts

These prompt files are compact evaluation variants of the public
`skills/mfs/` skill. They were used to isolate which command families were
available in each code-search workflow.

| File | Public workflow |
| --- | --- |
| `A0_native_shell.md` | Agent shell tools |
| `A1_mfs_search.md` | Agent shell tools plus MFS search |
| `A2_mfs_browse.md` | Agent shell tools plus MFS browse |
| `A3_mfs_search_and_browse.md` | Agent shell tools plus MFS search + MFS browse |

The prompts are intentionally shorter than the user-facing skill, but they
follow the same principles: locate candidates, inspect only enough context,
and verify the exact target before answering.
