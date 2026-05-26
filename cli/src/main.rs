//! MFS CLI — thin HTTP client over the server's /v1 control plane (design/01, 03).
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
        /// Force full re-index
        #[arg(long)]
        full: bool,
        /// Enqueue for a worker instead of indexing inline
        #[arg(long)]
        no_process: bool,
        /// Bundle + upload the tree to the server even on the same host (no shared fs)
        #[arg(long)]
        upload: bool,
        /// Never upload; have the server read the path itself (shared fs)
        #[arg(long)]
        no_upload: bool,
    },
    /// Semantic + keyword search
    Search {
        query: String,
        path: Option<String>,
        #[arg(long, default_value = "hybrid")]
        mode: String,
        #[arg(long, default_value_t = 10)]
        top_k: u32,
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
    Remove { target: String },
    /// Client profile (endpoint) management — ~/.mfs/client.toml
    Profile {
        #[command(subcommand)]
        action: ProfileAction,
    },
    /// Manage a local mfs-server process
    Serve {
        #[command(subcommand)]
        action: ServeAction,
    },
}

#[derive(Subcommand)]
enum JobAction {
    /// Show a job by id
    Show { job_id: String },
    /// Cancel a running/queued job
    Cancel { job_id: String },
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
    Remove { target: String },
}

#[derive(Subcommand)]
enum ProfileAction {
    /// List profiles
    List,
    /// Add (or update) a profile
    Add { name: String, url: String },
    /// Set the active profile
    Use { name: String },
}

#[derive(Subcommand)]
enum ServeAction {
    /// Start a local mfs-server (detached)
    Start {
        #[arg(long, default_value = "127.0.0.1:8765")]
        bind: String,
    },
    /// Stop the local mfs-server
    Stop,
    /// Is the local mfs-server running?
    Status,
    /// Tail the local server log
    Logs,
}

// ---------- profile (client.toml) ----------
#[derive(Serialize, Deserialize, Default)]
struct ClientConfig {
    active: Option<String>,
    #[serde(default)]
    profiles: BTreeMap<String, Profile>,
}

