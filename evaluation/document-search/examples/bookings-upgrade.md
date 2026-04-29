# Example: Bookings Upgrade

## User-Style Question

```text
Potential clients are unable to book a tour on my website due to the current
plan limitations. I received an email stating that I need to upgrade to a Wix
Business & eCommerce Premium Plan to enable bookings.
```

Expected article:

```text
articles/Wix_Bookings_Upgrading_Your_Site__d44da9f634.md
```

## Outcome

| Workflow | Final answer | Correct? | Token usage | Commands |
| --- | --- | ---: | ---: | ---: |
| Agent shell tools | `Editor_X_Adding_Wix_Bookings__be2c674a3e.md` | no | 38,293 | 7 |
| MFS search + MFS browse | `Wix_Bookings_Upgrading_Your_Site__d44da9f634.md` | yes | 23,288 | 3 |

Baseline matched the setup phrase `Adding Wix Bookings`. That article is
nearby, but the user was not asking how to add Bookings; they were blocked by
plan limits and needed the upgrade article. MFS search surfaced the upgrade
intent directly and needed only two browse checks before answering.

## Why MFS Was Stronger

This question contains a misleading keyword path. `Bookings`, `Business &
eCommerce`, and `enable bookings` all appear in setup-oriented articles. The
real intent is the failure mode: clients cannot book because the current plan
does not support the feature.

MFS made that intent visible in the first search. The top candidates still
included the setup article, but browse let the agent compare it against the
upgrade article without reading many full files.

## Trace

Trace artifact: [bookings-upgrade-trace.jsonl](bookings-upgrade-trace.jsonl)

This is a curated, shortened trace. It removes absolute paths, long article
excerpts, and low-signal tool output; it is not the full raw transcript.

| Step | Workflow | Action | What happened | Why it mattered |
| ---: | --- | --- | --- | --- |
| 1 | Agent shell tools | grep | Keyword search found articles about adding Bookings, premium plans, and generic upgrade states. | The exact words pointed toward setup pages. |
| 2 | Agent shell tools | read | The agent inspected Bookings overview, adding Bookings, and generic upgrade articles. | The right topic was present, but the specific upgrade page was not selected. |
| 3 | Agent shell tools | final | It answered with the article about adding Wix Bookings. | That article explains setup, not the user's plan-limit block. |
| 4 | MFS search + MFS browse | search | `mfs search` returned the upgrade-specific Wix Bookings article alongside the setup article. | The correct intent appeared before a long manual file search. |
| 5 | MFS search + MFS browse | browse | `mfs cat --peek` checked both the setup article and the upgrade article. | The article outlines made the distinction obvious. |
| 6 | MFS search + MFS browse | final | It returned the Wix Bookings upgrade article. | The answer matched the user's actual blocker in 3 commands. |

This example is useful because the mistake is easy to see: setup is not the
same as upgrade. MFS kept both candidates visible, then browse made the intent
comparison cheap.
