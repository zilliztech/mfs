# Document Search Evaluation Prompts

These prompt files are compact evaluation variants of the public
`skills/mfs/` skill. They were used to isolate which command families were
available in each document-search workflow.

| File | Public workflow |
| --- | --- |
| `A0_native_shell.md` | Agent shell tools |
| `A0S_native_shell_with_strategy.md` | Agent shell tools with strategy |
| `A1_mfs_search.md` | MFS search |
| `A2_mfs_browse.md` | MFS browse |
| `A3_mfs_search_and_browse.md` | MFS search + MFS browse |

`A0S_native_shell_with_strategy.md` is a control prompt for separating prompt
strategy from tool value. The main product workflow remains MFS search and MFS
browse working together with the agent's normal shell tools.
