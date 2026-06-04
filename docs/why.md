# Why MFS

MFS is useful when the hard part is not reading one known file. It is useful
when you need to find candidate evidence across a large or mixed source, then
reopen the exact source before acting on it.

## Decide quickly

| Situation | Use MFS? | Why | Next page |
|---|---:|---|---|
| You know the exact local file path or exact token. | Usually no | Shell tools such as `grep`, `rg`, `find`, `sed`, and `cat` are faster and need no server or index. | Use your shell. |
| You have a conceptual question and the wording may not match the answer. | Yes | `mfs search` can find semantic and keyword candidates, then `cat`/`head`/`tail` reopens the source. | [Search and Browse](search-and-browse.md) |
| You need to search several source types through one command surface. | Yes | Registered sources expose file-like URI trees for `search`, `grep`, `ls`, `tree`, `cat`, and related commands. | [Connectors](connectors.md) |
| You are building an agent workflow that needs structured results. | Yes | The CLI can emit JSON with `source`, `locator`, metadata, and server error codes. | [CLI Reference](cli.md) and [HTTP API](api.md) |
| You need a POSIX mount, file writes, lock semantics, or kernel-level filesystem behavior. | No | MFS is not a mounted filesystem. It adds search, browse, and read surfaces over sources. | Use a filesystem or storage layer. |
| You need an application vector database backend. | No | MFS may use Milvus or Zilliz for its own index, but it is not a vector database replacement for your app. | Use your vector database directly. |
| You want a runnable v0.4 beta deployment today. | Yes, with beta caveats | Source, Docker all-in-one, and Compose all-in-one are the runnable shapes. Helm api/worker is rendered post-v0.4 direction. | [Deployment](deployment.md) |

## Mental model

Search is not the final answer. Search gives candidates; browse and read give
evidence.

| Need | Best first tool | What to do next |
|---|---|---|
| Exact local token in a known folder | Shell `rg`/`grep` | Open the file with normal shell tools. |
| Exact token in a registered MFS source | `mfs grep PATTERN PATH` | Use the returned `source` and locator with `mfs cat`. |
| Concept, paraphrase, or mixed semantic/keyword query | `mfs search QUERY PATH` or `mfs search QUERY --all` | Reopen likely hits with `mfs cat SOURCE --range A:B` or `mfs cat SOURCE --locator JSON`. |
| Directory or source orientation | `mfs ls PATH` or `mfs tree PATH -L N` | Narrow the path, then search or read. |
| Large object or uncertain hit | `mfs head`, `mfs tail`, `mfs cat --range`, or `mfs export` | Read only enough to verify, or export the full object outside the prompt. |

!!! warning "Treat search results as candidates"
    Do not rely on a snippet alone. Before quoting, summarizing, editing, or
    making a decision, reopen exact evidence with `cat`, `head`, `tail`,
    `export`, `cat --range`, or `cat --locator`.

## What MFS is in v0.4

| Area | v0.4 boundary |
|---|---|
| CLI | A Rust binary named `mfs`. The published v0.4 beta artifact is the CLI. |
| Server | A Python FastAPI server named `mfs-server`. During the beta, run it from source or a locally built Docker/Compose image. |
| Protocol | The CLI and SDKs call the HTTP `/v1` control plane. The CLI default endpoint is `http://127.0.0.1:13619`; `mfs-server run` and `mfs-server api` bind to `127.0.0.1:13619` by default. |
| Ownership | Original sources remain the source of truth. MFS stores connector metadata, jobs, artifacts, and searchable indexes so clients can search, browse, and read them. |
| Connectors | The `file` connector is imported directly. Other built-in schemes are imported lazily and skipped when optional dependencies are absent in that server environment. |
| Deployment | Source, Docker all-in-one, and Compose all-in-one are runnable v0.4 shapes. The Helm api/worker chart is rendered as the post-v0.4 scalable direction. |

## Agent and human usage

| User | Good pattern | Avoid |
|---|---|---|
| Agent | Use scoped `mfs --json search`, preserve `source` and `locator`, then reopen exact evidence before editing or answering. | Editing based only on ranked snippets or guessed locator shapes. |
| Human | Use human output for orientation, `mfs tree` to narrow, and `mfs cat --range` for exact context. | Searching the whole namespace with `--all` when a narrower path is known. |
| Integration developer | Use `/v1` or generated SDKs when shelling out is awkward, and handle API errors by `code`. | Depending on generated SDK README defaults without setting the actual server URL and bearer token. |
| Operator | Start with source, Docker, or Compose all-in-one, persist `$MFS_HOME` or `/data`, and use upload mode when the server cannot read client paths. | Treating the rendered Helm api/worker chart as the default runnable v0.4 deployment. |

## Beta caveats

| Caveat | Practical effect |
|---|---|
| Server distribution | The CLI is published; the server is run from repository source or a locally built container. |
| Auth | `/v1` is bearer-token protected when auth is configured. Local CLI runs can read `$MFS_HOME/server.token`; remote clients must send a token. |
| Connector availability | Built-in schemes depend on the server environment. Probe the connector on the server where it will run. |
| Search completeness | Indexing state matters. If results are weak or incomplete, use `mfs ls --json`, `mfs grep`, and browse commands to separate ranking issues from indexing issues. |
| API stability | The beta HTTP API may still shift before a stable v0.4 release, so pin versions for scripts and integrations. |

## Open next

| If you want to... | Open |
|---|---|
| Run MFS locally for the first time | [Quickstart](getting-started.md) |
| Learn the search -> locate -> read loop | [Search and Browse](search-and-browse.md) |
| Check exact command names and flags | [CLI Reference](cli.md) |
| Add files, databases, SaaS tools, or object stores | [Connectors](connectors.md) |
| Call MFS from another program | [HTTP API](api.md) and [SDKs](sdks.md) |
| Pick a source, Docker, Compose, or rendered Helm shape | [Deployment](deployment.md) |
| Debug endpoint, auth, upload, indexing, or browse failures | [Troubleshooting](troubleshooting.md) |