#[derive(Serialize, Deserialize, Clone)]
struct Profile {
    url: String,
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
    std::fs::read_to_string(p).ok().and_then(|s| toml::from_str(&s).ok()).unwrap_or_default()
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
    "http://127.0.0.1:8765".to_string()
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
        Cmd::Add { target, config, since, full, no_process, upload, no_upload } => {
            // local/remote decision (design/02 §4.2): when the target is a real local path
            // and the server runs on a different host (no shared fs), bundle the tree and
            // upload it instead of asking the server to read a path it can't see. --upload
            // forces it on the same host; --no-upload always has the server read the path.
            let is_local_path = std::path::Path::new(target).exists();
            let do_upload = if *no_upload {
                false
            } else if *upload {
                is_local_path
            } else if is_local_path {
                let server_mid = get(client, &format!("{base}/v1/server/info"), &[])
                    .ok().and_then(|v| v["machine_id"].as_str().map(String::from)).unwrap_or_default();
                let client_host = client_hostname();
                !server_mid.is_empty() && !client_host.is_empty() && server_mid != client_host
            } else {
                false
            };
            if do_upload {
                return upload_path(client, base, target, !no_process, cli.json);
            }
            let mut body = serde_json::json!({"target": target, "full": full, "process": !no_process});
            if let Some(c) = config { body["config"] = load_config_file(c)?; }
            if let Some(s) = since { body["since"] = Value::String(s.clone()); }
            let v = post(client, &format!("{base}/v1/add"), &body)?;
            if cli.json { println!("{v}"); } else { println!("job: {}", v["job_id"].as_str().unwrap_or("?")); }
        }
        Cmd::Search { query, path, mode, top_k } => {
            let mut q = vec![("q", query.clone()), ("mode", mode.clone()), ("top_k", top_k.to_string())];
            if let Some(p) = path { q.push(("path", p.clone())); }
            let v = get(client, &format!("{base}/v1/search"), &q)?;
            if cli.json { println!("{v}"); return Ok(()); }
            for hit in v["results"].as_array().unwrap_or(&vec![]) {
                println!("{}  score={}", hit["source"].as_str().unwrap_or("?"),
                         hit["score"].as_f64().unwrap_or(0.0));
                if let Some(c) = hit["content"].as_str() {
                    println!("   {}", c.lines().next().unwrap_or("").chars().take(100).collect::<String>());
                }
            }
        }
        Cmd::Grep { pattern, path } => {
            let v = get(client, &format!("{base}/v1/grep"), &[("pattern", pattern.clone()), ("path", path.clone())])?;
            if cli.json { println!("{v}"); return Ok(()); }
            for hit in v["results"].as_array().unwrap_or(&vec![]) {
                println!("{}: {}", hit["source"].as_str().unwrap_or("?"),
                         hit["content"].as_str().unwrap_or("").chars().take(120).collect::<String>());
            }
        }
        Cmd::Ls { path } => {
            let v = get(client, &format!("{base}/v1/ls"), &[("path", path.clone())])?;
            if cli.json { println!("{v}"); return Ok(()); }
            print_entries(&v);
        }
        Cmd::Tree { path, depth } => {
            let v = get(client, &format!("{base}/v1/ls"), &[("path", path.clone())])?;
            if cli.json { println!("{v}"); return Ok(()); }
            println!("{path}");
            tree(client, base, path, *depth, "")?;
        }
        Cmd::Cat { path, range, meta, locator, peek, skim } => {
            let mut q = vec![("path", path.clone())];
            if let Some(r) = range { q.push(("range", r.clone())); }
            if *meta { q.push(("meta", "true".to_string())); }
            if let Some(l) = locator { q.push(("locator", l.clone())); }
            if *peek { q.push(("density", "peek".to_string())); }
            if *skim { q.push(("density", "skim".to_string())); }
            let v = get(client, &format!("{base}/v1/cat"), &q)?;
            if cli.json { println!("{v}"); return Ok(()); }
            if *meta { println!("{v}"); } else { println!("{}", v["content"].as_str().unwrap_or("")); }
        }
        Cmd::Head { path, lines } => {
            let text = cat_text(client, base, path)?;
            for l in text.lines().take(*lines) { println!("{l}"); }
        }
        Cmd::Tail { path, lines } => {
            let text = cat_text(client, base, path)?;
            let all: Vec<&str> = text.lines().collect();
            for l in all.iter().skip(all.len().saturating_sub(*lines)) { println!("{l}"); }
        }
        Cmd::Export { path, out } => {
            let text = cat_text(client, base, path)?;
            std::fs::write(out, &text).map_err(|e| e.to_string())?;
            println!("exported {} bytes -> {}", text.len(), out);
        }
        Cmd::Status => {
            let v = get(client, &format!("{base}/v1/status"), &[])?;
            println!("{}", serde_json::to_string_pretty(&v).unwrap_or_default());
        }
        Cmd::Job { action } => match action {
            JobAction::Show { job_id } => {
                let v = get(client, &format!("{base}/v1/jobs/{job_id}"), &[])?;
                println!("{}", serde_json::to_string_pretty(&v).unwrap_or_default());
            }
            JobAction::Cancel { job_id } => {
                let v = post(client, &format!("{base}/v1/jobs/{job_id}/cancel"), &serde_json::json!({}))?;
                println!("cancelled: {}", v["cancelled"].as_bool().unwrap_or(false));
            }
        },
        Cmd::Connector { action } => match action {
            ConnectorAction::Add { target, config } | ConnectorAction::Update { target, config } => {
                let mut body = serde_json::json!({"target": target});
                if let Some(c) = config { body["config"] = load_config_file(c)?; }
                let v = post(client, &format!("{base}/v1/add"), &body)?;
                println!("job: {}", v["job_id"].as_str().unwrap_or("?"));
            }
            ConnectorAction::Probe { target, config } => {
                let mut body = serde_json::json!({"target": target});
                if let Some(c) = config { body["config"] = load_config_file(c)?; }
                let v = post(client, &format!("{base}/v1/connectors/probe"), &body)?;
                println!("{}  ok={}  {}", v["type"].as_str().unwrap_or("?"),
                         v["ok"].as_bool().unwrap_or(false), v["detail"].as_str().unwrap_or(""));
            }
            ConnectorAction::List => {
                let v = get(client, &format!("{base}/v1/status"), &[])?;
                if cli.json { println!("{}", v["connectors"]); return Ok(()); }
                for c in v["connectors"].as_array().unwrap_or(&vec![]) {
                    println!("{:10}  {:8}  {}", c["type"].as_str().unwrap_or("?"),
                             c["status"].as_str().unwrap_or("?"), c["root_uri"].as_str().unwrap_or("?"));
                }
            }
            ConnectorAction::Inspect { target } => {
                let v = get(client, &format!("{base}/v1/connectors/inspect"), &[("target", target.clone())])?;
                println!("{}", serde_json::to_string_pretty(&v).unwrap_or_default());
            }
            ConnectorAction::Remove { target } => return remove_connector(client, base, target),
        },
        Cmd::Remove { target } => return remove_connector(client, base, target),
        Cmd::Profile { action } => return profile_cmd(action),
        Cmd::Serve { action } => return serve_cmd(action),
    }
    Ok(())
}

