# Examples

The same loop fits every source: **add** it once, **search** across it, then
**reopen** the exact hit. The scenarios below show what that looks like in
practice. Each `mfs` command is also one plain-language request to an agent —
`/mfs-ingest` to add a source, `/mfs-find` to search and read (Codex uses
`$mfs-ingest` / `$mfs-find`).

For the URI shape and credentials of any connector, see its page under
[Connectors](connectors.md). First time configuring a source? Don't hand-write
the TOML — tell the **mfs-ingest** skill what you want and it figures out the
credentials and writes the config for you.

## Your agent's memory and skills

Past-session memory files (`.md`, `.jsonl`) and reusable skills become one
searchable namespace — the prompt you tuned last week or a decision logged three
sessions ago is one query away.

```bash
mfs add path/to/memory     # /mfs-ingest index my session memory
mfs add path/to/skills     # /mfs-ingest index my skills
mfs search "the prompt I tuned for refund disputes" --all
```

```text
file://local/.agents/memory/2026-05-31.jsonl  score=0.88
  {"role":"note","text":"refund-dispute prompt: lead with the order ID, then ..."}
file://local/.agents/skills/support-triage/SKILL.md  score=0.74
  ## Refund disputes — confirm the order ID first, then check the gateway log ...
```

Reopen the hit to get the full note: `mfs cat file://local/.agents/memory/2026-05-31.jsonl`.

## Your codebases

Index the repos your agent reads or writes and grep them by meaning — find the
helper by what it *does*, not the name you've forgotten.

```bash
mfs add path/to/repo
mfs search "where do we retry failed webhook deliveries?" path/to/repo
```

```text
file://local/repos/payments/webhooks/deliver.go  score=0.84
  87  // cap exponential backoff at 6 attempts, then dead-letter
  88  func (d *Dispatcher) retryDelivery(ev Event) error {
```

The hit carries a line range, so you reopen exactly those lines:
`mfs cat file://local/repos/payments/webhooks/deliver.go --range 80:110`.

## Documents, images, any format

PDFs, Word docs, Markdown, screenshots — MFS converts each to text **locally**
(PDF / docx → Markdown, no API key), and with a vision model enabled it describes
images too. One search spans every format.

```bash
mfs add path/to/docs
mfs add path/to/screenshots
mfs search "audit-log retention and the dashboards that show it" --all
```

```text
file://local/design-docs/data-governance.pdf  score=0.86
  ... Audit logs are retained for 400 days, then moved to cold storage ...
file://local/screenshots/grafana-2026-06-02.png  score=0.71
  A Grafana dashboard; the p99 latency panel climbs to ~800 ms around 14:10 ...
```

Note that structured text like `.csv` and `.json` is browseable and greppable but
not part of semantic search — see [the `file` connector](connectors/file.md#what-gets-indexed).

## Cloud drives and buckets

Mount a Google Drive or S3 bucket; its files become searchable text alongside
your local ones — no syncing, no downloads.

```bash
mfs add gdrive://my-drive --config ./gdrive.toml
mfs add s3://acme-exports --config ./s3.toml
mfs search "the Q3 board deck" --all
```

```text
gdrive://my-drive/Board/2026-Q3-review.pdf  score=0.87
  ... Q3 highlights: net revenue retention 118%, two enterprise logos closed ...
s3://acme-exports/finance/2026-q3-summary.csv  score=0.70
  quarter,net_revenue,nrr,churn  2026Q3,4.2M,1.18,1.4% ...
```

## Online sources

Crawl a docs site or mount a GitHub repo with its issues — remote content lands
in the same namespace as your local files.

```bash
mfs add web://docs.your-product.com
mfs add github://your-org/your-repo --config ./github.toml
mfs search "how do we rotate signing keys?" --all
```

```text
web://docs.your-product.com/security/key-rotation  score=0.88
  ... Signing keys rotate every 90 days; trigger an early rotation from the admin console ...
github://your-org/your-repo/_meta/issues.jsonl  score=0.75
  #312  "Automate signing-key rotation"  state=open  labels=[security]
```

## Team chat and tickets

Mount Slack, Gmail, Jira, Linear and pull the thread, the ticket, and the email
behind a decision into one answer.

```bash
mfs add slack://acme --config ./slack.toml
mfs add jira://acme  --config ./jira.toml
mfs search "why did we revert the burst guard?" --all
```

```text
slack://acme/channels/platform__C012345/messages.jsonl  score=0.90
  [Tue 09:40] @carol: reverting the burst guard — it dropped healthy traffic
  [Tue 09:42] @dave:  agreed, reopening PLAT-491 to re-tune the window
jira://acme/projects/PLAT/issues.jsonl  score=0.81
  PLAT-491  "rate-limit guard misfires under burst"  state=Reopened
```

A chat hit reopens the whole thread, and a ticket reopens by its key:
`mfs cat jira://acme/projects/PLAT/issues.jsonl --locator '{"key":"PLAT-491"}'`.

## Customers and support

Pull your CRM and help desk together — the account, its open tickets, and the
notes behind a customer issue in one query.

```bash
mfs add hubspot://acme  --config ./hubspot.toml
mfs add zendesk://acme  --config ./zendesk.toml
mfs search "why is Globex unhappy with onboarding?" --all
```

```text
zendesk://acme/tickets/records.jsonl  score=0.88
  #5821  "Onboarding blocked on SSO setup"  status=open  priority=high
hubspot://acme/companies/records.jsonl  score=0.74
  Globex — renewal at risk; onboarding friction flagged by the CSM ...
```

## Production data

Point MFS at Postgres, Mongo, or BigQuery and search rows as text — each row is a
file-like object, so `mfs cat` pulls back the full record.

```bash
mfs add postgres://prod --config ./pg.toml
mfs search "refunds stuck in pending over 7 days" postgres://prod/public/orders/rows.jsonl
```

```text
postgres://prod/public/orders/rows.jsonl  score=0.79
  {"id":"ord_8842","status":"pending","refund_requested_at":"2026-05-30", ...}
```

Reopen the full row by its key:
`mfs cat postgres://prod/public/orders/rows.jsonl --locator '{"id":"ord_8842"}'`.

## One query across everything

With several sources registered, `--all` fans one query across all of them —
files, databases, trackers, chat — in a single result shape, so any hit copies
straight into `mfs cat`.

```bash
mfs search "rate-limit guard misfires under burst" --all
```

```text
slack://acme/channels/oncall__C0A1B2/messages.jsonl  score=0.91
  [Mon 22:14] @alice: ratelimiter pegged 500ms p99 tail, dump attached
jira://acme/projects/PLAT/issues.jsonl  score=0.83
  PLAT-491  "rate-limit guard misfires under burst"  state=In Progress
file://local/repo/src/throttle.go  score=0.71
  42  func handleRateLimit(req Request) error {
```

The locators are uniform, so the same `mfs cat` reopens any of them — a line
range for the code file, a structured locator for the ticket.
