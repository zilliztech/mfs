# Example: Manual Payment History

## User-Style Question

```text
I received a manual payment from the pay button and I am unable to see the
payment history.
```

Expected article:

```text
articles/Getting_an_Overview_of_Your_Payments__d487a621f0.md
```

## Why It Is Hard

The question includes several tempting keywords:

- `manual payment`
- `pay button`
- `payment history`

Native keyword search found related articles about setting up manual payments
and adding a Pay Button. Those were plausible, but they did not directly answer
where to see payment history.

## What MFS Did Differently

MFS search surfaced the payment overview article, and browse made it cheap to
inspect the surrounding article structure before answering.

Outcome:

| Workflow | Found expected article | Notes |
| --- | ---: | --- |
| Agent shell tools | no | selected Pay Button and manual payment setup articles |
| Agent shell tools with strategy | no | used fewer tokens but still chose adjacent articles |
| MFS search | yes | surfaced the payment overview article |
| MFS search + MFS browse | yes | verified the payment overview article with line-range reads |

This example shows the core MFS value: semantic search gets the agent closer to
the user's intent, and browse helps compare adjacent articles without reading
whole pages.
