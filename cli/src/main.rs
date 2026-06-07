//! MFS CLI — thin HTTP client over the server's /v1 control plane.
//! Cold-start-fast single binary; all heavy work is server-side. Endpoint resolves
//! from $MFS_API_URL, else the active profile in ~/.mfs/client.toml, else default.

use clap::{Parser, Subcommand};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::BTreeMap;
use std::path::PathBuf;

#[derive(Parser)]
#[command(name = "mfs", version, about = "Multi-source File-like Search")]
struct Cli {
    #[command(subcommand)]
    cmd: Cmd,
    /// Output raw JSON envelope
    #[arg(long, global = true)]
    json: bool,
}

#[derive(Subcommand)]
enum Cmd {
    /// Register + sync a path or connector URI
    Add {
        target: String,
        /// Connector config TOML (schemas, [[objects]], _credential_ref, ...)
        #[arg(long)]
        config: Option<String>,
        /// Only index changes since this cursor/date (connectors with a time cursor)
        #[arg(long)]
        since: Option<String>,
        /// Force full re-index (ignore caches/fingerprints)
        #[arg(long, visible_alias = "full")]
        force_index: bool,
        /// Block until indexing finishes (poll the job); default returns immediately
        #[arg(long)]
        wait: bool,
        /// Bundle + upload the tree to the server even on the same host (no shared fs)
        #[arg(long)]
        upload: bool,
        /// Re-upload every file (skip the manifest diff) and force a full re-index
        #[arg(long)]
        force_upload: bool,
        /// Never upload; have the server read the path itself (shared fs)
        #[arg(long)]
        no_upload: bool,
        /// Skip the pre-flight estimate/confirm prompt for external connectors
        #[arg(long, short = 'y')]
        yes: bool,
    },
    /// Semantic + keyword search
    Search {
        query: String,
        /// Path/URI to scope the search to (required unless --all)
        path: Option<String>,
        /// Search the whole namespace instead of a scoped path
        #[arg(long)]
        all: bool,
        #[arg(long, default_value = "hybrid")]
        mode: String,
        #[arg(long, default_value_t = 10)]
        top_k: u32,
        /// Restrict to chunk kinds, comma-separated (e.g. body,directory_summary)
        #[arg(long)]
        kind: Option<String>,
        /// Collapse multiple hits from the same object into one
        #[arg(long)]
        collapse: bool,
    },
    /// Keyword / full-text search (pushdown -> BM25 -> linear)
    Grep { pattern: String, path: String },
    /// List children
    Ls { path: String },
    /// Recursively list a subtree
    Tree {
        path: String,
        #[arg(short = 'L', long, default_value_t = 2)]
        depth: u32,
    },
    /// Read an object
    Cat {
        path: String,
        /// Line range, 1-based half-open: `start:end` returns lines start..end-1
        /// (e.g. `--range 1:11` = first 10 lines). Matches `locator.lines` from
        /// search hits.
        #[arg(long)]
        range: Option<String>,
        #[arg(long)]
        meta: bool,
        /// Reopen a single structured record by its locator JSON, e.g. '{"pk":{"id":12}}'
        #[arg(long)]
        locator: Option<String>,
        /// Skeleton view: headings/symbols only
        #[arg(long)]
        peek: bool,
        /// peek + one-line summaries
        #[arg(long)]
        skim: bool,
    },
    /// First N lines of an object
    Head {
        path: String,
        #[arg(short = 'n', long, default_value_t = 20)]
        lines: usize,
    },
    /// Last N lines of an object
    Tail {
        path: String,
        #[arg(short = 'n', long, default_value_t = 20)]
        lines: usize,
    },
    /// Export an object's full content to a file
    Export { path: String, out: String },
    /// Server / connector / job status
    Status,
    /// Job inspection / control
    Job {
        #[command(subcommand)]
        action: JobAction,
    },
    /// Connector management
    Connector {
        #[command(subcommand)]
        action: ConnectorAction,
    },
    /// Remove a connector + everything it owns (alias for `connector remove`)
    Remove {
        target: String,
        /// Skip the confirmation prompt
        #[arg(long, short = 'y')]
        yes: bool,
    },
    /// Client profile (endpoint) management — ~/.mfs/client.toml
    Profile {
        #[command(subcommand)]
        action: ProfileAction,
    },
    /// Show client/server config
    Config {
        #[command(subcommand)]
        action: ConfigAction,
    },
    /// Manage a local mfs-server process
    Serve {
        #[command(subcommand)]
        action: ServeAction,
    },
}

#[derive(Subcommand)]
enum JobAction {
    /// List recent jobs
    List,
    /// Show a job by id
    Show { job_id: String },
    /// Cancel a running/queued job
    Cancel { job_id: String },
}

#[derive(Subcommand)]
enum ConfigAction {
    /// Show resolved endpoint + server info
    Show,
}

#[derive(Subcommand)]
enum ConnectorAction {
    /// Register + sync a connector (alias: `mfs add`)
    Add {
        target: String,
        #[arg(long)]
        config: Option<String>,
    },
    /// Try-connect a connector without registering
    Probe {
        target: String,
        #[arg(long)]
        config: Option<String>,
    },
    /// List registered connectors
    List,
    /// Show a connector's objects/jobs summary
    Inspect { target: String },
    /// Re-sync a connector (alias: `mfs add <uri>`)
    Update {
        target: String,
        #[arg(long)]
        config: Option<String>,
    },
    /// Remove a connector and everything it owns
    Remove {
        target: String,
        /// Skip the confirmation prompt
        #[arg(long, short = 'y')]
        yes: bool,
    },
}

#[derive(Subcommand)]
enum ProfileAction {
    /// List profiles
    List,
    /// Add (or update) a profile
    Add {
        name: String,
        url: String,
        /// Bearer token for remote auth (literal or env:VAR)
        #[arg(long)]
        token: Option<String>,
    },
    /// Set the active profile
    Use { name: String },
}

#[derive(Subcommand)]
enum ServeAction {
    /// Start a local mfs-server (detached)
    Start {
        #[arg(long, default_value = "127.0.0.1:13619")]
        bind: String,
    },
    /// Stop the local mfs-server
    Stop,
    /// Restart the local mfs-server
    Restart {
        #[arg(long, default_value = "127.0.0.1:13619")]
        bind: String,
    },
    /// Is the local mfs-server running?
    Status,
    /// Tail the local server log
    Logs,
}

