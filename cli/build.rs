use std::process::Command;

fn main() {
    // Stamp the build with a short git commit so `mfs --version` can answer
    // "which build is this" without ps-aux archaeology across install paths
    // (cargo install / uv tool / a worktree's own target dir can all differ).
    // Falls back to "unknown" outside a git checkout (e.g. a source tarball).
    let sha = Command::new("git")
        .args(["rev-parse", "--short", "HEAD"])
        .output()
        .ok()
        .filter(|o| o.status.success())
        .and_then(|o| String::from_utf8(o.stdout).ok())
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| "unknown".to_string());
    println!("cargo:rustc-env=MFS_GIT_SHA={sha}");
    println!("cargo:rerun-if-changed=../.git/HEAD");
    println!("cargo:rerun-if-changed=../.git/index");
}
