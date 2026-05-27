# Agent Skill 指南

这一篇写给给 LLM agent 集成 MFS 的人（Skill 作者、agent framework 维护者、prompt 工程师）。讲 agent 何时用哪个命令、怎么解读返回结果、怎么避开常见坑。

不是给 connector 贡献者看的（看 [07-contributing-connector.md](07-contributing-connector.md)），也不是给最终用户看的（看 README）。

## 1. Agent 的 MFS 心智模型

让 agent 先建立这套心智再讨论命令：

```
Agent 想找信息
   │
   ├─ 已经知道路径   →  cat / head / tail / ls / tree
   │
   ├─ 知道关键词     →  grep
   │
   ├─ 概念性问题     →  search（语义混合）
   │
   └─ 不知道有什么   →  tree --peek 先扫一圈
```

四条核心规则：

1. 看到 URI 不要瞎猜——先 `mfs ls / tree <uri>` 看暴露什么对象，读 skill 的 connector reference 了解布局，`mfs connector inspect <root>` 看结构化 capabilities
2. 结果是可继续操作的——search / grep 返回的 `source` URI 直接喂给 cat / head / export
3. 大对象不要 cat——`mfs cat <uri>` 对大对象会拒绝，要用 head / tail / range / export
4. 结构化对象不要用 `--peek / --skim / --deep`——这些只对 document / code 形态有效，对 JSONL 报错

## 2. 推荐工作流

### 工作流 A：在一个 connector 内找东西

```bash
# 1. 知道大致范围，先了解 connector 暴露什么
mfs connector inspect postgres://prod
# 或：
mfs tree postgres://prod --peek -L 2

# 2. 看具体目录下有什么对象
mfs ls postgres://prod/public/tickets
# schema.json / rows.jsonl 两个对象

# 3. 看 schema 理解数据
mfs cat postgres://prod/public/tickets/schema.json

# 4. 看几条样本理解数据形状
mfs head -n 5 postgres://prod/public/tickets/rows.jsonl

# 5. 语义搜索找候选
mfs search "customer cannot login" postgres://prod/public/tickets --top-k 5

# 6. 精确读单条（用 search 结果里的 locator）
mfs cat postgres://prod/public/tickets/rows.jsonl --locator '{"pk":{"id":12}}'
```

### 工作流 B：在本地代码仓库里找 bug

```bash
mfs tree --peek -L 2 ./src              # 了解结构
mfs search "session expiration" ./src   # 语义找候选
mfs grep "ERR_TOKEN" ./src              # 关键词找精确位置
mfs cat ./src/auth/token.py --range 150:180  # 读上下文
```

### 工作流 C：跨多个 connector 找一个决策的来龙去脉

```bash
mfs search "why did we change pricing limit" --all --top-k 10
# 返回可能混合：linear issue / github PR / slack thread

# 拿到结果继续展开：用 locator 精确取那一条
mfs cat linear://product/teams/Pricing/issues.jsonl --locator '{"team":"Pricing","id":"LIN-88"}'
```

### 工作流 D：周期跟随数据（v0.4 不内置 tail -f）

```bash
# 周期同步 + 看快照
watch -n 60 'mfs add slack://eng && mfs head -n 20 slack://eng/.../today/messages.jsonl'
watch -n 60 'mfs add s3://logs && mfs head -n 50 s3://logs/app/today.jsonl'
```

## 3. 怎么解读返回结果

### `--json` envelope

每个命令都支持 `--json`。agent 优先用 `--json` 而不是解析人类输出。统一结构：

```json
{
  "source": "postgres://prod/public/tickets/rows.jsonl",
  "lines": null,
  "content": "Login broken after SSO migration",
  "score": 0.842,
  "locator": {
    "schema": "public",
    "table": "tickets",
    "pk": {"id": 12}
  },
  "metadata": {
    "kind": "search",
    "chunk_kind": "row_text",
    "connector_type": "postgres",
    "media_type": "application/x-ndjson",
    "fields": {
      "status": "open",
      "priority": "high"
    }
  }
}
```

agent 关心的字段：

| 字段 | 怎么用 |
|---|---|
| `source` | 喂给下一个命令（cat / head / grep / export） |
| `content` | 给 LLM 看的内容 snippet |
| `locator` | 当 source 是集合对象时，精确指向单条 record |
| `score` | 排序参考；低于 0.5 通常不可靠 |
| `metadata.chunk_kind` | 区分召回类型（body / row_text / thread_aggregate / vlm_description / directory_summary） |
| `metadata.media_type` | 判断对象类型，决定下一步用什么命令 |
| `metadata.fields` | 不打开对象就能看到的业务字段（status / priority 等） |