// ---------- profile (client.toml) ----------
#[derive(Serialize, Deserialize, Default)]
struct ClientConfig {
    active: Option<String>,
    /// Stable client identity (UUID), generated once. Survives hostname/container churn
    /// — machine-id (hostname) is only used to decide local-vs-remote.
    #[serde(default)]
    client_id: Option<String>,
    #[serde(default)]
    profiles: BTreeMap<String, Profile>,
}

/// Load (or generate + persist) the stable client_id from client.toml.
fn client_id() -> String {
    let mut cfg = load_client_cfg();
    if let Some(id) = &cfg.client_id {
        if !id.is_empty() {
            return id.clone();
        }
    }
    let id = std::fs::read_to_string("/proc/sys/kernel/random/uuid")
        .ok()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| format!("cid-{}", std::process::id()));
    cfg.client_id = Some(id.clone());
    let _ = save_client_cfg(&cfg);
    id
}

#[derive(Serialize, Deserialize, Clone)]
struct Profile {
    url: String,
    /// Bearer token for remote auth; literal or `env:VAR`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    token: Option<String>,
}

/// Active Bearer token: $MFS_API_TOKEN, else the active profile's token (env: resolved).
fn auth_token() -> Option<String> {
    if let Ok(t) = std::env::var("MFS_API_TOKEN") {
        if !t.is_empty() {
            return Some(t);
        }
    }
    let cfg = load_client_cfg();
    if let Some(raw) = cfg
        .active
        .as_ref()
        .and_then(|a| cfg.profiles.get(a))
        .and_then(|p| p.token.clone())
    {
        return Some(match raw.strip_prefix("env:") {
            Some(var) => std::env::var(var).unwrap_or_default(),
            None => raw,
        });
    }
    // Fall back to the local server's auto-generated token: `mfs-server
    // run` writes ~/.mfs/server.token, so a CLI on the same host authenticates to its
    // loopback server with zero configuration.
    std::fs::read_to_string(mfs_home().join("server.token"))
        .ok()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
}

/// A remote endpoint (non-loopback) means the server can't see local paths. Rewrite an
/// existing local path to its stable upload identity file://<client_id><abs> so browse/
/// search/remove hit the connector created by `mfs add --upload`.
fn is_remote(base: &str) -> bool {
    !(base.contains("127.0.0.1") || base.contains("localhost") || base.contains("[::1]"))
}

fn remote_path(base: &str, path: &str) -> String {
    if is_remote(base) {
        if let Ok(abs) = std::fs::canonicalize(path) {
            return format!("file://{}{}", client_id(), abs.to_string_lossy());
        }
    }
    path.to_string()
}

fn uploaded_local_path_from_status(
    status: &Value,
    client_id: &str,
    abs_path: &str,
) -> Option<String> {
    let prefix = format!("file://{client_id}");
    let mut best: Option<(usize, String)> = None;
    for connector in status["connectors"].as_array()? {
        let root_uri = connector["root_uri"].as_str().unwrap_or("");
        if !root_uri.starts_with(&prefix) {
            continue;
        }
        let root_path = &root_uri[prefix.len()..];
        let matches = abs_path == root_path
            || abs_path.starts_with(&format!("{}/", root_path.trim_end_matches('/')));
        if !matches {
            continue;
        }
        let suffix = if abs_path == root_path {
            ""
        } else {
            &abs_path[root_path.len()..]
        };
        let mapped = format!("{root_uri}{suffix}");
        if best
            .as_ref()
            .map_or(true, |(len, _)| root_path.len() > *len)
        {
            best = Some((root_path.len(), mapped));
        }
    }
    best.map(|(_, mapped)| mapped)
}

fn resolve_path_arg(client: &reqwest::blocking::Client, base: &str, path: &str) -> String {
    if let Ok(abs) = std::fs::canonicalize(path) {
        let abs_path = abs.to_string_lossy().to_string();
        if let Ok(status) = get(client, &format!("{base}/v1/status"), &[]) {
            if let Some(mapped) = uploaded_local_path_from_status(&status, &client_id(), &abs_path)
            {
                return mapped;
            }
        }
    }
    remote_path(base, path)
}

fn with_auth(rb: reqwest::blocking::RequestBuilder) -> reqwest::blocking::RequestBuilder {
    match auth_token() {
        Some(t) if !t.is_empty() => rb.bearer_auth(t),
        _ => rb,
    }
}

fn mfs_home() -> PathBuf {
    let home = std::env::var("MFS_HOME")
        .or_else(|_| std::env::var("HOME").map(|h| format!("{h}/.mfs")))
        .unwrap_or_else(|_| ".mfs".to_string());
    PathBuf::from(home)
}

fn client_cfg_path() -> PathBuf {
    mfs_home().join("client.toml")
}

fn load_client_cfg() -> ClientConfig {
    let p = client_cfg_path();
    std::fs::read_to_string(p)
        .ok()
        .and_then(|s| toml::from_str(&s).ok())
        .unwrap_or_default()
}

fn save_client_cfg(cfg: &ClientConfig) -> Result<(), String> {
    let dir = mfs_home();
    std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    let s = toml::to_string_pretty(cfg).map_err(|e| e.to_string())?;
    std::fs::write(client_cfg_path(), s).map_err(|e| e.to_string())
}

fn base_url() -> String {
    if let Ok(u) = std::env::var("MFS_API_URL") {
        return u;
    }
    let cfg = load_client_cfg();
    if let Some(active) = &cfg.active {
        if let Some(p) = cfg.profiles.get(active) {
            return p.url.clone();
        }
    }
    "http://127.0.0.1:13619".to_string()
}

fn main() {
    let cli = Cli::parse();
    let client = reqwest::blocking::Client::new();
    let base = base_url();
    if let Err(e) = run(&cli, &client, &base) {
        eprintln!("error: {e}");
        std::process::exit(1);
    }
}

