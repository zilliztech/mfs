"""Per-connector interactive-prompt schemas.

Each scheme maps to a list of ConnectorField rows the wizard walks through to
collect credentials and per-source options. Field definitions are deliberately
minimal — the wizard hits the "must-fill" set; fine-tuning (text_fields,
locator_fields, per-object filters, etc.) is left to direct TOML editing or a
follow-up `mfs add <uri> --config ...`.

The schemas mirror the `_cfg()` keys actually consumed by each plugin under
mfs_server.connectors.<scheme>.plugin. Stay in sync when a plugin grows a
new field.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ConnectorField:
    """One prompt in the connector wizard."""

    # toml key the value is written under
    name: str
    # human-facing prompt label (shown before [default])
    label: str
    # hide echo + tag as "(input hidden)"
    secret: bool = False
    # accept a comma-separated list; value is split and trimmed
    multi: bool = False
    # str | int | bool — the wizard parses accordingly
    type: str = "str"
    # default value shown in brackets; empty = no default
    default: str = ""
    # required fields can't be left empty (re-prompts); non-required allow blank
    required: bool = True
    # optional one-line hint printed under the label
    help: str = ""


@dataclass
class ConnectorSchema:
    scheme: str
    summary: str  # one-liner shown when the wizard starts (sets context)
    uri_hint: str  # example URI pattern shown if the user didn't pass one
    fields: list[ConnectorField] = field(default_factory=list)
    # When set, the wizard derives extra TOML sections automatically. E.g.
    # postgres needs an [[objects]] match block to declare text_fields; that
    # lives outside the simple field list so the schema stays declarative.
    extras_hint: str = ""


SCHEMAS: dict[str, ConnectorSchema] = {
    "web": ConnectorSchema(
        scheme="web",
        summary="Crawl HTTP(S) pages and index converted markdown.",
        uri_hint="web://my-docs",
        fields=[
            ConnectorField(
                "start_urls",
                "Start URL(s)",
                multi=True,
                help="The wizard accepts a comma-separated list.",
            ),
            ConnectorField(
                "allowed_domains", "Allowed domains (limit crawl scope)", multi=True, required=False
            ),
            ConnectorField("max_pages", "Max pages", type="int", default="100", required=False),
        ],
    ),
    "s3": ConnectorSchema(
        scheme="s3",
        summary="Index objects under an S3 / R2 / GCS / MinIO bucket prefix.",
        uri_hint="s3://my-bucket",
        fields=[
            ConnectorField("bucket", "Bucket name"),
            ConnectorField("prefix", "Key prefix (empty = whole bucket)", required=False),
            ConnectorField("region", "Region", default="us-east-1", required=False),
            ConnectorField(
                "endpoint_url", "Endpoint URL (empty = AWS; set for R2/GCS/MinIO)", required=False
            ),
            ConnectorField("access_key_id", "Access key ID", secret=True),
            ConnectorField("secret_access_key", "Secret access key", secret=True),
        ],
    ),
    "gdrive": ConnectorSchema(
        scheme="gdrive",
        summary="Google Drive folders / files (requires OAuth credentials).",
        uri_hint="gdrive://my-drive",
        fields=[
            ConnectorField(
                "token",
                "OAuth credentials path or env:VAR_NAME",
                secret=True,
                help="A service-account JSON file path or env:GOOGLE_APPLICATION_CREDENTIALS.",
            ),
        ],
    ),
    "postgres": ConnectorSchema(
        scheme="postgres",
        summary="Postgres tables — per-row index with cursor-based incremental sync.",
        uri_hint="postgres://prod-db",
        extras_hint=(
            "After the wizard you'll need [[objects]] blocks in the generated TOML to "
            "declare text_fields / locator_fields per table. See skills/mfs/references/connectors/postgres.md."
        ),
        fields=[
            ConnectorField("dsn", "DSN (postgresql://user:pass@host:5432/db)", secret=True),
            ConnectorField(
                "schemas",
                "Schemas to index (comma-separated)",
                multi=True,
                default="public",
                required=False,
            ),
            ConnectorField(
                "cursor_column",
                "Cursor column for incremental sync (often updated_at)",
                required=False,
            ),
            ConnectorField(
                "max_read_rows", "Max rows per table", type="int", default="100000", required=False
            ),
        ],
    ),
    "mysql": ConnectorSchema(
        scheme="mysql",
        summary="MySQL tables — per-row index with cursor-based incremental sync.",
        uri_hint="mysql://prod-db",
        extras_hint=(
            "After the wizard you'll need [[objects]] blocks for text_fields/locator_fields. "
            "See skills/mfs/references/connectors/mysql.md."
        ),
        fields=[
            ConnectorField("host", "Host", default="127.0.0.1"),
            ConnectorField("port", "Port", type="int", default="3306"),
            ConnectorField("database", "Database name"),
            ConnectorField("user", "User"),
            ConnectorField("password", "Password", secret=True),
            ConnectorField("cursor_column", "Cursor column (often updated_at)", required=False),
            ConnectorField(
                "max_read_rows", "Max rows per table", type="int", default="100000", required=False
            ),
        ],
    ),
    "mongo": ConnectorSchema(
        scheme="mongo",
        summary="MongoDB collections — per-document index.",
        uri_hint="mongo://prod-cluster",
        extras_hint=(
            "Set text_fields/locator_fields per collection via [[objects]] in the TOML. "
            "See skills/mfs/references/connectors/mongo.md."
        ),
        fields=[
            ConnectorField("uri", "URI (mongodb://user:pass@host:27017)", secret=True),
            ConnectorField("database", "Database"),
            ConnectorField("cursor_field", "Cursor field (often updatedAt or _id)", required=False),
            ConnectorField(
                "max_read_docs",
                "Max docs per collection",
                type="int",
                default="100000",
                required=False,
            ),
        ],
    ),
    "snowflake": ConnectorSchema(
        scheme="snowflake",
        summary="Snowflake tables (key-pair JWT auth required).",
        uri_hint="snowflake://analytics",
        extras_hint=(
            "Snowflake folds identifiers to UPPERCASE. Add [[objects]] for text_fields/"
            "locator_fields per table; see skills/mfs/references/connectors/snowflake.md."
        ),
        fields=[
            ConnectorField("account", "Account identifier (e.g. ABCDEFG-XY12345)"),
            ConnectorField("user", "User"),
            ConnectorField("warehouse", "Warehouse"),
            ConnectorField(
                "role", "Role (recommended: a narrow-scope read-only role)", required=False
            ),
            ConnectorField("database", "Database (or use 'databases' for many)", required=False),
            ConnectorField(
                "databases",
                "Databases (comma-separated, alt. to single 'database')",
                multi=True,
                required=False,
            ),
            ConnectorField(
                "credential_ref", "Private key reference (e.g. file:/path/to/key.p8)", secret=True
            ),
            ConnectorField(
                "private_key_passphrase_ref",
                "Passphrase reference (env:VAR or file:/path), empty = no passphrase",
                secret=True,
                required=False,
            ),
            ConnectorField(
                "max_read_rows", "Max rows per table", type="int", default="100000", required=False
            ),
        ],
    ),
    "bigquery": ConnectorSchema(
        scheme="bigquery",
        summary="BigQuery tables (uses Application Default Credentials).",
        uri_hint="bigquery://analytics",
        fields=[
            ConnectorField("project", "GCP project ID"),
            ConnectorField("datasets", "Datasets to index (comma-separated)", multi=True),
            ConnectorField("endpoint", "Custom endpoint (e.g. local emulator URL)", required=False),
            ConnectorField(
                "max_read_rows", "Max rows per table", type="int", default="100000", required=False
            ),
        ],
    ),
    "github": ConnectorSchema(
        scheme="github",
        summary="GitHub repository — code + issues / PRs as separate object kinds.",
        uri_hint="github://owner/repo",
        fields=[
            ConnectorField(
                "repo",
                "Repo (owner/name)",
                help="If your URI already encodes owner/repo this is optional.",
            ),
            ConnectorField("branch", "Branch (empty = repo default)", required=False),
            ConnectorField("token", "GitHub token (env:VAR_NAME supported)", secret=True),
            ConnectorField(
                "max_read_rows", "Max files / issues", type="int", default="100000", required=False
            ),
        ],
    ),
    "jira": ConnectorSchema(
        scheme="jira",
        summary="Jira issues — uses enhanced_jql (Cloud) for paged retrieval.",
        uri_hint="jira://acme",
        fields=[
            ConnectorField("url", "URL (e.g. https://acme.atlassian.net)"),
            ConnectorField("cloud", "Cloud (true) or Server (false)", type="bool", default="true"),
            ConnectorField(
                "username", "Email (Cloud) — leave empty for Server PAT", required=False
            ),
            ConnectorField("api_token", "API token (Cloud) or PAT (Server)", secret=True),
            ConnectorField(
                "projects",
                "Project keys (comma-separated, empty = all)",
                multi=True,
                required=False,
            ),
            ConnectorField(
                "max_read_rows",
                "Max issues per project",
                type="int",
                default="100000",
                required=False,
            ),
        ],
    ),
    "linear": ConnectorSchema(
        scheme="linear",
        summary="Linear issues across teams.",
        uri_hint="linear://workspace",
        fields=[
            ConnectorField("api_key", "API key (lin_api_...)", secret=True),
            ConnectorField(
                "teams", "Team IDs (comma-separated, empty = all)", multi=True, required=False
            ),
        ],
    ),
    "hubspot": ConnectorSchema(
        scheme="hubspot",
        summary="HubSpot CRM objects (contacts/companies/deals/tickets/...).",
        uri_hint="hubspot://acme",
        fields=[
            ConnectorField("access_token", "Private-app access token (pat-na1-...)", secret=True),
            ConnectorField(
                "object_types",
                "Object types (comma-separated, empty = probe-and-skip auto-detect)",
                multi=True,
                required=False,
                help="Default 'probe-and-skip' tries contacts/companies/deals/tickets and drops the ones the portal 403s on.",
            ),
        ],
    ),
    "salesforce": ConnectorSchema(
        scheme="salesforce",
        summary="Salesforce sObjects (Accounts, Contacts, Opportunities, ...).",
        uri_hint="salesforce://acme",
        fields=[
            ConnectorField("instance_url", "Instance URL (e.g. https://acme.my.salesforce.com)"),
            ConnectorField("domain", "Login domain (login/test)", default="login", required=False),
            ConnectorField("username", "Username"),
            ConnectorField("password", "Password", secret=True),
            ConnectorField("security_token", "Security token", secret=True),
            ConnectorField(
                "objects",
                "Objects to index (comma-separated, e.g. Account,Contact)",
                multi=True,
                required=False,
            ),
        ],
    ),
    "notion": ConnectorSchema(
        scheme="notion",
        summary="Notion pages / databases (Internal Integration token).",
        uri_hint="notion://workspace",
        fields=[
            ConnectorField("token", "Internal integration token (secret_...)", secret=True),
        ],
    ),
    "zendesk": ConnectorSchema(
        scheme="zendesk",
        summary="Zendesk tickets + articles.",
        uri_hint="zendesk://acme",
        fields=[
            ConnectorField("subdomain", "Subdomain (e.g. 'acme' from acme.zendesk.com)"),
            ConnectorField(
                "base_url", "Base URL override (empty = subdomain default)", required=False
            ),
            ConnectorField("username", "User email (used with /token suffix)"),
            ConnectorField("api_token", "API token", secret=True),
            ConnectorField(
                "max_read_rows", "Max tickets", type="int", default="100000", required=False
            ),
        ],
    ),
    "slack": ConnectorSchema(
        scheme="slack",
        summary="Slack channels — messages + threads.",
        uri_hint="slack://my-workspace",
        fields=[
            ConnectorField("token", "Bot (xoxb-...) or user (xoxp-...) token", secret=True),
            ConnectorField(
                "channel_types",
                "Channel types",
                multi=True,
                default="public_channel",
                required=False,
                help="comma-separated: public_channel, private_channel, mpim, im",
            ),
            ConnectorField(
                "oldest", "Oldest history boundary (e.g. now-30d, or unix ts)", required=False
            ),
            ConnectorField(
                "max_read_rows",
                "Max messages per channel",
                type="int",
                default="100000",
                required=False,
            ),
        ],
    ),
    "discord": ConnectorSchema(
        scheme="discord",
        summary="Discord guild channels — messages.",
        uri_hint="discord://my-guild",
        fields=[
            ConnectorField("token", "Bot token", secret=True),
            ConnectorField("guild_id", "Guild ID (numeric)"),
            ConnectorField(
                "max_read_rows",
                "Max messages per channel",
                type="int",
                default="100000",
                required=False,
            ),
        ],
    ),
    "gmail": ConnectorSchema(
        scheme="gmail",
        summary="Gmail threads — uses Google OAuth.",
        uri_hint="gmail://inbox",
        fields=[
            ConnectorField("token", "OAuth credentials file path", secret=True),
            ConnectorField(
                "labels",
                "Labels to index (comma-separated, empty = all)",
                multi=True,
                required=False,
            ),
            ConnectorField(
                "max_read_rows", "Max threads", type="int", default="100000", required=False
            ),
        ],
    ),
    "feishu": ConnectorSchema(
        scheme="feishu",
        summary="Feishu (Lark) docs + messenger chats. Two auth modes.",
        uri_hint="feishu://my-workspace",
        fields=[
            ConnectorField("app_id", "App ID (from the Lark Developer console)"),
            ConnectorField("app_secret", "App secret", secret=True),
            ConnectorField("region", "Region (cn or us)", default="cn", required=False),
            ConnectorField(
                "auth",
                "Auth mode (oauth = user-OAuth device flow, internal = app-only)",
                default="oauth",
                required=False,
            ),
            ConnectorField(
                "oauth_state_file",
                "OAuth state file (default $MFS_HOME/feishu.oauth.json)",
                required=False,
            ),
            ConnectorField(
                "docs_folder_token", "Docs folder token (limits the docs subtree)", required=False
            ),
            ConnectorField(
                "extra_chats",
                "Extra chat IDs to include (oc_xxx,oc_yyy)",
                multi=True,
                required=False,
            ),
            ConnectorField("extra_docs", "Extra doc tokens to include", multi=True, required=False),
        ],
    ),
}


def lookup_schema(scheme: str) -> Optional[ConnectorSchema]:
    return SCHEMAS.get(scheme)


def supported_schemes() -> list[str]:
    return sorted(SCHEMAS.keys())