/// Best-effort client machine id (matches the server's socket.gethostname()).
fn client_hostname() -> String {
    std::process::Command::new("hostname").output().ok()
        .and_then(|o| String::from_utf8(o.stdout).ok())
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .or_else(|| std::env::var("HOSTNAME").ok())
        .unwrap_or_default()
}

/// Bundle a local tree into a tar.gz and POST it to /v1/upload (CS upload flow,
/// design/02 §4.2) — for a client/server that don't share a filesystem.
fn upload_path(client: &reqwest::blocking::Client, base: &str, target: &str,
               process: bool, json: bool) -> Result<(), String> {
    let p = std::path::Path::new(target);
    let name = p.file_name().and_then(|s| s.to_str())
        .ok_or_else(|| format!("cannot derive a name from {target}"))?.to_string();
    let parent = p.parent().filter(|x| !x.as_os_str().is_empty())
        .map(|x| x.to_path_buf()).unwrap_or_else(|| std::path::PathBuf::from("."));
    let tmp = std::env::temp_dir().join(format!("mfs-upload-{}.tar.gz", std::process::id()));
    // -C parent <name>: archive paths are relative to the dir, so the server stages
    // <name>/... cleanly (the server re-validates every member against zip-slip).
    let status = std::process::Command::new("tar")
        .arg("-czf").arg(&tmp).arg("-C").arg(&parent).arg(&name)
        .status().map_err(|e| format!("tar failed (is it installed?): {e}"))?;
    if !status.success() {
        return Err("tar failed to bundle the tree".into());
    }
    let data = std::fs::read(&tmp).map_err(|e| e.to_string())?;
    let _ = std::fs::remove_file(&tmp);
    let resp = client.post(format!("{base}/v1/upload"))
        .query(&[("name", name.as_str()), ("process", &process.to_string())])
        .body(data).send().map_err(|e| e.to_string())?;
    let v = parse(resp)?;
    if json { println!("{v}"); }
    else { println!("uploaded {} -> job {}", name, v["job_id"].as_str().unwrap_or("?")); }
    Ok(())
}

/// Load a connector config TOML file and convert it to a JSON value for the /v1/add body.
fn load_config_file(path: &str) -> Result<Value, String> {
    let text = std::fs::read_to_string(path).map_err(|e| format!("read {path}: {e}"))?;
    let toml_val: toml::Value = toml::from_str(&text).map_err(|e| format!("parse {path}: {e}"))?;
    serde_json::to_value(toml_val).map_err(|e| e.to_string())
}