fn run(cli: &Cli, client: &reqwest::blocking::Client, base: &str) -> Result<(), String> {
    match &cli.cmd {
        Cmd::Add {
            target,
            config,
            since,
            force_index,
            wait,
            upload,
            force_upload,
            no_upload,
            yes,
        } => {
            let is_local = std::path::Path::new(target).exists();
            // Make a bare/relative local path absolute CLIENT-side before sending: a
            // loopback server resolves a relative path against its OWN cwd (not the user's),
            // so `mfs add ./repo` would 500 with a server-side FileNotFoundError. Canonicalizing
            // to the stable file://local<abs> identity also keeps search/cat/remove consistent.
            let canon_target: String = if is_local {
                std::fs::canonicalize(target)
                    .map(|p| p.to_string_lossy().into_owned())
                    .unwrap_or_else(|_| target.clone())
            } else {
                target.clone()
            };
            let target = &canon_target;
            // zero-billing estimate + confirm before indexing an external connector
            //: the user sees physical work before any embedding spend.
            if !is_local && !yes {
                let mut eb = serde_json::json!({"target": target});
                if let Some(c) = config {
                    eb["config"] = load_config_file(c)?;
                }
                let est = post(client, &format!("{base}/v1/connectors/estimate"), &eb)?;
                println!("Connector: {target}");
                println!("Discovered: {} objects", est["objects"]);
                println!("Estimated (local chunker + tokenizer only — no embedding API calls):");
                println!("  chunks: ~{}", est["est_chunks"]);
                println!(
                    "  tokens: ~{}  (apply your provider's per-token rate to estimate $)",
                    est["est_tokens"]
                );
                if !confirm("Continue? [y/N] ")? {
                    println!("aborted.");
                    return Ok(());
                }
            }
            // local/remote decision: when the target is a real local path
            // and the server runs on a different host (no shared fs), bundle the tree and
            // upload it instead of asking the server to read a path it can't see. --upload
            // forces it on the same host; --no-upload always has the server read the path.
            let do_upload = if *no_upload {
                false
            } else if *upload || *force_upload {
                is_local
            } else if is_local {
                let server_mid = get(client, &format!("{base}/v1/server/info"), &[])
                    .ok()
                    .and_then(|v| v["machine_id"].as_str().map(String::from))
                    .unwrap_or_default();
                let client_host = client_hostname();
                !server_mid.is_empty() && !client_host.is_empty() && server_mid != client_host
            } else {
                false
            };
            // add is async: the server enqueues and returns a job_id immediately; the
            // worker (in-process for the single-binary, dedicated in CS) drains it in the
            // background. --wait polls the job to completion for scripts/CI that must block.
            let job_id = if do_upload {
                // --force-upload re-sends every file AND forces a re-index; --force-index
                // alone re-indexes the already-staged tree without re-sending bytes.
                upload_path(
                    client,
                    base,
                    target,
                    *force_index || *force_upload,
                    *force_upload,
                    cli.json,
                )?
            } else {
                let mut body =
                    serde_json::json!({"target": target, "full": force_index, "process": false});
                if let Some(c) = config {
                    body["config"] = load_config_file(c)?;
                }
                if let Some(s) = since {
                    body["since"] = Value::String(s.clone());
                }
                let v = post(client, &format!("{base}/v1/add"), &body)?;
                v["job_id"].as_str().unwrap_or("").to_string()
            };
            if *wait {
                wait_for_job(client, base, &job_id, cli.json)?;
            } else if cli.json {
                println!("{}", serde_json::json!({"job_id": job_id}));
            } else {
                println!("queued (job {job_id}). Worker running in background — run `mfs status` to check progress.");
            }
        }
        Cmd::Search {
            query,
            path,
            all,
            mode,
            top_k,
            kind,
            collapse,
        } => {
            if path.is_none() && !all {
                return Err(
                    "specify a path to scope the search, or --all for the whole namespace".into(),
                );
            }
            let mut q = vec![
                ("q", query.clone()),
                ("mode", mode.clone()),
                ("top_k", top_k.to_string()),
            ];
            if let Some(p) = path {
                q.push(("path", resolve_path_arg(client, base, p)));
            }
            if let Some(k) = kind {
                q.push(("kind", k.clone()));
            }
            if *collapse {
                q.push(("collapse", "true".to_string()));
            }
            let v = get(client, &format!("{base}/v1/search"), &q)?;
            if cli.json {
                println!("{v}");
                return Ok(());
            }
            for hit in v["results"].as_array().unwrap_or(&vec![]) {
                println!(
                    "{}  score={}",
                    hit["source"].as_str().unwrap_or("?"),
                    hit["score"].as_f64().unwrap_or(0.0)
                );
                if let Some(c) = hit["content"].as_str() {
                    println!(
                        "   {}",
                        c.lines()
                            .next()
                            .unwrap_or("")
                            .chars()
                            .take(100)
                            .collect::<String>()
                    );
                }
            }
        }
        Cmd::Grep { pattern, path } => {
            let v = get(
                client,
                &format!("{base}/v1/grep"),
                &[
                    ("pattern", pattern.clone()),
                    ("path", resolve_path_arg(client, base, path)),
                ],
            )?;
            if cli.json {
                println!("{v}");
                return Ok(());
            }
            for hit in v["results"].as_array().unwrap_or(&vec![]) {
                println!(
                    "{}: {}",
                    hit["source"].as_str().unwrap_or("?"),
                    hit["content"]
                        .as_str()
                        .unwrap_or("")
                        .chars()
                        .take(120)
                        .collect::<String>()
                );
            }
        }
        Cmd::Ls { path } => {
            let v = get(
                client,
                &format!("{base}/v1/ls"),
                &[("path", resolve_path_arg(client, base, path))],
            )?;
            if cli.json {
                println!("{v}");
                return Ok(());
            }
            print_entries(&v);
        }
        Cmd::Tree { path, depth } => {
            let rp = resolve_path_arg(client, base, path);
            if cli.json {
                let v = tree_json(client, base, &rp, *depth)?;
                println!("{v}");
                return Ok(());
            }
            println!("{path}");
            tree(client, base, &rp, *depth, "")?;
        }
        Cmd::Cat {
            path,
            range,
            meta,
            locator,
            peek,
            skim,
        } => {
            let mut q = vec![("path", resolve_path_arg(client, base, path))];
            if let Some(r) = range {
                q.push(("range", r.clone()));
            }
            if *meta {
                q.push(("meta", "true".to_string()));
            }
            if let Some(l) = locator {
                q.push(("locator", l.clone()));
            }
            if *peek {
                q.push(("density", "peek".to_string()));
            }
            if *skim {
                q.push(("density", "skim".to_string()));
            }
            let v = get(client, &format!("{base}/v1/cat"), &q)?;
            if cli.json {
                println!("{v}");
                return Ok(());
            }
            if *meta {
                println!("{v}");
            } else {
                println!("{}", v["content"].as_str().unwrap_or(""));
            }
        }
        Cmd::Head { path, lines } => {
            let v = get(
                client,
                &format!("{base}/v1/head"),
                &[
                    ("path", resolve_path_arg(client, base, path)),
                    ("n", lines.to_string()),
                ],
            )?;
            if cli.json {
                println!("{v}");
            } else {
                println!("{}", v["content"].as_str().unwrap_or(""));
            }
        }
        Cmd::Tail { path, lines } => {
            let v = get(
                client,
                &format!("{base}/v1/tail"),
                &[
                    ("path", resolve_path_arg(client, base, path)),
                    ("n", lines.to_string()),
                ],
            )?;
            if cli.json {
                println!("{v}");
            } else {
                println!("{}", v["content"].as_str().unwrap_or(""));
            }
        }
        Cmd::Export { path, out } => {
            // export returns the FULL object (no bare-cat size guard), but each
            // connector's own row cap still applies — surface partial=true so
            // the caller knows the file on disk is not the complete object.
            let v = get(
                client,
                &format!("{base}/v1/export"),
                &[("path", resolve_path_arg(client, base, path))],
            )?;
            let text = v["content"].as_str().unwrap_or("");
            let partial = v["partial"].as_bool().unwrap_or(false);
            std::fs::write(out, text).map_err(|e| e.to_string())?;
            println!("exported {} bytes -> {}", text.len(), out);
            if partial {
                println!(
                    "warning: partial export — connector capped at max_read_rows; \
                     raise the cap or use `mfs cat --range` to page the rest"
                );
            }
        }
        Cmd::Status => {
            let v = get(client, &format!("{base}/v1/status"), &[])?;
            println!("{}", serde_json::to_string_pretty(&v).unwrap_or_default());
        }
        Cmd::Job { action } => match action {
            JobAction::List => {
                let v = get(client, &format!("{base}/v1/jobs"), &[])?;
                if cli.json {
                    println!("{v}");
                    return Ok(());
                }
                for j in v.as_array().unwrap_or(&vec![]) {
                    println!(
                        "{:8}  {:10}  {}",
                        j["status"].as_str().unwrap_or("?"),
                        j["op_kind"].as_str().unwrap_or("?"),
                        j["id"].as_str().unwrap_or("?")
                    );
                }
            }
            JobAction::Show { job_id } => {
                let v = get(client, &format!("{base}/v1/jobs/{job_id}"), &[])?;
                println!("{}", serde_json::to_string_pretty(&v).unwrap_or_default());
            }
            JobAction::Cancel { job_id } => {
                let v = post(
                    client,
                    &format!("{base}/v1/jobs/{job_id}/cancel"),
                    &serde_json::json!({}),
                )?;
                println!("cancelled: {}", v["cancelled"].as_bool().unwrap_or(false));
            }
        },
        Cmd::Connector { action } => match action {
            ConnectorAction::Add { target, config } => {
                let mut body = serde_json::json!({"target": target});
                if let Some(c) = config {
                    body["config"] = load_config_file(c)?;
                }
                let v = post(client, &format!("{base}/v1/add"), &body)?;
                println!("job: {}", v["job_id"].as_str().unwrap_or("?"));
            }
            ConnectorAction::Update { target, config } => {
                // update applies the new config to the existing connector (add ignores --config)
                let mut body = serde_json::json!({"target": target, "update": true});
                if let Some(c) = config {
                    body["config"] = load_config_file(c)?;
                }
                let v = post(client, &format!("{base}/v1/add"), &body)?;
                println!("job: {}", v["job_id"].as_str().unwrap_or("?"));
            }
            ConnectorAction::Probe { target, config } => {
                let mut body = serde_json::json!({"target": target});
                if let Some(c) = config {
                    body["config"] = load_config_file(c)?;
                }
                let v = post(client, &format!("{base}/v1/connectors/probe"), &body)?;
                println!(
                    "{}  ok={}  {}",
                    v["type"].as_str().unwrap_or("?"),
                    v["ok"].as_bool().unwrap_or(false),
                    v["detail"].as_str().unwrap_or("")
                );
            }
            ConnectorAction::List => {
                let v = get(client, &format!("{base}/v1/status"), &[])?;
                if cli.json {
                    println!("{}", v["connectors"]);
                    return Ok(());
                }
                for c in v["connectors"].as_array().unwrap_or(&vec![]) {
                    println!(
                        "{:10}  {:8}  {}  ({} objects, {} chunks)",
                        c["type"].as_str().unwrap_or("?"),
                        c["status"].as_str().unwrap_or("?"),
                        c["root_uri"].as_str().unwrap_or("?"),
                        c["object_count"].as_u64().unwrap_or(0),
                        c["chunk_count"].as_u64().unwrap_or(0)
                    );
                }
            }
            ConnectorAction::Inspect { target } => {
                let v = get(
                    client,
                    &format!("{base}/v1/connectors/inspect"),
                    &[("target", target.clone())],
                )?;
                println!("{}", serde_json::to_string_pretty(&v).unwrap_or_default());
            }
            ConnectorAction::Remove { target, yes } => {
                return remove_connector(client, base, target, *yes, cli.json)
            }
        },
        Cmd::Remove { target, yes } => {
            return remove_connector(client, base, target, *yes, cli.json)
        }
        Cmd::Profile { action } => return profile_cmd(action),
        Cmd::Config { action } => match action {
            ConfigAction::Show => {
                println!("endpoint: {base}");
                let cfg = load_client_cfg();
                println!(
                    "active profile: {}",
                    cfg.active.as_deref().unwrap_or("(none)")
                );
                println!("client_id: {}", client_id());
                match get(client, &format!("{base}/v1/server/info"), &[]) {
                    Ok(v) => println!("server: {}", serde_json::to_string(&v).unwrap_or_default()),
                    Err(e) => println!("server: unreachable ({e})"),
                }
            }
        },
        Cmd::Serve { action } => return serve_cmd(action),
    }
    Ok(())
}

