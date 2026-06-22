# Feishu / Lark (`feishu`)

The `feishu` connector indexes Feishu (Lark) messenger chats and docx documents.
It has two auth modes — **user** (the default) indexes everything the authorizing
person can see; **tenant** acts as an app-only bot limited to what it's added to.

## How MFS sees it

```text
feishu://workspace/
├── chats/
│   └── eng-team__oc_abc123/messages.jsonl   message_stream
└── docs/
    └── Roadmap__doccnxxx.md                 document
```

Chat messages are grouped into threads by the `feishu.messages` preset (rendered
with the sender id for a stronger search signal); only **docx** documents are
indexed.

## Credentials

Feishu / Lark needs an **App ID** + **App Secret** from the Lark Developer
Console.

1. <https://open.feishu.cn/app> (feishu / China) or
   <https://open.larksuite.com/app> (lark / overseas) → *Create Custom App*.
2. Note the **App ID** (`cli_…`) and **App Secret**.
3. *Permissions & Scopes* → add the read scopes, then request release/approval if
   your org requires it.

![Feishu Create Custom App button](https://github.com/user-attachments/assets/1099fb06-aa59-4b5f-ba1b-543f7551e508)

![Feishu Create custom app dialog](https://github.com/user-attachments/assets/99a2a7e2-769a-49b4-b3ba-2e8ee2409bea)

![Feishu app credentials section](https://github.com/user-attachments/assets/478ddc09-79e5-4d8c-a3c2-e441ebb37c66)

![Feishu Permissions and Scopes page](https://github.com/user-attachments/assets/4183bc00-e351-4576-9f19-8520290d114c)

![Feishu Version Management and Release page](https://github.com/user-attachments/assets/f1fa494a-46eb-4934-a65f-97fbd9f6eef8)

`feishu` and `lark` are separate registries — an app from one can't authorize
against the other (the `region` field selects which).

### User mode (default, recommended)

The app indexes everything the authorizing user can see. Add the scopes as **User
Scopes**: `im:chat:readonly`, `im:message.group_msg:get_as_user`,
`im:message.p2p_msg:get_as_user`, `drive:drive:readonly`,
`docx:document:readonly`, `contact:user.id:readonly`.

Add the connector — the wizard runs a one-time browser authorization inline:

```bash
mfs-server connector add feishu://workspace
```

The wizard prompts for App ID, App Secret, region, and auth mode, writes the TOML
under `$MFS_HOME/connectors/`, and registers the connector against the local
server. Open the printed URL and approve. **This consent must be done by a person
and can't be automated** — it's the OAuth user-authorization step. After that the
token refreshes automatically while the connector is actively synced (each sync
renews it). If it goes unused for several days the authorization expires; the next
use reports that re-authorization is needed. To re-authorize, have a person
approve the URL again — existing indexed data is unaffected:

```bash
mfs-server connector auth feishu://workspace
```

```toml
app_id = "cli_a1b2c3d4"
app_secret = "env:FEISHU_APP_SECRET"
region = "feishu"          # or "lark"
auth = "user"
```

### Tenant mode (app-only bot)

Set `auth = "tenant"`. The app acts as itself and sees only chats it's been added
to and docs/folders shared with it — add the bot to a chat by `@mentioning` it.
Add the same scopes as **Bot Scopes**.

```toml
app_id = "cli_a1b2c3d4"
app_secret = "env:FEISHU_APP_SECRET"
region = "feishu"
auth = "tenant"
docs_folder_token = "fldcn..."   # optional: limit docs to one shared folder
max_read_rows = 50000
```

Tenant mode does not need browser consent. You can use the same
`mfs-server connector add feishu://workspace` wizard and choose `tenant`, or save
the TOML yourself and run:

```bash
mfs connector probe feishu://workspace --config ./feishu.toml
mfs add feishu://workspace --config ./feishu.toml
```

## Sync and freshness

The connector uses the message `create_time` as its cursor; deletion detection is
`never`. Like [`gdrive`](gdrive.md), it honors `--since`: user mode can enumerate
your whole My Space, so for a large account estimate first (optionally with a
`since` date) and use `mfs add feishu://<alias> --since <date>` to index only
recently-changed docs. Older docs are left untouched and can be added later.

## Search and browse

```bash
mfs search "deploy failed" feishu://workspace/chats/
mfs search "quarterly roadmap" feishu://workspace/docs/
mfs cat feishu://workspace/docs/Roadmap__doccnxxx.md --range 1:80
```

## Pitfalls

- **p2p single chats** can't be auto-listed (a Feishu API limit) — include them
  with `extra_chats`, by `oc_…` chat id or the partner's `ou_…` open_id.
- **docs:** only docx is indexed. In user mode with no `docs_folder_token` /
  `extra_docs`, the whole My Space is enumerated; narrow with `docs_folder_token`
  or name docs with `extra_docs`. In tenant mode the app only sees what's shared
  with it.
