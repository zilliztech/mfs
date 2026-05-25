# 打包、部署、运维

这一篇讲 MFS 怎么交付、怎么部署、怎么观测。看完这篇知道：用户怎么装、运维怎么起服务、发版工作流是什么。

架构层面的内容在 [02-architecture.md](02-architecture.md)，这里只讲实施细节。

## 1. 实现语言

MFS 是多语言混合项目，每块按场景选最顺手的语言：

| 模块 | 语言 | 理由 |
|---|---|---|
| `mfs` CLI | Rust | 单 binary、冷启动几十 ms，跟 docker/kubectl/git 一个体验档次 |
| API server / engine / connectors | Python + FastAPI | IO-bound 为主；外部 SDK（postgres / slack / openai 等）几乎都是 Python first-class |
| Server hot path | Rust，PyO3 绑定 | 大目录扫描、JSONL 流处理、grep——这几个是 Python 拉胯的地方 |
| Python SDK | Python | 给写脚本的用户 |
| TypeScript / Go / Java SDK | 各自语言，从 OpenAPI 生成 | 跨语言集成 |

CLI 跟 server 走 HTTP，互相不 import，所以 CLI 用什么语言跟 server 怎么写完全独立。

### 为什么 CLI 走 Rust，不走 Python

| 维度 | Python CLI | Rust binary |
|---|---|---|
| 安装产物 | 一堆 .py + 依赖 | 一个 binary 文件 |
| 安装体积 | 100-300 MB | 30-50 MB |
| 冷启动 | 200-500 ms | 几十 ms |
| 分发渠道 | PyPI / uv | brew / scoop / cargo / 直接下载 |

agent 大量循环调 CLI，冷启动 100 ms 跟 30 ms 累积下来差很多。Rust binary 也跟用户对"现代 CLI 工具"的预期一致（ripgrep / fd / uv 都是这套）。

### Python + Rust 互操作

业界已经很成熟：polars、pydantic v2、ruff、uv、tokenizers 都是 "Rust 核心 + Python 包装" 的组合。MFS 沿用同一套：

```bash
# 开发时
cd server-rs/
maturin develop --release          # 编译 Rust + 装到当前 Python 环境

# Python 里直接 import
from mfs_server_rs import scan_dir, parse_jsonl_stream, linear_grep
```

Rust 模块只暴露给 server 内部的几个 hot path，connector 贡献者完全感知不到，写 connector 全用 Python。

## 2. 交付物清单

| 交付物 | 实现 | 用户安装 |
|---|---|---|
| `mfs` CLI binary | Rust | `brew` / `scoop` / `cargo install` / `uv tool install` / `curl install.sh` |
| `mfs-server` | Python + Rust PyO3 wheel | `uv tool install mfs-server` |
| `mfs-server` 容器镜像（`mfs-api` / `mfs-worker` / `mfs-server-aio`） | Docker | `docker pull` / Compose / Helm |
| `mfs-sdk`（Python 程序化集成） | Python | `pip install mfs-sdk` |
| `@mfs/sdk`（TS） | TypeScript | `npm install @mfs/sdk` |
| Go SDK | Go | `go get github.com/zilliztech/mfs-sdk-go` |
| Java SDK | Java | `io.zilliz.mfs:mfs-sdk` |

多语言 SDK 都从 `protocol/openapi.yaml` 生成。

## 3. 用户安装速查

```bash
# macOS / Linux
curl -fsSL https://mfs.dev/install.sh | sh        # 一行下载 binary
brew install zilliztech/tap/mfs                   # Homebrew

# Windows
irm https://mfs.dev/install.ps1 | iex             # PowerShell 一行
scoop install mfs                                 # Scoop

# 通过语言包管理器
cargo install mfs                                 # Rust 用户
pip install mfs                                   # Python 用户（实际装 wheel，里面是 Rust binary）
uv tool install mfs                               # uv 用户

# Server（个人本机）
uv tool install mfs-server
```

`mfs-server` 按需装额外的 connector 依赖：

```text
mfs-server[postgres]    mfs-server[mysql]       mfs-server[mongo]
mfs-server[slack]       mfs-server[discord]     mfs-server[gmail]
mfs-server[github]      mfs-server[linear]      mfs-server[jira]
mfs-server[notion]      mfs-server[gdrive]      mfs-server[feishu]
mfs-server[s3]          mfs-server[web]
mfs-server[salesforce]  mfs-server[hubspot]     mfs-server[zendesk]
mfs-server[bigquery]    mfs-server[snowflake]
mfs-server[embedding-onnx]    mfs-server[embedding-google]
mfs-server[llm-anthropic]     mfs-server[llm-google]
mfs-server[web-crawl4ai]      mfs-server[converter-docling]   # 可选重型 backend（playwright / vision 模型）
mfs-server[zilliz]
mfs-server[all]
```