/// Prompt on stderr/stdout and read a yes/no from stdin.
fn confirm(prompt: &str) -> Result<bool, String> {
    use std::io::Write;
    print!("{prompt}");
    std::io::stdout().flush().ok();
    let mut s = String::new();
    std::io::stdin()
        .read_line(&mut s)
        .map_err(|e| e.to_string())?;
    Ok(matches!(s.trim().to_lowercase().as_str(), "y" | "yes"))
}

/// Best-effort client machine id (matches the server's socket.gethostname()).
fn client_hostname() -> String {
    std::process::Command::new("hostname")
        .output()
        .ok()
        .and_then(|o| String::from_utf8(o.stdout).ok())
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .or_else(|| std::env::var("HOSTNAME").ok())
        .unwrap_or_default()
}

/// One file's stat from the client scan.
struct ScanEntry {
    rel: String,
    size: u64,
    mtime_ns: i64,
    inode: u64,
}

/// Walk a directory (skipping noisy dirs) collecting per-file stat (rel path, size,
/// mtime_ns, inode). Mirrors the server file connector's default ignores roughly.
fn scan_tree(root: &std::path::Path) -> Result<Vec<ScanEntry>, String> {
    use std::os::unix::fs::MetadataExt;
    const SKIP: &[&str] = &[
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".idea",
        ".vscode",
    ];
    let mut out = Vec::new();
    let mut stack = vec![root.to_path_buf()];
    while let Some(dir) = stack.pop() {
        let rd = std::fs::read_dir(&dir).map_err(|e| format!("scan {}: {e}", dir.display()))?;
        for ent in rd {
            let ent = ent.map_err(|e| e.to_string())?;
            let path = ent.path();
            let md = ent.metadata().map_err(|e| e.to_string())?;
            let name = ent.file_name().to_string_lossy().to_string();
            if md.is_dir() {
                if !SKIP.contains(&name.as_str()) {
                    stack.push(path);
                }
            } else if md.is_file() {
                let rel = path
                    .strip_prefix(root)
                    .map_err(|e| e.to_string())?
                    .to_string_lossy()
                    .replace('\\', "/");
                out.push(ScanEntry {
                    rel,
                    size: md.size(),
                    // ns since epoch fits i64 until year ~2262; send as a JSON number so it
                    // round-trips to the server's int field and stat-compares cleanly next sync
                    mtime_ns: md.mtime() * 1_000_000_000 + md.mtime_nsec(),
                    inode: md.ino(),
                });
            }
        }
    }
    Ok(out)
}

