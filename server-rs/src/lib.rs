//! MFS server hot-path acceleration, exposed to Python via PyO3.
//!
//! Three hot paths that pure Python is slow at:
//!   - `scan_dir`        : recursive directory walk -> (relpath, size, mtime_ns), with
//!                         substring-based ignore (gitignore-style pruning lives in Python).
//!   - `linear_grep_file`: streaming literal/regex grep over a file -> (line_no, line).
//!   - `jsonl_record_count` / `jsonl_field_texts` : fast JSONL scanning.
//!
//! Every function has a pure-Python fallback in `mfs_server.common.accel`, so the
//! server runs identically whether or not this native module is installed.

use pyo3::prelude::*;
use pyo3::exceptions::PyIOError;
use std::fs;
use std::io::{BufRead, BufReader};
use walkdir::WalkDir;

/// Recursively walk `root`, returning (relative_path, size_bytes, mtime_ns) for every
/// file. Any entry whose path contains one of `ignore_substrings` is skipped (and, if a
/// directory, pruned). Relative paths use '/' and have a leading '/'.
#[pyfunction]
#[pyo3(signature = (root, ignore_substrings = Vec::new()))]
fn scan_dir(root: &str, ignore_substrings: Vec<String>) -> PyResult<Vec<(String, u64, i64)>> {
    let root_path = std::path::Path::new(root);
    let mut out: Vec<(String, u64, i64)> = Vec::new();
    let walker = WalkDir::new(root).follow_links(false).into_iter();
    let mut it = walker;
    while let Some(entry) = it.next() {
        let entry = match entry {
            Ok(e) => e,
            Err(_) => continue,
        };
        let path = entry.path();
        let path_str = path.to_string_lossy();
        let ignored = ignore_substrings.iter().any(|s| path_str.contains(s.as_str()));
        if ignored {
            if entry.file_type().is_dir() {
                it.skip_current_dir();
            }
            continue;
        }
        if !entry.file_type().is_file() {
            continue;
        }
        let meta = match entry.metadata() {
            Ok(m) => m,
            Err(_) => continue,
        };
        let rel = match path.strip_prefix(root_path) {
            Ok(r) => r.to_string_lossy().replace('\\', "/"),
            Err(_) => continue,
        };
        let mtime_ns = meta
            .modified()
            .ok()
            .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
            .map(|d| d.as_nanos() as i64)
            .unwrap_or(0);
        out.push((format!("/{}", rel), meta.len(), mtime_ns));
    }
    Ok(out)
}

/// Streaming grep over a file. Returns (1-based line_no, line_text) for matching lines.
/// If `regex` is true the pattern is a regular expression, else a literal substring.
#[pyfunction]
#[pyo3(signature = (path, pattern, case_insensitive = false, regex = false, max_matches = 1000))]
fn linear_grep_file(
    path: &str,
    pattern: &str,
    case_insensitive: bool,
    regex: bool,
    max_matches: usize,
) -> PyResult<Vec<(usize, String)>> {
    let file = fs::File::open(path).map_err(|e| PyIOError::new_err(e.to_string()))?;
    let reader = BufReader::new(file);
    let mut out: Vec<(usize, String)> = Vec::new();

    let re = if regex {
        Some(
            regex::RegexBuilder::new(pattern)
                .case_insensitive(case_insensitive)
                .build()
                .map_err(|e| PyIOError::new_err(format!("bad regex: {e}")))?,
        )
    } else {
        None
    };
    let needle = if case_insensitive {
        pattern.to_lowercase()
    } else {
        pattern.to_string()
    };

    for (i, line) in reader.lines().enumerate() {
        let line = match line {
            Ok(l) => l,
            Err(_) => continue, // skip non-utf8 line
        };
        let hit = match &re {
            Some(r) => r.is_match(&line),
            None => {
                if case_insensitive {
                    line.to_lowercase().contains(&needle)
                } else {
                    line.contains(&needle)
                }
            }
        };
        if hit {
            out.push((i + 1, line));
            if out.len() >= max_matches {
                break;
            }
        }
    }
    Ok(out)
}

/// Count non-empty lines (≈ records) in a JSONL file without parsing each object.
#[pyfunction]
fn jsonl_record_count(path: &str) -> PyResult<usize> {
    let file = fs::File::open(path).map_err(|e| PyIOError::new_err(e.to_string()))?;
    let reader = BufReader::new(file);
    let mut n = 0usize;
    for line in reader.lines() {
        if let Ok(l) = line {
            if !l.trim().is_empty() {
                n += 1;
            }
        }
    }
    Ok(n)
}

/// Parse a JSONL file and, for each record, join the string values of `fields` with
/// "\n" into one text blob. Missing fields are skipped. Returns one string per record.
#[pyfunction]
#[pyo3(signature = (path, fields, max_records = 1_000_000))]
fn jsonl_field_texts(path: &str, fields: Vec<String>, max_records: usize) -> PyResult<Vec<String>> {
    let file = fs::File::open(path).map_err(|e| PyIOError::new_err(e.to_string()))?;
    let reader = BufReader::new(file);
    let mut out: Vec<String> = Vec::new();
    for line in reader.lines() {
        let line = match line {
            Ok(l) => l,
            Err(_) => continue,
        };
        if line.trim().is_empty() {
            continue;
        }
        let val: serde_json::Value = match serde_json::from_str(&line) {
            Ok(v) => v,
            Err(_) => continue,
        };
        let mut parts: Vec<String> = Vec::new();
        for f in &fields {
            if let Some(v) = val.get(f) {
                let s = match v {
                    serde_json::Value::String(s) => s.clone(),
                    serde_json::Value::Null => continue,
                    other => other.to_string(),
                };
                parts.push(format!("{f}: {s}"));
            }
        }
        out.push(parts.join("\n"));
        if out.len() >= max_records {
            break;
        }
    }
    Ok(out)
}