## 4. 部署形态

### 4.1 个人本机

```bash
brew install mfs                # CLI
uv tool install mfs-server      # Server
mfs serve start
mfs profile add local --url http://127.0.0.1:8765
mfs profile use local
mfs add .
```

默认存储：

- Metadata: `~/.mfs/metadata.db`（SQLite）
- Cache: `~/.mfs/cache/`（本地文件）
- Milvus: `~/.mfs/milvus.db`（Lite）

适合个人使用、零运维。

### 4.2 单容器 server（demo / 小规模自部署）

```bash
docker run --rm -p 8765:8765 \
  -v mfs-data:/data \
  ghcr.io/zilliztech/mfs-server:0.4.0
```

API + worker 同进程，存储用容器内 SQLite + Milvus Lite。**记得挂 volume**，否则容器重启数据没了。

### 4.3 Docker Compose（团队 / CS 部署）

API、worker 用 Compose 起；Milvus 推荐用 Zilliz Cloud（托管，不自部署）：

```yaml
services:
  postgres:    image: postgres:16              # metadata
  minio:       image: minio/minio              # object store
  mfs-api:     image: ghcr.io/zilliztech/mfs-api:0.4.0
  mfs-worker:  image: ghcr.io/zilliztech/mfs-worker:0.4.0
```

`server.toml` 指向 Zilliz Cloud：

```toml
[milvus]
uri = "https://xxx.zillizcloud.com"
token = "<env:ZILLIZ_TOKEN>"
```

> 自部署 Milvus 需要自己跑 etcd + pulsar + 对象存储一整套容器拓扑，运维成本不低。v0.4 默认假设走 Zilliz Cloud，要自部署去 milvus.io 找官方 compose。

### 4.4 Kubernetes

```bash
helm install mfs ./charts/mfs \
  --set api.replicas=2 \
  --set worker.replicas=4 \
  --set objectStore.type=s3 \
  --set search.type=zilliz \
  --set metadata.type=postgres
```

## 5. 工程目录结构

```
.
├── cli/                                 # Rust CLI（单 binary 分发）
│   ├── Cargo.toml
│   ├── src/
│   │   ├── main.rs
│   │   ├── commands/                    # add / search / grep / ls / cat / ... 每条命令一个模块
│   │   ├── transport/                   # HTTP client + machine-id 探测
│   │   ├── client_config/               # client.toml 解析
│   │   ├── output/                      # 人类可读 + JSON envelope
│   │   └── models/                      # 从 protocol/openapi.yaml 生成
│   └── tests/
│
├── protocol/                            # 跨语言契约
│   ├── openapi.yaml                     # client ↔ server HTTP API
│   ├── schemas/                         # JSON schema
│   └── errors.md                        # 错误码表
│
├── server/python/                       # PyPI: mfs-server
│   ├── pyproject.toml
│   └── src/mfs_server/
│       ├── api/                         # FastAPI routes
│       ├── server/                      # mfs-server CLI entry (run / api / worker)
│       ├── worker/
│       ├── engine/                      # 业务编排
│       ├── connectors/                  # 每类 connector 自包含
│       │   ├── base.py / registry.py
│       │   ├── file/  web/  postgres/  slack/  github/  ...
│       ├── processors/                  # 按 object_kind 加工
│       │   ├── document/  code/  table_rows/  message_stream/
│       │   ├── record_collection/  image/  binary/
│       ├── common/                      # 通用服务
│       │   ├── embedding/  summary/  vlm/
│       │   ├── retrieval/  export/
│       └── storage/                     # metadata / object_store / queue / search adapter
│
├── server-rs/                           # Rust 加速模块（PyO3）
│   ├── Cargo.toml
│   ├── pyproject.toml                   # maturin
│   ├── mfs-scan/                        # 大目录扫描 + manifest hash
│   ├── mfs-jsonl/                       # JSONL / CSV / Parquet 流处理
│   └── mfs-grep/                        # 高并发线性 grep
│
├── sdks/                                # 从 OpenAPI 生成
│   ├── python/  typescript/  go/  java/
│
├── deployments/
│   ├── docker/  compose/  helm/
│
├── tests/
│   ├── cli/  server/  connectors/  e2e/  fixtures/
│
├── docs/
└── skills/                              # agent skill（pure markdown）
```