fn sha1_file(path: &std::path::Path) -> Result<String, String> {
    use sha1::{Digest, Sha1};
    let bytes = std::fs::read(path).map_err(|e| e.to_string())?;
    let mut h = Sha1::new();
    h.update(&bytes);
    Ok(format!("{:x}", h.finalize()))
}

/// Manifest-diff upload: scan -> POST /v1/files/manifest -> sha1 the
/// needed files + pair renames by (inode,size,sha1) -> PUT /v1/files/upload a tar.gz
/// carrying `.mfs-meta.json` + only the changed bytes. The client keeps no state.
fn upload_path(
    client: &reqwest::blocking::Client,
    base: &str,
    target: &str,
    full: bool,
    resend_all: bool,
    json: bool,
) -> Result<String, String> {
    use std::io::Write;
    let root = std::path::Path::new(target);
    let client_id = client_id(); // stable UUID identity, not the hostname
                                 // absolute path is the connector's stable identity file://<client_id><abs>
    let abs_root = std::fs::canonicalize(root)
        .map_err(|e| e.to_string())?
        .to_string_lossy()
        .to_string();

    // ① scan (stat only)
    let entries = scan_tree(root)?;
    let files: Vec<Value> = entries
        .iter()
        .map(|e| {
            serde_json::json!(
        {"path": e.rel, "size": e.size, "mtime_ns": e.mtime_ns, "inode": e.inode})
        })
        .collect();

    // ② manifest diff
    let mf = post(
        client,
        &format!("{base}/v1/files/manifest"),
        &serde_json::json!({"client_id": client_id, "root": abs_root, "files": files}),
    )?;
    // --force-upload (resend_all): ignore the server's diff and re-send every file's bytes.
    let need: std::collections::HashSet<String> = if resend_all {
        entries.iter().map(|e| e.rel.clone()).collect()
    } else {
        mf["need_sha1"]
            .as_array()
            .unwrap_or(&vec![])
            .iter()
            .filter_map(|v| v.as_str().map(String::from))
            .collect()
    };
    let del_cands = mf["deletion_candidates"]
        .as_array()
        .cloned()
        .unwrap_or_default();

    // ③ sha1 the needed files; pair renames against deletion candidates (inode+size+sha1)
    let by_rel: std::collections::HashMap<&str, &ScanEntry> =
        entries.iter().map(|e| (e.rel.as_str(), e)).collect();
    let mut hashes: Vec<Value> = Vec::new();
    let mut sha_of: std::collections::HashMap<String, String> = std::collections::HashMap::new();
    for rel in &need {
        let e = by_rel[rel.as_str()];
        let sha = sha1_file(&root.join(rel))?;
        sha_of.insert(rel.clone(), sha.clone());
        hashes.push(serde_json::json!(
            {"path": rel, "sha1": sha, "size": e.size, "mtime_ns": e.mtime_ns, "inode": e.inode}));
    }
    let mut renames: Vec<Value> = Vec::new();
    let mut consumed_old: std::collections::HashSet<String> = std::collections::HashSet::new();
    let mut renamed_new: std::collections::HashSet<String> = std::collections::HashSet::new();
    for rel in &need {
        let e = by_rel[rel.as_str()];
        let sha = &sha_of[rel];
        for dc in &del_cands {
            let old = dc["path"].as_str().unwrap_or("");
            if old.is_empty() || consumed_old.contains(old) {
                continue;
            }
            // inode+size first; fall back to sha1+size so a cross-filesystem rename (inode
            // changes) is still recognized as a rename instead of delete+add — which would
            // re-upload and re-embed identical bytes.
            let size_match = dc["size"].as_u64() == Some(e.size);
            let same = size_match
                && (dc["inode"].as_u64() == Some(e.inode)
                    || dc["sha1"].as_str() == Some(sha.as_str()));
            if same {
                renames.push(serde_json::json!({"old": old, "new": rel, "sha1": sha}));
                consumed_old.insert(old.to_string());
                renamed_new.insert(rel.clone());
                break;
            }
        }
    }
    let deletions: Vec<String> = del_cands
        .iter()
        .filter_map(|dc| dc["path"].as_str().map(String::from))
        .filter(|p| !consumed_old.contains(p))
        .collect();

    // ④ build tar.gz: .mfs-meta.json + changed bytes (renamed files send no bytes)
    let meta = serde_json::json!({"hashes": hashes, "renames": renames, "deletions": deletions});
    let buf = Vec::new();
    let enc = flate2::write::GzEncoder::new(buf, flate2::Compression::default());
    let mut tar = tar::Builder::new(enc);
    let meta_bytes = serde_json::to_vec(&meta).map_err(|e| e.to_string())?;
    let mut hdr = tar::Header::new_gnu();
    hdr.set_size(meta_bytes.len() as u64);
    hdr.set_mode(0o644);
    hdr.set_cksum();
    tar.append_data(&mut hdr, ".mfs-meta.json", &meta_bytes[..])
        .map_err(|e| e.to_string())?;
    for rel in &need {
        if !resend_all && renamed_new.contains(rel) {
            continue;
        } // moved on server, no bytes
        tar.append_path_with_name(root.join(rel), rel)
            .map_err(|e| e.to_string())?;
    }
    let gz = tar.into_inner().map_err(|e| e.to_string())?;
    let data = gz.finish().map_err(|e| e.to_string())?;
    let _ = std::io::stdout().flush();

    let resp = with_auth(
        client
            .put(format!("{base}/v1/files/upload"))
            .query(&[
                ("client_id", client_id.as_str()),
                ("root", abs_root.as_str()),
                ("process", "false"),
                ("full", &full.to_string()),
            ])
            .body(data),
    )
    .send()
    .map_err(|e| e.to_string())?;
    let v = parse(resp)?;
    if !json {
        println!(
            "uploaded {} changed, {} renamed, {} deleted",
            need.len() - renamed_new.len(),
            renames.len(),
            deletions.len()
        );
    }
    Ok(v["job_id"].as_str().unwrap_or("").to_string())
}