### 从结果回到对象：locator 优先，lines 次之

`lines` 和 `locator` 是**两个独立的可选字段，不互斥**——有的 chunk 两个都有（如 slack thread：既在 messages.jsonl 有行位置，又有 thread_ts locator）。agent 按这个优先级用：

1. **`locator` 非空 → 优先用它**精确定位单元（DB row / issue / ticket / thread）：

```bash
mfs cat <source> --locator '{"pk":{"id":12}}'      # 推荐：按 locator 精确取回单条完整记录
mfs export <source> /tmp/data.jsonl && jq 'select(.id == 12)' /tmp/data.jsonl   # 备选：导出后过滤
```

2. **只有 `lines` 非空（locator 为 null）→ 用行区间**（纯文本 / document / code）：

```bash
mfs cat <source> --range <start>:<end>      # 直接读那段
```

每种 chunk_kind 填哪些字段是稳定契约，见 [06 §15](06-search-and-retrieval.md#15-json-envelope-searchgrep)。`locator` 的内部 schema per-connector，agent 不要硬编码——读 skill 里该 connector 的 `references/connectors/<name>.md`（或 [06 §3](06-search-and-retrieval.md#3-locator-schema-per-connector) 的表）拿它文档化的 locator schema。

## 4. 反模式

| 别这样 | 推荐 |
|---|---|
| `mfs cat <huge-rows.jsonl>`（不带 `--range`） | `mfs head -n 20` 或 `mfs cat --range 0:100` |
| `mfs cat --peek <rows.jsonl>` | `mfs head -n 5 <rows.jsonl>` |
| 拼构造 path 取单 record（如 `tickets/12.json`） | 用 search / grep 的结果 + `locator` |
| 用 pipe 传递 source 元信息 | 直接传 path 参数：`mfs search "..." <path>` |
| `mfs add <uri>` 然后假设立刻 search 可用 | `mfs status <uri>` 看 sync 进度，等 search=available |
| remote profile 下大目录 `mfs add ./repo`（会触发大量上传） | 先 `mfs connector probe ./repo` 看一眼，再决定 |
| `mfs cat` 看图片 | `mfs cat <img> --meta` 看 VLM description |
| `--all` 上跑没有 filter 的 query | 加 `--top-k` 限制 / 加 path 缩小范围 |

## 5. 错误码处理

agent 拿到 `--json` 输出里的 error 时，按 `code` 字段决定怎么 recover：

| code | 意思 | agent 该怎么办 |
|---|---|---|
| `object_too_large_for_cat` | 文件太大，cat 拒绝 | 改用 `head` / `tail` / `cat --range` / `export`，按 suggestions |
| `is_directory` | 对目录 cat | 改用 `ls` / `tree` |
| `sync_already_running` | 同 connector 正在 sync | `mfs status <uri>` 看进度，或 `mfs job cancel` |
| `connector_removing` | connector 正在 remove | 等清理完，或换 connector |
| `connector_unhealthy` | connector 连不上 | 看 error.details；用户层凭据问题 |
| `density_unsupported` | 结构化对象不能用 `--peek/--skim/--deep` | 改用 `head` |
| `since_unsupported` | connector 不支持 `--since` | 去掉 `--since`，直接 `mfs add` |
| `range_unsupported` | 二进制对象不支持 `--range` | 用 `head -c` 字节或 `export` |
| `tail_unsupported` | 对象 `capabilities.tail=false`（无稳定排序的集合等） | 改用 `head` / `cat --range` |
| `chunk_max_exceeded` | 对象太大，部分索引 | search 仍然可用但召回不全；建议用户加 `index_filter` |
| `upload_rejected` | 用户加了 `--no-upload` 拒绝上传 | 去掉 flag 或切换 local profile |
| `upload_bundle_too_large` | 单次 bundle 太大 | 加 ignore 规则缩小范围，或拆分目录 |
| `field_missing` | text_fields 配的字段不存在 | 用户层配置问题；提示用户改 connector TOML |

所有错误都有 `suggestions` 字段，优先按 suggestion 行动，不要试错。

补充错误码：

| code | 意思 | 怎么办 |
|---|---|---|
| `upload_not_applicable` | shared fs 场景下用了 `--force-upload` | 去掉 flag，shared fs 不存在上传 |

## 6. Skill 目录结构

MFS 发布一个 agent skill 包，结构如下：

```
skills/mfs/
├── SKILL.md                      # 主体：心智模型 + 命令清单 + 工作流 + 反模式
└── references/
    ├── connectors/               # 每个 connector 一份 PROMPT
    │   ├── file.md               # 来自 connectors/file/PROMPT.md
    │   ├── postgres.md           # 来自 connectors/postgres/PROMPT.md
    │   ├── slack.md              # 来自 connectors/slack/PROMPT.md
    │   ├── github.md
    │   ├── web.md
    │   └── ...                   # 所有已发布的 connector
    ├── error-codes.md            # 错误码表（从 protocol/errors.md 生成）
    ├── json-envelope.md          # JSON envelope schema 详解
    └── workflows.md              # 工作流示例库
```

`SKILL.md` 主体内容：

1. MFS 是什么（1 段）：file-like shell-native CLI，agent 直接用 shell 命令搜 / 读各种数据源
2. 命令清单 + 用途（一张表）：参考 [03-cli-commands.md](03-cli-commands.md)
3. 推荐工作流（本文 §2）
4. 结果 envelope + 怎么从结果回到对象（本文 §3）
5. 反模式列表（本文 §4）
6. 错误码处理（本文 §5）
7. 指向 `references/connectors/<name>.md`：agent 看到某 connector URI 就读对应的 reference 了解暴露的对象布局

### 连接器 PROMPT 由 CI 自动收纳进 references

每个 connector 在自己目录里写 `PROMPT.md`（详见 [07 §5](07-contributing-connector.md#5-promptmd-范本)）。**发版时 CI** 把这些 PROMPT 收纳进 skill bundle 的 references 目录——**不是用户敲的命令**：

```
CI 发版流程（对用户不可见）:
  1. 扫所有内置 ConnectorPlugin
  2. 每个 connector 的 PROMPT.md → references/connectors/<name>.md
  3. protocol/errors.md → references/error-codes.md
  4. SKILL.md frontmatter 盖章版本（见下）+ 更新 connector 索引
  5. 整个 skills/mfs/ 作为一个包发布到 GitHub
```

贡献者的体验：**写好 `PROMPT.md` 就行，CI 自动把它发进主 skill 的 references，更新也自动跟**。没有 `mfs skill build` 这种用户命令——用户只通过自己 agent 框架的机制 install / update 这个包（Anthropic Skills / Cursor Rules / Cline 等都能直接消费这种 markdown bundle）。

### 运行时只查结构化能力，不吐 prose

per-connector 的**布局说明**（长 markdown）住在 bundle 的 `references/connectors/<name>.md`，agent 加载 skill 时就有、按相对链接读。**inspect 不再返回这一大段 prose**（那样的 JSON 体验很差）。运行时只查**结构化、小**的信息：

```bash
mfs connector list --json     # 看有哪些 connector 已注册
mfs ls <uri> --json           # 目录 URI 列子项 + 每项 capabilities；单对象 URI 返回该对象自身那行（cat / grep / tail / range）
```

分工：**"这个 connector 怎么布局"读 bundle 里的 reference；"这个对象能用什么命令"查 `ls --json` 的结构化 capabilities**。两边内容不相交——一个是 prose 文档（bundle），一个是结构化字段（runtime），不重复、agent 不会纠结看哪个。

### Skill 版本管理：整份装着 + 自检版本 + 温和修复

skill 跟 MFS 是**两条独立安装轨**——skill 由 agent 框架管（npx skills / Cursor / Cline，各有机制、且跟 MFS 版本无耦合），MFS 由用户自己装。所以 skill 版本和运行中 MFS 版本**会漂移**。

不试图阻止漂移（做不到），而是**让 agent 自己察觉 + 温和修复**：

**① skill frontmatter 盖版本**（CI 发版时写）：

```yaml
---
name: mfs
version: 0.4.3                # 这份 skill 出自哪个 MFS 版本
mfs_compat: ">=0.4,<0.5"      # 兼容的运行时 MFS 版本范围
---
```

skill 不走 git、靠框架机制装，所以版本只能写在它自己的 frontmatter 里——这是 agent 运行时唯一读得到的地方。

**② SKILL.md 指令 agent 操作前轻检查**（gentle，不打断）：

```
执行 MFS 操作前，跑 mfs --version / mfs status 拿运行中 MFS 版本，
跟本 skill frontmatter 的 mfs_compat 比一下。
对不上 → 心里有数即可，照常继续（核心命令/玩法大概率没变，多数版本差异无害）。
```

**③ 真跑挂时才升级处理**：

```
某命令报错 / 行为不符 + 之前已知版本对不上
  → agent 反应过来"很可能版本错位"
  → 读 SKILL.md / README 的【版本修复建议】段，反过来引导用户：
     "你的 skill 是 0.4、MFS 是 0.6，更新 skill 用 <命令>、更新 MFS 用 <命令>，同步一下"
```

**④ 修复建议固定写在 SKILL.md / README**：怎么更新 skill（`npx skills update mfs` 之类）、怎么更新 MFS CLI（`uv tool upgrade mfs` / `brew upgrade mfs`）。agent 在版本不符尤其跑挂时看这一眼就知道怎么引导。

精髓：**先试别拦，只在真跑挂时凭"已知版本不符 + 固定修复建议"引导用户同步**。符合"多数更新主框架不变"的现实，比一上来强提示舒服。

> per-connector 单独版本字段先不做（skill 级 `mfs_compat` 已回答"整体对不对得上"这个粗问题）；以后真要更细信号，再在 `references/connectors/<name>.md` 的 frontmatter 加 `version`，是增量。

## 7. 让 agent 自己发现能力

agent 不要硬编码"什么 connector 支持什么操作"。运行时 query 的是**结构化 capabilities**（不是 prose 布局——布局读 skill 的 `references/connectors/<name>.md`）：

```bash
mfs connector list --json
mfs ls <uri> --json                     # 目录 URI 列子项 + 每项 capabilities；单对象 URI 返回该对象自身那行
```

`capabilities` 字段告诉 agent 这个对象能用什么命令：

```json
{
  "cat": "denied_unless_range",
  "grep": "pushdown",
  "range": true
}
```

agent 看到 `cat="denied_unless_range"` 就不要直接 cat，走 head 或 range；看到 `grep="pushdown"` 就用 `mfs grep` 让 server 下推 SQL / API。

### search 建好没：`search_status` 决定走 search 还是 grep

`ls --json` 的每个 object 还带一个 `search_status`，告诉 agent 这个对象的语义索引就绪没（呼应"渐进可用"，agent 不用空等全量索引）：

| search_status | agent 该走 |
|---|---|
| `indexed` | `mfs search`（语义混合，最佳） |
| `partial` | 部分索引（chunk_max 超限 / windowed / sampled）→ `search` 可用但召回不全，关键查询再补一发 `grep` |
| `stale` | 仍可 `search`（结果可能旧），或 `mfs add` 刷新后再搜 |
| `building` | 还在建——`mfs status` 等就绪，或先用 grep 兜底 |
| `not_indexed` | 没语义索引（indexable=false / 还没建）→ 用 grep |

对 `not_indexed` 对象 grep 不依赖 Milvus（没 chunks，走连接器下推或线性扫，详见 [05 §6](05-browse-and-read.md#6-grep-的派发)），索引没就绪也能兜底。

> **grep 的精确性随派发路径而变，agent 别假设它总是字面精确**：connector 下推（SQL `ILIKE` / provider search）和线性扫是字面精确的；而**已索引对象**的 grep 默认走 Milvus BM25（token 级、非 regex、不保证字面命中），返回的是 chunk 片段。要字面精确穷尽，用支持下推的源，或 `mfs export` + 本地 `grep` / `rg`。

> grep 具体用哪个——`mfs grep`、系统 `grep`、`ripgrep`，还是 agent 框架自带的搜索 tool——**不强制**，用 agent 默认提供的那个即可。MFS 不要求特定实现，`mfs grep` 只是其中一条路径。

这种动态发现让 agent 跟着新 connector 自动适应，不用每加一个 source 就改 skill 文件。

## 8. 多步骤任务的中断与恢复

agent 跑长流程（如先 mfs add 一个大 connector 再 search）：

- `mfs add` 默认后台跑，立刻返回 `job_id`
- agent 应该 poll `mfs status <uri>` 或 `mfs job inspect <job_id>` 直到 `search: available`
- 不要假设 add 完立刻能 search

poll 模板（伪 shell）：

```bash
JOB=$(mfs add postgres://prod --config x.toml --yes --json | jq -r '.job_id')
while true; do
  STATUS=$(mfs job inspect "$JOB" --json | jq -r '.status')
  case $STATUS in
    succeeded) break ;;
    failed|cancelled) exit 1 ;;
    *) sleep 30 ;;
  esac
done
mfs search "..." postgres://prod
```

## 9. 给 connector 贡献者的提示

贡献新 connector 时别忘了在 connector 目录里写一份 `PROMPT.md`，agent skill 会读它生成上下文。详见 [07 §5](07-contributing-connector.md#5-promptmd-范本)。

好的 `PROMPT.md` 让 agent 不需要试错就知道：

- 这个 connector 下面有什么对象
- 每个对象 cat 出来是什么格式
- 大对象有什么限制
- 推荐什么命令路径
