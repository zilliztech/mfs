# Code Search Evaluation

## What We Tested

We asked Claude Code to find the one Python file that matched a
natural-language description. The corpus had 2,000 Python files sampled from
[CodeSearchNet](https://github.com/github/CodeSearchNet).

The tasks were split into three levels:

| Level | What the query looked like | Example query | Ground truth |
| --- | --- | --- | --- |
| Easy | close to a docstring, symbol, or obvious file purpose | `Return a topological sorting of nodes in a graph.` | `python/performance/performance/benchmarks/bm_mdp.py` |
| Medium | paraphrased, but still had useful domain words | `factory that produces a callable invoking a given shell command` | `llimllib/pub/pub/shortcuts/shortcuts.py` |
| Hard | conceptual, with weak literal anchors and plausible false positives | `persist a multi-dimensional numerical array as a rasterized image` | `pytorch/vision/torchvision/utils.py` |

## What We Compared

| Workflow | Tools available | Skill prompt |
| --- | --- | --- |
| Agent shell tools | the agent's built-in Bash/shell command execution with standard tools such as `grep`, `find`, `sed`, `cat`, and direct file reads | [`A0_native_shell.md`](experiment-skills/A0_native_shell.md) |
| MFS search | agent shell tools plus `mfs search` for locating candidates | [`A1_mfs_search.md`](experiment-skills/A1_mfs_search.md) |
| MFS search + MFS browse | agent shell tools plus `mfs search` for locating candidates and MFS browse commands such as `mfs cat --peek/--skim/-n` for compact inspection | [`A3_mfs_search_and_browse.md`](experiment-skills/A3_mfs_search_and_browse.md) |

The result tables below use shorter labels such as `MFS search`, but those
rows still mean the agent kept its normal shell tools and gained the listed
MFS capability.

Each run ended with one predicted source file. We measured whether the file was
correct, how many turns the agent took, and how much token usage it incurred.

## Result

| Workflow | Correct | Timeouts | Avg token usage | Avg turns | Avg wall time |
| --- | ---: | ---: | ---: | ---: | ---: |
| Agent shell tools | 22/24 | **1** | 962 | 5.8 | 28.8s |
| MFS search | 22/24 | 2 | 516 | **3.2** | 33.0s |
| MFS search + MFS browse | **23/24** | **1** | **460** | **3.2** | **25.5s** |

The combined workflow found one more target than agent shell tools and used
about 52% lower token usage. It also took fewer turns on average.

![Code search baseline comparison](https://github.com/user-attachments/assets/da624f61-fccc-40b9-bc07-77d6bc416e57)

The combined MFS search + MFS browse workflow improved the end-to-end result
while reducing average token cost versus the agent-shell baseline.

## Breakdown by Difficulty

On easy queries, agent shell tools were already strong. MFS mostly reduced
token usage without changing accuracy.

| Easy-query workflow | Correct | Avg token usage | Avg turns |
| --- | ---: | ---: | ---: |
| Agent shell tools | **8/8** | 280 | 2.5 |
| MFS search | **8/8** | **174** | **2.0** |
| MFS search + MFS browse | **8/8** | 194 | **2.0** |

On medium queries, the combined workflow matched the agent-shell baseline's
accuracy while using fewer tokens and fewer turns. Search-only was cheaper, but
missed more targets.

| Medium-query workflow | Correct | Avg token usage | Avg turns |
| --- | ---: | ---: | ---: |
| Agent shell tools | **7/8** | 872 | 5.6 |
| MFS search | 6/8 | **252** | **1.8** |
| MFS search + MFS browse | **7/8** | 493 | 3.5 |

On hard paraphrase queries, MFS search + MFS browse kept perfect accuracy and
used the lowest average token usage.

| Hard-query workflow | Correct | Avg token usage | Avg turns |
| --- | ---: | ---: | ---: |
| Agent shell tools | 7/8 | 1,734 | 9.2 |
| MFS search | **8/8** | 1,122 | 6.0 |
| MFS search + MFS browse | **8/8** | **692** | **4.2** |

![Hard code search baseline comparison](https://github.com/user-attachments/assets/95ed7047-5c46-4f1a-aea7-97354d86252b)

MFS helped because semantic search gave the agent better first candidates, and
browse let it inspect only the relevant shape or line range instead of reading
large files.

## Concrete Example

See [Code example: image-saving function](examples/image-save.md).

In that case, agent shell tools selected a plausible TIFF writer. The MFS
search + MFS browse workflow found the expected `torchvision.utils.save_image`
implementation and verified the relevant line window.

## Run Notes

The run used commit `afaf8c6`, Claude Code, and Claude Sonnet 4.6. The final
run window was April 24, 2026, 04:15:45 to 04:50:56 UTC.

The harness ran Claude Code in non-interactive mode with `claude -p`. Each
workflow's skill prompt, listed in the comparison table above, was injected
with `--append-system-prompt`; tool restrictions controlled which MFS commands
were available. This made the run an end-to-end agent test rather than a
standalone retrieval call.

Each task had a 180-second timeout; timed-out tasks count as misses. Token
usage is `input_tokens + output_tokens` for this Claude Code run. Claude Code
also reports cache creation and cache read tokens; those can dominate totals
while reflecting provider-side caching more than active search work, so the
public tables use the fresh input/output measure. The raw artifact keeps
`linear_tokens` and `with_read_tokens` as secondary metrics.