/// Last `n` lines of a file, read backward from EOF in chunks so a huge file is never
/// fully materialized (the point of `tail`). Returns lines oldest->newest, no trailing '\n'.
#[pyfunction]
#[pyo3(signature = (path, n = 20))]
fn tail_lines(path: &str, n: usize) -> PyResult<Vec<String>> {
    use std::io::{Read, Seek, SeekFrom};
    if n == 0 {
        return Ok(Vec::new());
    }
    let mut file = fs::File::open(path).map_err(|e| PyIOError::new_err(e.to_string()))?;
    let size = file.seek(SeekFrom::End(0)).map_err(|e| PyIOError::new_err(e.to_string()))?;
    let mut buf: Vec<u8> = Vec::new();
    let mut pos = size;
    let chunk = 65536u64;
    // read backward until we've seen n newlines beyond the final one, or hit the start
    while pos > 0 {
        let read = chunk.min(pos);
        pos -= read;
        file.seek(SeekFrom::Start(pos)).map_err(|e| PyIOError::new_err(e.to_string()))?;
        let mut tmp = vec![0u8; read as usize];
        file.read_exact(&mut tmp).map_err(|e| PyIOError::new_err(e.to_string()))?;
        let mut merged = tmp;
        merged.extend_from_slice(&buf);
        buf = merged;
        // a trailing newline doesn't start a new line, so we need n+1 newlines to bound n lines
        if buf.iter().filter(|&&b| b == b'\n').count() > n {
            break;
        }
    }
    let text = String::from_utf8_lossy(&buf);
    let lines: Vec<&str> = text.split('\n').collect();
    // drop a trailing empty element produced by a final newline
    let mut sl: &[&str] = &lines;
    if let Some(last) = sl.last() {
        if last.is_empty() {
            sl = &sl[..sl.len() - 1];
        }
    }
    let start = sl.len().saturating_sub(n);
    Ok(sl[start..].iter().map(|s| s.to_string()).collect())
}

/// Recursive walk applying gitignore-semantics `patterns` (gitwildmatch lines, same as the
/// file connector feeds pathspec). Returns (relpath '/foo', size, mtime_ns, inode) for each
/// non-ignored file; ignored directories are pruned (not descended). Raises on IO error.
#[pyfunction]
fn walk_tree(root: &str, patterns: Vec<String>) -> PyResult<Vec<(String, u64, i64, u64)>> {
    use ignore::gitignore::GitignoreBuilder;
    use std::os::unix::fs::MetadataExt;
    let mut gb = GitignoreBuilder::new(root);
    for p in &patterns {
        let _ = gb.add_line(None, p);
    }
    let gi = gb.build().map_err(|e| PyIOError::new_err(e.to_string()))?;
    let root_path = std::path::Path::new(root);
    let mut out: Vec<(String, u64, i64, u64)> = Vec::new();
    let walker = WalkDir::new(root).into_iter().filter_entry(|e| {
        if e.depth() == 0 {
            return true;        // the root itself is never matched/pruned
        }
        match e.path().strip_prefix(root_path) {
            Ok(rel) => !gi.matched(rel, e.file_type().is_dir()).is_ignore(),
            Err(_) => true,
        }
    });
    for entry in walker {
        let entry = entry.map_err(|e| PyIOError::new_err(e.to_string()))?;
        if !entry.file_type().is_file() {
            continue;
        }
        let md = entry.metadata().map_err(|e| PyIOError::new_err(e.to_string()))?;
        let rel = entry.path().strip_prefix(root_path).unwrap().to_string_lossy().replace('\\', "/");
        let mtime_ns = md.modified().ok()
            .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
            .map(|d| d.as_nanos() as i64)
            .unwrap_or(0);
        out.push((format!("/{}", rel), md.len(), mtime_ns, md.ino()));
    }
    Ok(out)
}

fn sha1_one(path: &str) -> Option<String> {
    use sha1::{Digest, Sha1};
    use std::io::Read;
    let mut f = fs::File::open(path).ok()?;
    let mut hasher = Sha1::new();
    let mut buf = [0u8; 1 << 16];
    loop {
        let n = f.read(&mut buf).ok()?;
        if n == 0 {
            break;
        }
        hasher.update(&buf[..n]);
    }
    Some(format!("{:x}", hasher.finalize()))
}

/// Content sha1 (hex) of each path, hashed in parallel with the GIL released. Returns
/// (path, Some(hex)) or (path, None) when unreadable. Order matches the input.
#[pyfunction]
fn sha1_files(py: Python<'_>, paths: Vec<String>) -> PyResult<Vec<(String, Option<String>)>> {
    use rayon::prelude::*;
    let res = py.allow_threads(|| {
        paths.par_iter().map(|p| (p.clone(), sha1_one(p))).collect::<Vec<_>>()
    });
    Ok(res)
}

#[pymodule]
fn mfs_server_rs(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", "0.4.0")?;
    m.add_function(wrap_pyfunction!(scan_dir, m)?)?;
    m.add_function(wrap_pyfunction!(linear_grep_file, m)?)?;
    m.add_function(wrap_pyfunction!(jsonl_record_count, m)?)?;
    m.add_function(wrap_pyfunction!(jsonl_field_texts, m)?)?;
    m.add_function(wrap_pyfunction!(tail_lines, m)?)?;
    m.add_function(wrap_pyfunction!(walk_tree, m)?)?;
    m.add_function(wrap_pyfunction!(sha1_files, m)?)?;
    Ok(())
}