/// Poll a sync job to a terminal state (for `mfs add --wait`). The HTTP request itself is
/// short — we never hold a long connection open, so a slow index can't time the client out.
fn wait_for_job(
    client: &reqwest::blocking::Client,
    base: &str,
    job_id: &str,
    json: bool,
) -> Result<(), String> {
    let v = poll_job_until_done(
        || get_job(client, base, job_id),
        || std::thread::sleep(std::time::Duration::from_millis(1000)),
    )?;
    if json {
        println!("{v}");
    } else {
        println!(
            "done: {} of {} objects indexed, {} failed",
            v["succeeded_objects"].as_i64().unwrap_or(0),
            v["total_objects"].as_i64().unwrap_or(0),
            v["failed_objects"].as_i64().unwrap_or(0)
        );
    }
    Ok(())
}

const JOB_WAIT_MAX_TRANSIENT_ERRORS: usize = 300;

#[derive(Debug)]
enum JobPollError {
    Transient(String),
    Terminal(String),
}

fn get_job(
    client: &reqwest::blocking::Client,
    base: &str,
    job_id: &str,
) -> Result<Value, JobPollError> {
    let resp = with_auth(client.get(format!("{base}/v1/jobs/{job_id}")))
        .send()
        .map_err(|e| JobPollError::Transient(e.to_string()))?;
    parse(resp).map_err(JobPollError::Terminal)
}

fn poll_job_until_done<F, S>(mut poll: F, mut sleep: S) -> Result<Value, String>
where
    F: FnMut() -> Result<Value, JobPollError>,
    S: FnMut(),
{
    let mut transient_errors = 0;
    loop {
        let v = match poll() {
            Ok(v) => {
                transient_errors = 0;
                v
            }
            Err(JobPollError::Transient(e)) => {
                transient_errors += 1;
                if transient_errors > JOB_WAIT_MAX_TRANSIENT_ERRORS {
                    return Err(format!(
                        "job poll failed after {JOB_WAIT_MAX_TRANSIENT_ERRORS} retries: {e}"
                    ));
                }
                sleep();
                continue;
            }
            Err(JobPollError::Terminal(e)) => return Err(e),
        };
        match v["status"].as_str().unwrap_or("") {
            "succeeded" => return Ok(v),
            "failed" | "cancelled" => {
                return Err(format!(
                    "job {}: {}",
                    v["status"].as_str().unwrap_or("?"),
                    v["error"].as_str().unwrap_or("")
                ));
            }
            _ => sleep(),
        }
    }
}

/// Load a connector config TOML file and convert it to a JSON value for the /v1/add body.
fn load_config_file(path: &str) -> Result<Value, String> {
    let text = std::fs::read_to_string(path).map_err(|e| format!("read {path}: {e}"))?;
    let toml_val: toml::Value = toml::from_str(&text).map_err(|e| format!("parse {path}: {e}"))?;
    serde_json::to_value(toml_val).map_err(|e| e.to_string())
}

fn remove_connector(
    client: &reqwest::blocking::Client,
    base: &str,
    target: &str,
    yes: bool,
    json: bool,
) -> Result<(), String> {
    // remove is destructive (drops the index, artifacts, and all metadata); confirm unless -y.
    if !yes
        && !confirm(&format!(
            "Remove connector '{target}' and everything it owns? [y/N] "
        ))?
    {
        if json {
            println!("{}", serde_json::json!({"removed": false, "aborted": true}));
        } else {
            println!("aborted.");
        }
        return Ok(());
    }
    let target = resolve_path_arg(client, base, target); // local path -> upload identity when available
    let resp = with_auth(
        client
            .delete(format!("{base}/v1/connectors"))
            .query(&[("target", target.as_str())]),
    )
    .send()
    .map_err(|e| e.to_string())?;
    let v = parse(resp)?;
    println!("{}", remove_output(&v, json));
    Ok(())
}

fn remove_output(v: &Value, json: bool) -> String {
    if json {
        v.to_string()
    } else {
        format!("removed: {}", v["removed"].as_bool().unwrap_or(false))
    }
}

