# Connector 目录布局参考

每类 connector 决定自己 root 下面暴露哪些 object。这一篇是各 connector path 布局的参考清单。

约定：

- **对象名带 media type 后缀**（`.json` / `.jsonl` / `.patch` / `.md` 等），跟 unix 习惯一致
- **目录节点没有后缀**，路径末尾用 `/`；`cat` 一个目录返回 `is_directory` 错误
- 单条 record（一条 ticket / 一行 row）不暴露成单独的 path，通过搜索结果的 `locator` 定位

各 connector 的 path 长什么样：

## v0.4 connector 类型一览

| 类别 | Connectors |
|---|---|
| 文件 / 对象存储 | file, GitHub repo, Google Drive, Feishu Docs, S3 / R2 / GCS / MinIO, SSH remote fs |
| 网页 | web（HTML crawl → markdown） |
| 消息 | Slack, Discord, Gmail, 飞书群聊, Telegram, Email |
| 任务 / 协作 | GitHub Issues / PRs, Jira, Linear, Notion |
| 数据库 | Postgres, MySQL, MongoDB, BigQuery, Snowflake |
| 业务 SaaS | Salesforce, HubSpot, Zendesk |

## File

```text
./repo/
  <原始文件树，保留原文件名和后缀>
```

代码、文档、图片、JSON、CSV 都按各自扩展名进入对应处理器。`mfs ls ./repo` 看到的就是真实目录。

file connector 在 client / server 共享 fs 时直接读本机；不共享 fs 时通过 upload flow 把字节落到 server staging area，扫描该 staging 子目录。两种情况共用同一份代码，scope 由 framework 注入（详见 [02 §4.2](02-architecture.md#42-本地文件-upload-flow不共享-fs-时)）。

## Web

抓取网页转 markdown 缓存（v0.4 默认抓静态 / SSR HTML：aiohttp + markitdown；JS-heavy SPA 留 v0.5+，可选 crawl4ai backend）：

```text
web://<alias>/
  index.json                          # crawl 后的页面索引（URL → title / fingerprint）
  pages/
    <host>/<url-path>.md              # HTML 转 markdown
  assets/
    <hash>.<ext>                      # 引用的图片 / 附件
```

URL → path 的规范化（query / fragment / 大小写 / 尾斜杠）见 [07 §10.7](07-contributing-connector.md#107-url--path-规范化web--crawler-类-connector-必须遵守)。`cat web://acme-docs/pages/docs.acme.com/api/auth.md` 返回 markdown；`cat web://acme-docs/assets/abc123.png` 返回图片字节。

## GitHub repo（代码 + issues / pulls）

```text
github://<alias>/
  <原始 repo 文件树，按 branch HEAD>
  _meta/
    issues.jsonl                      # 全部 issues
    pulls.jsonl                       # 全部 PRs
    pulls/<n>/diff.patch              # 单 PR 的 diff
    pulls/<n>/reviews.jsonl
    pulls/<n>/comments.jsonl
```

`_meta/` 用来跟真实文件树区分。

## Google Drive

```text
gdrive://<alias>/
  <镜像 Drive 文件树>
    Pricing.gdoc                      # cat → 导出为 markdown
    Roadmap.gsheet/                   # 目录
      Sheet1.jsonl
      Sheet2.jsonl
    Chart.png                         # cat → bytes
    DPA.pdf                           # cat → bytes；索引时转 markdown
```

## S3 / R2 / GCS / MinIO

```text
s3://<alias>/
  <object key 树，保留原 key 名>
    app/2026-05-10/app.jsonl
    reports/Q1.csv
    images/header.png
```

## Slack

```text
slack://<alias>/
  channels/
    <name>__<channel-id>/
      <yyyy-mm-dd>/
        messages.jsonl                # 当天消息
        threads.jsonl                 # 当天 thread 聚合
        files/                        # 当天附件目录
          <name>__<F-id>.<ext>
  dms/
    <user>__<dm-id>/
      <yyyy-mm-dd>/
        messages.jsonl
        files/
  users.jsonl
```

频道和 DM 用 `<sanitized-name>__<id>` 命名，ID 必带——名字可重复。

## Discord

```text
discord://<alias>/
  channels/
    <name>__<channel-id>/
      <yyyy-mm-dd>/
        messages.jsonl
        files/
  forums/
    <forum>__<id>/
      <thread>__<id>/
        messages.jsonl
        files/
  users.jsonl
```

## Gmail

```text
gmail://<alias>/
  labels/
    inbox/<yyyy-mm>/
      messages.jsonl
      threads.jsonl
    support/<yyyy-mm>/
      ...
  attachments/
    <file_id>__<name>.<ext>
  users.jsonl                         # contacts，可选
```

## Postgres / MySQL

```text
postgres://<alias>/
  database.json                       # 跨 schema 概览
  <schema>/
    tables/
      <table>/
        schema.json                   # column / PK / FK / index
        rows.jsonl                    # 全部行（lazy，大表不物化）
    views/
      <view>/
        schema.json
        rows.jsonl
```

`head -n N rows.jsonl` 下推为 `SELECT ... LIMIT N`。首次 head 的预 cache 由 framework 内部处理（`head_cache`），用户不感知。

## MongoDB

```text
mongo://<alias>/
  <db>/
    collections/
      <col>/
        schema.json                   # 采样推断 + confidence
        documents.jsonl               # lazy
```

## BigQuery / Snowflake

```text
bigquery://<alias>/
  <dataset>/
    tables/
      <table>/
        schema.json
        rows.jsonl                    # lazy
        partition/<partition-key>.jsonl
```

## Linear / Jira / Notion

```text
linear://<alias>/
  teams/
    <team>/
      issues.jsonl
      cycles.jsonl
  users.jsonl
  workflows.json

jira://<alias>/
  projects/
    <proj>/
      issues.jsonl
      sprints.jsonl
      versions.jsonl
  users.jsonl

notion://<alias>/
  pages/<id>.md                       # 页面文本
  databases/<id>/
    schema.json
    records.jsonl
```

## Zendesk / Salesforce / HubSpot

```text
zendesk://<alias>/
  tickets/
    schema.json
    records.jsonl                     # 全部 ticket
    comments.jsonl                    # 全部 comment（带 ticket_id）
  users/records.jsonl
  organizations/records.jsonl

salesforce://<alias>/
  <object>/                           # Account / Opportunity / Case / ...
    schema.json
    records.jsonl
    activities.jsonl

hubspot://<alias>/
  <object>/                           # contacts / companies / deals / ...
    schema.json
    records.jsonl
```

## 命名规范

每个 connector 贡献者要遵守的对象命名约定见 [07 §10](07-contributing-connector.md#10-对象命名规范)。简要说：

- 集合用 `.jsonl`（`rows`、`messages`、`issues`、`records`、`comments`、`users`、`threads`、`activities`）
- 单元数据描述用 `.json`（`schema.json`、`database.json`、`workflows.json`、`index.json`）
- 长文本对象用 `.md`
- 真实文件保留原文件名和后缀
- 目录节点不带后缀
- 单 record 走 `locator` 不暴露成 path（GitHub PR `pulls/42/diff.patch` 这种是例外，单条对象有持久 path 且数量可控）
