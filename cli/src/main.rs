//! MFS CLI — thin HTTP client over the server's /v1 control plane (design/01, 03).
//! Cold-start-fast single binary; all heavy work is server-side. Endpoint resolves
//! from $MFS_API_URL (default http://127.0.0.1:8765).

use clap::{Parser, Subcommand};
use serde_json::Value;

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
    Add { target: String },
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
    /// Read an object
    Cat {
        path: String,
        #[arg(long)]
        range: Option<String>,
        #[arg(long)]
        meta: bool,
    },
    /// Server / connector / job status
    Status,
}

fn base_url() -> String {
    std::env::var("MFS_API_URL").unwrap_or_else(|_| "http://127.0.0.1:8765".to_string())
}

fn main() {
    let cli = Cli::parse();
    let client = reqwest::blocking::Client::new();
    let base = base_url();
    let result = run(&cli, &client, &base);
    if let Err(e) = result {
        eprintln!("error: {e}");
        std::process::exit(1);
    }
}

fn run(cli: &Cli, client: &reqwest::blocking::Client, base: &str) -> Result<(), String> {
    match &cli.cmd {
        Cmd::Add { target } => {
            let v = post(client, &format!("{base}/v1/add"), &serde_json::json!({"target": target}))?;
            println!("job: {}", v["job_id"].as_str().unwrap_or("?"));
        }
        Cmd::Search { query, path, mode, top_k } => {
            let mut q = vec![("q", query.clone()), ("mode", mode.clone()), ("top_k", top_k.to_string())];
            if let Some(p) = path { q.push(("path", p.clone())); }
            let v = get(client, &format!("{base}/v1/search"), &q)?;
            if cli.json { println!("{}", v); return Ok(()); }
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
            if cli.json { println!("{}", v); return Ok(()); }
            for hit in v["results"].as_array().unwrap_or(&vec![]) {
                println!("{}: {}", hit["source"].as_str().unwrap_or("?"),
                         hit["content"].as_str().unwrap_or("").chars().take(120).collect::<String>());
            }
        }
        Cmd::Ls { path } => {
            let v = get(client, &format!("{base}/v1/ls"), &[("path", path.clone())])?;
            if cli.json { println!("{}", v); return Ok(()); }
            for e in v["entries"].as_array().unwrap_or(&vec![]) {
                println!("{:4}  {}", e["type"].as_str().unwrap_or(""), e["name"].as_str().unwrap_or(""));
            }
        }
        Cmd::Cat { path, range, meta } => {
            let mut q = vec![("path", path.clone())];
            if let Some(r) = range { q.push(("range", r.clone())); }
            if *meta { q.push(("meta", "true".to_string())); }
            let v = get(client, &format!("{base}/v1/cat"), &q)?;
            if cli.json { println!("{}", v); return Ok(()); }
            if *meta { println!("{}", v); }
            else { println!("{}", v["content"].as_str().unwrap_or("")); }
        }
        Cmd::Status => {
            let v = get(client, &format!("{base}/v1/status"), &[])?;
            println!("{}", serde_json::to_string_pretty(&v).unwrap_or_default());
        }
    }
    Ok(())
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
        return Err(format!("{}: {}", status, v.get("detail").and_then(|d| d.as_str()).unwrap_or("request failed")));
    }
    Ok(v)
}