几个关键点：

- CLI 跟 server 通过 HTTP 通信，没有 import 关系 —— CLI 选 Rust 不影响 server。
- Rust 加速模块通过 PyO3 暴露给 Python，maturin 编译成 wheel；Python 代码 `from mfs_server_rs import scan_dir` 直接调，跟普通 Python 函数一样。
- 多语言 SDK 都从 `protocol/openapi.yaml` 生成；Python SDK 跟 CLI 独立（CLI 是 Rust binary，SDK 是给 Python 程序集成用）。
- 文本 chunking 用 **Chonkie** `RecursiveChunker`（`processors/document/`），代码用 **Chonkie** `CodeChunker`（底层 tree-sitter，`processors/code/`）。chunker 库版本在 `pyproject.toml` pin 死保证可复现（chunker 不进 cache、不算指纹）；convert / embed / vlm / summary 的结果进 cache，模型 / 库版本是各自 cache key 的一部分（见 04 §5.2 / 06 §6）。

## 6. 发版工作流

```
本地：
  bump 版本号（Cargo.toml + pyproject.toml + package.json 等）
  git tag v0.4.0 && git push --tags

GitHub Actions 自动跑：
  ① cargo-dist:  build CLI 多平台 binary → GH Releases + Homebrew tap PR
  ② maturin:     build mfs-server wheel 多平台 → PyPI
  ③ docker:      build / push mfs-api / mfs-worker / mfs-server-aio → ghcr.io
  ④ npm:         TS SDK → npm
  ⑤ Maven:       Java SDK → Maven Central
  ⑥ Go SDK 通过 git tag 自动可用，无需 publish
```

一次 git tag 触发全套，通常 10-15 分钟全部完成。

### CLI 跨平台编译

用 cargo-dist 自动化（uv / ruff / starship / atuin 都用这套），target matrix 覆盖 99% 用户：

```
x86_64-unknown-linux-gnu      # Linux x86_64
aarch64-unknown-linux-gnu     # Linux ARM64
x86_64-unknown-linux-musl     # Alpine 静态链接
x86_64-apple-darwin           # macOS Intel
aarch64-apple-darwin          # macOS Apple Silicon
x86_64-pc-windows-msvc        # Windows
```

### Server 端 Rust 模块走 maturin

`server-rs/` 里的 Rust crate 不单独发到 crates.io，而是作为 `mfs-server` 的 native extension 一起打 wheel 发 PyPI：

```bash
# CI
maturin build --release --target <platform>     # matrix
twine upload dist/*.whl
```

每个 `mfs-server` 版本对应多个 wheel（每平台一个），里面已经包含编译好的 `.so` / `.pyd`。用户 `uv tool install mfs-server` 时 pip 自动选当前平台 wheel，不需要装 Rust。

### Rust 用 crates.io 的位置

`cargo install mfs` 是下载源码本地编译，几分钟才能跑起来。所以 crates.io 不是主路径，只是给 Rust 老用户的便利入口。主分发还是 GitHub Releases binary + Homebrew tap + Scoop bucket + PyPI wheel。

## 7. 版本策略

```
0.x.y    快速迭代
1.0.0    CLI / 本机 daemon / 多 connector / API v1 / Milvus schema 全部稳定
```

进入 1.0 后保持兼容的面：

- `mfs` CLI 命令和参数
- HTTP API `/v1`（additive only，breaking 进 `/v2`）
- JSON envelope
- connector URI 与对象命名约定
- connector TOML schema、`chunk_kind` 枚举、`locator` schema
- Milvus collection schema（含 `namespace_id` 字段）
- server 镜像 entrypoint

CLI/server 版本兼容关系写进 release note：

```
MFS CLI 0.4.x supports MFS API v1.
Minimum server version: 0.4.0.
```

## 8. 运维与可观测

用户面通过 `mfs status` 看状态。后台指标走 Prometheus / OpenTelemetry，主要观测：

- connector healthcheck 通过率、sync lag
- 任务队列深度、worker 心跳
- API 延迟、search 延迟
- embedding cost（token / $）
- 存储用量、cache 命中率
- Milvus 查询 QPS / 延迟

operation log 存 `~/.mfs/audit.log`（本机）或 server 端 audit table（远端）。
