# Example: Email Marketing Pricing

## User-Style Question

```text
What is the pricing for email marketing on Wix?
```

Expected articles:

```text
articles/Email_Marketing_Upgrading_Your_Email_Marketing_Plan__de6e96935f.md
articles/Email_Marketing_Creating_a_Campaign_from_Start_to_Finish__1403e3db01.md
```

## Outcome

| Workflow | Final answer | Correct? | Token usage | Commands |
| --- | --- | ---: | ---: | ---: |
| Agent shell tools | `Email_Marketing_Understanding_Your_Monthly_Balance__352943a1ba.md` | no | 93,188 | 11 |
| MFS search + MFS browse | `Email_Marketing_Upgrading_Your_Email_Marketing_Plan__de6e96935f.md`<br>`Email_Marketing_Creating_a_Campaign_from_Start_to_Finish__1403e3db01.md` | yes | 35,783 | 10 |

Baseline followed the word `monthly` into a balance/quota article. That page
is related to email marketing usage, but it is not the pricing answer. MFS
search surfaced the upgrade-plan intent, then browse let the agent verify that
the final answer needed both the plan-upgrade article and the campaign-creation
article.

## Why MFS Was Stronger

The question is short and underspecified. It does not name a specific article,
plan, or UI page. In a large help-center corpus, exact keyword search found many
email-marketing pages and the agent spent most of its work comparing adjacent
but incomplete candidates.

MFS changed the first candidate set. Instead of only matching repeated words,
semantic search pulled in articles about free quotas, upgrades, and plan
selection. The browse step then made it cheap to inspect article outlines and
reject the monthly-balance page.

## Trace

Trace artifacts:

- [shell-only trace](email-marketing-pricing-shell-trace.jsonl)
- [MFS-enabled trace](email-marketing-pricing-mfs-trace.jsonl)

These are curated, shortened traces from two separate agent runs on the same
task. They remove absolute paths, long article excerpts, and low-signal tool
output; they are not the full raw transcripts.

### Shell-Only Run

| Step | Action | What happened | Why it mattered |
| ---: | --- | --- | --- |
| 1 | grep | Keyword search returned many email-marketing and plan-related files. | The candidate set was broad and noisy. |
| 2 | read | The agent inspected free-email, monthly-balance, upgrade-plan, and getting-started articles. | Several files were plausible, but only some answered pricing. |
| 3 | final | It selected the monthly-balance article. | This matched usage/quota language, not the user's pricing intent. |

### MFS-Enabled Run

| Step | Action | What happened | Why it mattered |
| ---: | --- | --- | --- |
| 1 | search | `mfs search` surfaced free quota and upgrade-plan candidates from the natural-language question. | The search started closer to the billing/plan intent. |
| 2 | browse | `mfs cat --peek` compared the free-quota, upgrade-plan, and monthly-balance article structures. | Browse made adjacent candidate comparison cheap. |
| 3 | search | A refined MFS query for plan pricing and free monthly emails surfaced the companion campaign article. | The agent recognized that the answer needed two articles. |
| 4 | final | It returned both expected articles. | The final answer covered both pricing/upgrade and campaign flow. |

This is the clearest document-search pattern in the run: MFS found the right
intent and cut token usage by about 62% at the same time.