fn print_entries(v: &Value) {
    for e in v["entries"].as_array().unwrap_or(&vec![]) {
        println!(
            "{:4}  {}",
            e["type"].as_str().unwrap_or(""),
            e["name"].as_str().unwrap_or("")
        );
    }
}

fn tree(
    client: &reqwest::blocking::Client,
    base: &str,
    path: &str,
    depth: u32,
    prefix: &str,
) -> Result<(), String> {
    if depth == 0 {
        return Ok(());
    }
    let v = get(
        client,
        &format!("{base}/v1/ls"),
        &[("path", path.to_string())],
    )?;
    let entries = v["entries"].as_array().cloned().unwrap_or_default();
    let n = entries.len();
    for (i, e) in entries.iter().enumerate() {
        let name = e["name"].as_str().unwrap_or("");
        let is_dir = e["type"].as_str() == Some("dir");
        let last = i + 1 == n;
        let branch = if last { "└── " } else { "├── " };
        println!("{prefix}{branch}{name}{}", if is_dir { "/" } else { "" });
        if is_dir {
            let child = format!("{}/{}", path.trim_end_matches('/'), name);
            let next_prefix = format!("{prefix}{}", if last { "    " } else { "│   " });
            tree(client, base, &child, depth - 1, &next_prefix)?;
        }
    }
    Ok(())
}

fn tree_json(
    client: &reqwest::blocking::Client,
    base: &str,
    path: &str,
    depth: u32,
) -> Result<Value, String> {
    let mut root = get(
        client,
        &format!("{base}/v1/ls"),
        &[("path", path.to_string())],
    )?;
    let entries = root["entries"].as_array().cloned().unwrap_or_default();
    if depth == 0 {
        root["entries"] = Value::Array(Vec::new());
        return Ok(root);
    }
    let mut fetch_child = |child_path: &str| {
        get(
            client,
            &format!("{base}/v1/ls"),
            &[("path", child_path.to_string())],
        )
    };
    root["entries"] = Value::Array(expand_tree_entries(entries, depth, &mut fetch_child)?);
    Ok(root)
}

fn expand_tree_entries<F>(
    entries: Vec<Value>,
    depth: u32,
    fetch_child: &mut F,
) -> Result<Vec<Value>, String>
where
    F: FnMut(&str) -> Result<Value, String>,
{
    if depth == 0 {
        return Ok(Vec::new());
    }

    let mut expanded = Vec::with_capacity(entries.len());
    for mut entry in entries {
        let is_dir = entry["type"].as_str() == Some("dir");
        if is_dir && depth > 1 {
            let children = if let Some(child_path) = entry["path"].as_str() {
                let child = fetch_child(child_path)?;
                let child_entries = child["entries"].as_array().cloned().unwrap_or_default();
                expand_tree_entries(child_entries, depth - 1, fetch_child)?
            } else {
                Vec::new()
            };
            entry["children"] = Value::Array(children);
        }
        expanded.push(entry);
    }
    Ok(expanded)
}

fn profile_cmd(action: &ProfileAction) -> Result<(), String> {
    let mut cfg = load_client_cfg();
    match action {
        ProfileAction::List => {
            for (name, p) in &cfg.profiles {
                let marker = if cfg.active.as_deref() == Some(name) {
                    "*"
                } else {
                    " "
                };
                println!("{marker} {name:12} {}", p.url);
            }
            if cfg.profiles.is_empty() {
                println!("(no profiles; using {})", base_url());
            }
        }
        ProfileAction::Add { name, url, token } => {
            cfg.profiles.insert(
                name.clone(),
                Profile {
                    url: url.clone(),
                    token: token.clone(),
                },
            );
            if cfg.active.is_none() {
                cfg.active = Some(name.clone());
            }
            save_client_cfg(&cfg)?;
            println!(
                "profile '{name}' -> {url}{}",
                if token.is_some() { " (token set)" } else { "" }
            );
        }
        ProfileAction::Use { name } => {
            if !cfg.profiles.contains_key(name) {
                return Err(format!("no such profile: {name}"));
            }
            cfg.active = Some(name.clone());
            save_client_cfg(&cfg)?;
            println!("active profile: {name}");
        }
    }
    Ok(())
}

fn serve_cmd(action: &ServeAction) -> Result<(), String> {
    let pid_file = mfs_home().join("server.pid");
    let log_file = mfs_home().join("server.log");
    match action {
        ServeAction::Start { bind } => {
            if let Some(pid) = read_pid(&pid_file) {
                if pid_alive(pid) {
                    println!("already running (pid {pid})");
                    return Ok(());
                }
            }
            std::fs::create_dir_all(mfs_home()).map_err(|e| e.to_string())?;
            let log = std::fs::File::create(&log_file).map_err(|e| e.to_string())?;
            let log_err = log.try_clone().map_err(|e| e.to_string())?;
            let child = std::process::Command::new("mfs-server")
                .args(["run", "--bind", bind])
                .stdout(std::process::Stdio::from(log))
                .stderr(std::process::Stdio::from(log_err))
                .spawn()
                .map_err(|e| format!("failed to spawn mfs-server: {e}"))?;
            std::fs::write(&pid_file, child.id().to_string()).map_err(|e| e.to_string())?;
            println!(
                "started mfs-server (pid {}) on {bind}; logs: {}",
                child.id(),
                log_file.display()
            );
        }
        ServeAction::Stop => match read_pid(&pid_file) {
            Some(pid) => {
                let _ = std::process::Command::new("kill")
                    .arg(pid.to_string())
                    .status();
                let _ = std::fs::remove_file(&pid_file);
                println!("stopped (pid {pid})");
            }
            None => println!("not running"),
        },
        ServeAction::Restart { bind } => {
            if let Some(pid) = read_pid(&pid_file) {
                let _ = std::process::Command::new("kill")
                    .arg(pid.to_string())
                    .status();
                let _ = std::fs::remove_file(&pid_file);
            }
            return serve_cmd(&ServeAction::Start { bind: bind.clone() });
        }
        ServeAction::Status => match read_pid(&pid_file) {
            Some(pid) if pid_alive(pid) => println!("running (pid {pid})"),
            _ => println!("not running"),
        },
        ServeAction::Logs => {
            let s = std::fs::read_to_string(&log_file).unwrap_or_default();
            for l in s
                .lines()
                .rev()
                .take(40)
                .collect::<Vec<_>>()
                .into_iter()
                .rev()
            {
                println!("{l}");
            }
        }
    }
    Ok(())
}

