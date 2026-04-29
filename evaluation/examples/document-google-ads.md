# Example: Multi-Article Google Ads Question

## User-Style Question

```text
I need help with our Google Ads campaign. I can't see our paid ad in the
campaign list and I'm wondering if it will appear after it's been reviewed.
Also, I want to know if the payment for our campaign will be automatically
deducted from our credit card or if we need to top up the credit.
```

Expected articles:

```text
articles/Google_Ads_with_Wix_Creating_Multiple_Campaigns__1d9d696fc7.md
articles/Google_Ads_with_Wix_FAQs_About_Your_Monthly_Subscription_Daily_Spend_and_Ad_Credits__7e10270b7e.md
```

## Why It Is Hard

The question asks two things:

- campaign visibility/review behavior
- billing, subscription, spend, and ad credit behavior

One article is not enough. The agent has to notice that the question contains
two targets and return companion articles.

## What MFS Did Differently

The combined MFS workflow used search to find the candidate set, then browse to
verify that two different articles covered the two parts of the question.

Outcome:

| Workflow | Found both articles | Notes |
| --- | ---: | --- |
| Native shell | no | usually found one relevant Google Ads article |
| MFS search | sometimes partial | better candidate set, but still required agent judgment |
| MFS search + browse | yes | returned both campaign and billing/credit articles |

This is the case where browse matters most: the agent must compare article
outlines and decide that the final answer needs more than one document.

