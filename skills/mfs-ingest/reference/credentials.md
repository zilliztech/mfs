# Credential handling

The connector TOML files under `$MFS_HOME/connectors/` are stored on
disk, chmod 0600 by default. Plaintext secrets work, but:

- They survive on disk after the process exits.
- They show up in backups, container image layers (if baked in), and
  `tar` snapshots.
- They're easy to leak via `git add` mistakes if the directory is ever
  inside a repo.

Two indirection forms eliminate plaintext at the TOML layer.

## `env:VAR`

The string `env:NAME` is replaced with `os.environ["NAME"]` at plugin
init time (inside the server). If `NAME` isn't set when the server
constructs the connector, ingest fails fast with a clear error
("environment variable NAME is not set") rather than silently using the
literal string as the secret.

```toml
token = "env:SLACK_BOT_TOKEN"
dsn   = "env:PG_PROD_DSN"
api_token = "env:JIRA_TOKEN"
```

**Recommendation**: when the user already has the value exported in
their shell (very common for CLI users — they ran their own
`source .env` or have it in `~/.bashrc`), prefer this over pasting the
plaintext. Verify the variable IS set before writing the toml:

```bash
test -n "$SLACK_BOT_TOKEN" && echo set || echo "NOT set — export first"
```

The connector wizard inline-validates this at prompt time — if the user
types `env:NAME` and `NAME` isn't in the wizard's shell env, the wizard
re-prompts with the actual variable name in the error.

## `file:/path`

The string `file:/abs/path` is replaced with the file's contents
(stripped of trailing whitespace) at plugin init time.

```toml
credential_ref = "file:/run/secrets/snowflake-key.p8"
token = "file:/etc/mfs/secrets/slack.token"
```

**When to use**:
- K8s secret mounts (`/var/run/secrets/...`).
- Docker secrets (`/run/secrets/...`).
- File-based PEM keys that aren't shell-exportable (Snowflake's RSA
  private key, JWT signing keys).
- Multi-line tokens / keys (env vars can store them but quoting is
  awkward).

Path must be absolute. Relative paths are rejected.

## Plaintext (fallback)

Only when the user explicitly chooses it (demo / quick-test / shared
laptop where no env var or secret store exists). Confirm with the user:

> "Storing the token as plaintext in `$MFS_HOME/connectors/<alias>.toml`.
> The file is chmod 0600 but anyone with read access to your home
> directory can recover it. OK?"

## Which env vars common providers already export

Many users already have these exported via their CLI setup, IDE plugin,
or org-distributed `.env`. Check before asking for fresh credentials:

| Provider | Typical env var |
|---|---|
| OpenAI | `OPENAI_API_KEY` (read by openai SDK automatically — NEVER goes into a connector toml; only used for embedding/summary on the server side) |
| Anthropic | `ANTHROPIC_API_KEY` (server-side embedding/VLM, same as OpenAI) |
| Slack | `SLACK_BOT_TOKEN` or `SLACK_USER_TOKEN` |
| Discord | `DISCORD_BOT_TOKEN` |
| GitHub | `GITHUB_TOKEN` or `GH_TOKEN` |
| Jira / Atlassian | `ATLASSIAN_API_TOKEN`, `JIRA_TOKEN` |
| Notion | `NOTION_TOKEN` |
| Linear | `LINEAR_API_KEY` |
| HubSpot | `HUBSPOT_ACCESS_TOKEN` |
| Zendesk | `ZENDESK_API_TOKEN` + `ZENDESK_SUBDOMAIN` |
| Postgres | `PG_DSN`, `DATABASE_URL`, `POSTGRES_CONNECTION` (varies wildly) |
| Snowflake | `SNOWFLAKE_USER`, `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_PRIVATE_KEY_PATH` |
| BigQuery | `GOOGLE_APPLICATION_CREDENTIALS` (path to service-account JSON) |
| S3 / AWS | `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` (boto3 reads automatically; usually no need to put them in toml) |
| Feishu | `FEISHU_APP_ID` + `FEISHU_APP_SECRET` |

When the user mentions one of these providers, suggest checking the
listed env vars first.

## Why we don't have a "secret store" plugin

We considered abstracting "fetch from Vault / AWS Secrets Manager / GCP
Secret Manager" but kept things simple — `env:` covers the 80% case
because most secret stores already inject into the process env (CI,
K8s, docker), and `file:` covers the K8s-secret-mount case. Anything
more elaborate is one shell wrapper away:

```bash
export PG_PROD_DSN=$(aws secretsmanager get-secret-value --secret-id mfs/pg/prod --query SecretString --output text)
mfs add postgres://prod-db --config /tmp/pg-prod.toml
```

If a user really wants a Vault-aware connector, they can hand-roll an
init script around `mfs add`.