fn remove_connector(client: &reqwest::blocking::Client, base: &str, target: &str) -> Result<(), String> {
    let resp = client.delete(format!("{base}/v1/connectors"))
        .query(&[("target", target)]).send().map_err(|e| e.to_string())?;
    let v = parse(resp)?;
    println!("removed: {}", v["removed"].as_bool().unwrap_or(false));
    Ok(())
}

fn print_entries(v: &Value) {
    for e in v["entries"].as_array().unwrap_or(&vec![]) {
        println!("{:4}  {}", e["type"].as_str().unwrap_or(""), e["name"].as_str().unwrap_or(""));
    }
}

fn tree(client: &reqwest::blocking::Client, base: &str, path: &str, depth: u32, prefix: &str) -> Result<(), String> {
    if depth == 0 { return Ok(()); }
    let v = get(client, &format!("{base}/v1/ls"), &[("path", path.to_string())])?;
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

fn cat_text(client: &reqwest::blocking::Client, base: &str, path: &str) -> Result<String, String> {
    let v = get(client, &format!("{base}/v1/cat"), &[("path", path.to_string())])?;
    Ok(v["content"].as_str().unwrap_or("").to_string())
}

fn profile_cmd(action: &ProfileAction) -> Result<(), String> {
    let mut cfg = load_client_cfg();
    match action {
        ProfileAction::List => {
            for (name, p) in &cfg.profiles {
                let marker = if cfg.active.as_deref() == Some(name) { "*" } else { " " };
                println!("{marker} {name:12} {}", p.url);
            }
            if cfg.profiles.is_empty() { println!("(no profiles; using {})", base_url()); }
        }
        ProfileAction::Add { name, url } => {
            cfg.profiles.insert(name.clone(), Profile { url: url.clone() });
            if cfg.active.is_none() { cfg.active = Some(name.clone()); }
            save_client_cfg(&cfg)?;
            println!("profile '{name}' -> {url}");
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
            println!("started mfs-server (pid {}) on {bind}; logs: {}", child.id(), log_file.display());
        }
        ServeAction::Stop => match read_pid(&pid_file) {
            Some(pid) => {
                let _ = std::process::Command::new("kill").arg(pid.to_string()).status();
                let _ = std::fs::remove_file(&pid_file);
                println!("stopped (pid {pid})");
            }
            None => println!("not running"),
        },
        ServeAction::Status => match read_pid(&pid_file) {
            Some(pid) if pid_alive(pid) => println!("running (pid {pid})"),
            _ => println!("not running"),
        },
        ServeAction::Logs => {
            let s = std::fs::read_to_string(&log_file).unwrap_or_default();
            for l in s.lines().rev().take(40).collect::<Vec<_>>().into_iter().rev() {
                println!("{l}");
            }
        }
    }
    Ok(())
}

fn read_pid(p: &PathBuf) -> Option<u32> {
    std::fs::read_to_string(p).ok().and_then(|s| s.trim().parse().ok())
}

fn pid_alive(pid: u32) -> bool {
    std::process::Command::new("kill")
        .args(["-0", &pid.to_string()])
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
}

fn get(client: &reqwest::blocking::Client, url: &str, q: &[(&str, String)]) -> Result<Value, String> {
    let resp = client.get(url).query(q).send().map_err(|e| e.to_string())?;
    parse(resp)
}

fn post(client: &reqwest::blocking::Client, url: &str, body: &Value) -> Result<Value, String> {
    let resp = client.post(url).json(body).send().map_err(|e| e.to_string())?;
    parse(resp)
}

fn parse(resp: reqwest::blocking::Response) -> Result<Value, String> {
    let status = resp.status();
    let v: Value = resp.json().map_err(|e| e.to_string())?;
    if !status.is_success() {
        // surface the stable error `code` (errors.md) alongside the human detail
        let code = v.get("code").and_then(|c| c.as_str()).unwrap_or("");
        let detail = v.get("detail").and_then(|d| d.as_str()).unwrap_or("request failed");
        return Err(if code.is_empty() { format!("{status}: {detail}") }
                   else { format!("{status} [{code}]: {detail}") });
    }
    Ok(v)
}