fn read_pid(p: &PathBuf) -> Option<u32> {
    std::fs::read_to_string(p)
        .ok()
        .and_then(|s| s.trim().parse().ok())
}

fn pid_alive(pid: u32) -> bool {
    std::process::Command::new("kill")
        .args(["-0", &pid.to_string()])
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
}

fn get(
    client: &reqwest::blocking::Client,
    url: &str,
    q: &[(&str, String)],
) -> Result<Value, String> {
    let resp = with_auth(client.get(url).query(q))
        .send()
        .map_err(|e| e.to_string())?;
    parse(resp)
}

fn post(client: &reqwest::blocking::Client, url: &str, body: &Value) -> Result<Value, String> {
    let resp = with_auth(client.post(url).json(body))
        .send()
        .map_err(|e| e.to_string())?;
    parse(resp)
}

fn parse(resp: reqwest::blocking::Response) -> Result<Value, String> {
    let status = resp.status();
    let v: Value = resp.json().map_err(|e| e.to_string())?;
    if !status.is_success() {
        // The server returns a stable {code, detail, suggestions: [...]} envelope
        // for every error (see api/app.py _http_exc). Surface all three so users
        // see the recovery hint without having to read --json output.
        let code = v.get("code").and_then(|c| c.as_str()).unwrap_or("");
        let detail = v
            .get("detail")
            .and_then(|d| d.as_str())
            .unwrap_or("request failed");
        let suggestions: Vec<&str> = v
            .get("suggestions")
            .and_then(|s| s.as_array())
            .map(|arr| arr.iter().filter_map(|x| x.as_str()).collect())
            .unwrap_or_default();
        let header = if code.is_empty() {
            format!("{status}: {detail}")
        } else {
            format!("{status} [{code}]: {detail}")
        };
        return Err(if suggestions.is_empty() {
            header
        } else {
            format!("{header}\n  try: {}", suggestions.join(", "))
        });
    }
    Ok(v)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn expand_tree_entries_adds_nested_children_until_depth_limit() {
        let root_entries = vec![
            json!({"name": "README.md", "path": "file://root/README.md", "type": "file"}),
            json!({"name": "notes", "path": "file://root/notes", "type": "dir"}),
        ];
        let mut fetch_child = |path: &str| match path {
            "file://root/notes" => Ok(json!({
                "entries": [
                    {"name": "search.md", "path": "file://root/notes/search.md", "type": "file"}
                ]
            })),
            _ => Err(format!("unexpected path {path}")),
        };

        let expanded = expand_tree_entries(root_entries, 2, &mut fetch_child).unwrap();

        assert_eq!(expanded[0].get("children"), None);
        assert_eq!(
            expanded[1]["children"][0]["path"],
            "file://root/notes/search.md"
        );
    }

    #[test]
    fn expand_tree_entries_respects_depth_one() {
        let root_entries =
            vec![json!({"name": "notes", "path": "file://root/notes", "type": "dir"})];
        let mut fetch_child = |_path: &str| -> Result<Value, String> {
            panic!("depth one should not fetch child directories")
        };

        let expanded = expand_tree_entries(root_entries, 1, &mut fetch_child).unwrap();

        assert_eq!(expanded[0].get("children"), None);
    }

    #[test]
    fn remove_output_honors_json_flag() {
        let response = json!({"removed": true});

        assert_eq!(remove_output(&response, false), "removed: true");
        assert_eq!(
            serde_json::from_str::<Value>(&remove_output(&response, true)).unwrap(),
            response
        );
    }

    #[test]
    fn poll_job_retries_transient_errors_until_success() {
        let mut calls = 0;
        let mut sleeps = 0;

        let result = poll_job_until_done(
            || {
                calls += 1;
                if calls < 3 {
                    Err(JobPollError::Transient("connection reset".to_string()))
                } else {
                    Ok(json!({
                        "status": "succeeded",
                        "succeeded_objects": 3,
                        "total_objects": 3,
                        "failed_objects": 0
                    }))
                }
            },
            || {
                sleeps += 1;
            },
        )
        .unwrap();

        assert_eq!(result["status"], "succeeded");
        assert_eq!(calls, 3);
        assert_eq!(sleeps, 2);
    }

    #[test]
    fn poll_job_does_not_retry_terminal_errors() {
        let mut sleeps = 0;

        let err = poll_job_until_done(
            || Err(JobPollError::Terminal("401 [unauthorized]".to_string())),
            || {
                sleeps += 1;
            },
        )
        .unwrap_err();

        assert_eq!(err, "401 [unauthorized]");
        assert_eq!(sleeps, 0);
    }

    #[test]
    fn uploaded_local_path_maps_exact_root_and_child() {
        let status = json!({
            "connectors": [
                {"root_uri": "file://local/tmp/project"},
                {"root_uri": "file://cid-1/tmp/project"}
            ]
        });

        assert_eq!(
            uploaded_local_path_from_status(&status, "cid-1", "/tmp/project"),
            Some("file://cid-1/tmp/project".to_string())
        );
        assert_eq!(
            uploaded_local_path_from_status(&status, "cid-1", "/tmp/project/src/lib.rs"),
            Some("file://cid-1/tmp/project/src/lib.rs".to_string())
        );
    }

    #[test]
    fn uploaded_local_path_prefers_longest_matching_root() {
        let status = json!({
            "connectors": [
                {"root_uri": "file://cid-1/tmp/project"},
                {"root_uri": "file://cid-1/tmp/project/subdir"}
            ]
        });

        assert_eq!(
            uploaded_local_path_from_status(&status, "cid-1", "/tmp/project/subdir/a.txt"),
            Some("file://cid-1/tmp/project/subdir/a.txt".to_string())
        );
    }

    #[test]
    fn uploaded_local_path_ignores_local_and_other_clients() {
        let status = json!({
            "connectors": [
                {"root_uri": "file://local/tmp/project"},
                {"root_uri": "file://other-client/tmp/project"}
            ]
        });

        assert_eq!(
            uploaded_local_path_from_status(&status, "cid-1", "/tmp/project/a.txt"),
            None
        );
    }
}
