"""Phase 14 — code-related deep paths: multi-language tree-sitter sweep, fallback
paths, density views on code, grep precision, and explicit Milvus-row inspection.

Covers the long tail of code-indexing scenarios that the file/text pipeline can hit:

  - Multi-language sweep — for 8 of the languages in CODE_EXT, write a small but
    real source file and verify chonkie's CodeChunker (tree-sitter) actually
    parses it: multi-chunk output, valid [start_line, end_line], chunk content
    contains the recognisable symbols we wrote.
  - Syntax error fallback — a broken .py file still gets indexed via chonkie's
    own except->RecursiveChunker fallback (or the engine's outer except wrapper
    in processors/text.py:chunk_body).
  - Empty + tiny code files — 0 / 1 chunk, no crash.
  - cat --peek on code — returns lines matching _CODE_SYMBOL (def/class/func/fn/
    public/private/type) only, not full bodies.
  - cat --skim on code — peek-equivalent with longer lines.
  - cat --range A:B on code — exact lines preserved.
  - mfs grep literal token on code — finds the precise file, accelerated path.
  - Mixed-extension repo — each file lands in the right chunk_kind ('body') and
    its chunks are recorded against the right object_uri in Milvus.

Self-contained; needs OPENAI_API_KEY (bash -ic)."""
import asyncio
import os
import pathlib
import shutil

from mfs_server.config import load_server_config
from mfs_server.engine.engine import Engine

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, cond):
    results.append(bool(cond)); print(f"  [{OK if cond else FAIL}] {name}"); return cond


# small but real code samples for 8 languages — each carries an unmistakable symbol
# we can grep / assert on, and enough lines to force chunk_size=200 to split it.
SAMPLES = {
    "auth.py": '''\
"""User authentication module."""


def authenticate_via_saml_sso(assertion: str) -> bool:
    """Verify a SAML SSO assertion's signature and timestamps."""
    if not assertion or len(assertion) < 100:
        return False
    return assertion.startswith("<saml:Assertion") and "Signature" in assertion


def hash_password_argon2id(plaintext: str, salt: bytes) -> bytes:
    """Hash a password using argon2id with the given salt."""
    if not plaintext:
        raise ValueError("empty password")
    return (plaintext.encode() + salt)[:32]


class TokenBucketRateLimiter:
    """A simple token-bucket rate limiter for the auth endpoint."""

    def __init__(self, capacity: int, refill_rate_per_sec: float) -> None:
        self.capacity = capacity
        self.refill_rate = refill_rate_per_sec
        self.tokens = float(capacity)

    def consume(self, n: int = 1) -> bool:
        """Try to consume n tokens, return True if allowed."""
        if self.tokens >= n:
            self.tokens -= n
            return True
        return False
''',
    "throttle.go": '''\
// Package throttle implements a leaky-bucket rate limiter for the edge proxy.
package throttle

import (
\t"sync"
\t"time"
)

// LeakyBucket caps request rate at a steady drain.
type LeakyBucket struct {
\tcapacity int
\tcurrent  int
\tlast     time.Time
\tmu       sync.Mutex
}

// NewLeakyBucket constructs a bucket with the given capacity and drain rate.
func NewLeakyBucket(capacity int, drainPerSec float64) *LeakyBucket {
\treturn &LeakyBucket{capacity: capacity, current: 0, last: time.Now()}
}

// Allow returns true if a request fits in the current bucket level.
func (b *LeakyBucket) Allow() bool {
\tb.mu.Lock()
\tdefer b.mu.Unlock()
\tif b.current >= b.capacity {
\t\treturn false
\t}
\tb.current++
\treturn true
}

func reset_bucket_zero(b *LeakyBucket) {
\tb.current = 0
}
''',
    "session.ts": '''\
// User session manager with sliding expiration.
import { createHash } from "node:crypto";

export interface SessionRecord {
  id: string;
  userId: string;
  expiresAt: number;
}

export class SessionStore {
  private records = new Map<string, SessionRecord>();

  public issue(userId: string, ttlSec: number): SessionRecord {
    const id = createHash("sha256").update(userId + Date.now()).digest("hex");
    const rec: SessionRecord = {
      id, userId, expiresAt: Date.now() + ttlSec * 1000,
    };
    this.records.set(id, rec);
    return rec;
  }

  public revoke(sessionId: string): boolean {
    return this.records.delete(sessionId);
  }

  private cleanup_expired_records(): number {
    const now = Date.now();
    let cleared = 0;
    for (const [id, rec] of this.records) {
      if (rec.expiresAt < now) {
        this.records.delete(id);
        cleared++;
      }
    }
    return cleared;
  }
}
''',
    "queue.rs": '''\
//! A simple thread-safe MPSC bounded queue for the worker pool.
use std::sync::{Arc, Mutex, Condvar};

pub struct BoundedQueue<T> {
    inner: Arc<(Mutex<Vec<T>>, Condvar)>,
    capacity: usize,
}

impl<T> BoundedQueue<T> {
    pub fn new(capacity: usize) -> Self {
        BoundedQueue {
            inner: Arc::new((Mutex::new(Vec::with_capacity(capacity)), Condvar::new())),
            capacity,
        }
    }

    pub fn push(&self, item: T) -> bool {
        let (lock, cvar) = &*self.inner;
        let mut q = lock.lock().unwrap();
        if q.len() >= self.capacity {
            return false;
        }
        q.push(item);
        cvar.notify_one();
        true
    }

    pub fn pop_blocking_until_item(&self) -> T {
        let (lock, cvar) = &*self.inner;
        let mut q = lock.lock().unwrap();
        while q.is_empty() {
            q = cvar.wait(q).unwrap();
        }
        q.remove(0)
    }
}

fn private_helper_log_failure() {
    eprintln!("queue full");
}
''',
    "Worker.java": '''\
package com.example.workerpool;

import java.util.concurrent.BlockingQueue;
import java.util.concurrent.LinkedBlockingQueue;

public class Worker implements Runnable {

    private final BlockingQueue<Runnable> taskQueue;
    private volatile boolean running = true;

    public Worker(BlockingQueue<Runnable> taskQueue) {
        this.taskQueue = taskQueue;
    }

    @Override
    public void run() {
        while (running) {
            try {
                Runnable t = taskQueue.take();
                t.run();
            } catch (InterruptedException ex) {
                Thread.currentThread().interrupt();
                return;
            }
        }
    }

    public void shutdown_gracefully_now() {
        this.running = false;
    }

    private void escalate_to_unique_handler_xyz() {
        System.err.println("unexpected_state");
    }
}
''',
    "deploy.sh": '''\
#!/bin/bash
# Deploy script for the staging environment.
set -euo pipefail

ENVIRONMENT=${1:-staging}
ARTIFACT_BUCKET="s3://acme-releases"

function check_prereqs_for_deployment() {
    command -v kubectl >/dev/null || { echo "kubectl missing"; exit 1; }
    command -v helm >/dev/null || { echo "helm missing"; exit 1; }
}

function fetch_artifact_from_bucket() {
    local version=$1
    aws s3 cp "${ARTIFACT_BUCKET}/release-${version}.tar.gz" /tmp/release.tar.gz
}

function rollout_blue_green_strategy() {
    local current_color
    current_color=$(kubectl get svc app -o jsonpath='{.spec.selector.color}')
    local next_color="green"
    if [ "$current_color" = "green" ]; then next_color="blue"; fi
    kubectl apply -f deploy/${next_color}.yaml
    kubectl patch svc app -p "{\\"spec\\":{\\"selector\\":{\\"color\\":\\"${next_color}\\"}}}"
}

check_prereqs_for_deployment
fetch_artifact_from_bucket "1.4.7"
rollout_blue_green_strategy
''',
    "queue_ext.cpp": '''\
#include <vector>
#include <mutex>
#include <condition_variable>

namespace mfs {

class CircularQueue {
public:
    explicit CircularQueue(std::size_t capacity)
        : buffer_(capacity), head_(0), tail_(0), size_(0), capacity_(capacity) {}

    bool push(int value) {
        std::unique_lock<std::mutex> lock(mu_);
        if (size_ == capacity_) {
            return false;
        }
        buffer_[tail_] = value;
        tail_ = (tail_ + 1) % capacity_;
        ++size_;
        cv_.notify_one();
        return true;
    }

    int pop_or_block_indefinitely_xx() {
        std::unique_lock<std::mutex> lock(mu_);
        cv_.wait(lock, [this]() { return size_ > 0; });
        int v = buffer_[head_];
        head_ = (head_ + 1) % capacity_;
        --size_;
        return v;
    }

private:
    std::vector<int> buffer_;
    std::size_t head_, tail_, size_, capacity_;
    std::mutex mu_;
    std::condition_variable cv_;
};

}  // namespace mfs
''',
    "validator.rb": '''\
# Simple input validator for the API surface.

module ApiInputs
  class Validator
    def initialize(rules)
      @rules = rules
    end

    def validate_payload_against_rules(payload)
      errors = []
      @rules.each do |field, rule|
        value = payload[field]
        if rule[:required] && value.nil?
          errors << "#{field} is required"
        end
        if value && rule[:max_length] && value.length > rule[:max_length]
          errors << "#{field} exceeds max length"
        end
      end
      errors
    end

    private

    def trim_whitespace_everywhere(s)
      s.to_s.strip.gsub(/\\s+/, " ")
    end
  end
end
''',
    "broken_syntax.py": '''\
"""This file is intentionally broken — tests the chunker fallback path."""


def half_open(a, b:
    """Missing close paren above. tree-sitter Python parser should bail."""
    return a + b


class Mostly(Working):
    def method(self):
        return [1, 2 3]  # missing comma — invalid syntax
''',
    "empty.py": "",
    "oneliner.py": "x = 1  # tiny one-line file\n",
}


# expected symbol-ish anchor per file — must appear in at least one indexed chunk
EXPECTED_ANCHORS = {
    "auth.py": "authenticate_via_saml_sso",
    "throttle.go": "NewLeakyBucket",
    "session.ts": "cleanup_expired_records",
    "queue.rs": "pop_blocking_until_item",
    "Worker.java": "shutdown_gracefully_now",
    "deploy.sh": "rollout_blue_green_strategy",
    "queue_ext.cpp": "pop_or_block_indefinitely_xx",
    "validator.rb": "validate_payload_against_rules",
}


async def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set — run via bash -ic"); raise SystemExit(2)

    base = f"/tmp/mfs_code_{os.getpid()}"; os.system(f"rm -rf '{base}'*")
    repo = pathlib.Path(f"{base}_repo")
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_m.db"; cfg.milvus.uri = base + "_v.db"; cfg.milvus.token = ""
    cfg.object_store.root = base + "_c"; cfg.transformation_cache.db_path = base + "_t.db"
    cfg.summary.enabled = False
    cfg.chunk.chunk_size = 200      # force tiny budget so non-trivial files split
    eng = Engine(cfg)
    await eng.startup()

    repo.mkdir(parents=True)
    for name, body in SAMPLES.items():
        (repo / name).write_text(body)

    try:
        eng.milvus.drop_collection("default"); eng.milvus.ensure_collection("default")
        await eng.add(str(repo))

        crow = await eng.meta.fetchone("SELECT id, root_uri FROM connectors WHERE type='file'")
        cid = crow["id"]; uri = crow["root_uri"]

        # ----- objects table consistency -----
        objs = await eng.meta.fetchall(
            "SELECT object_uri, chunk_count, search_status FROM objects WHERE connector_id=?",
            (cid,))
        paths = {r["object_uri"]: r for r in objs}
        for f in SAMPLES:
            check(f"objects row exists for /{f}", f"/{f}" in paths)

        # ----- multi-language sweep: each file with content >= 2 chunks -----
        for f, anchor in EXPECTED_ANCHORS.items():
            row = paths.get(f"/{f}", {})
            cc = row.get("chunk_count") or 0
            check(f"{f}: chunk_count >= 2 (got {cc})", cc >= 2)
            # Milvus row inspection
            full_uri = uri + f"/{f}"
            chunks = await asyncio.to_thread(
                eng.milvus.get_chunks_by_object, "default", uri, full_uri)
            check(f"{f}: Milvus chunk count == objects.chunk_count ({len(chunks)} vs {cc})",
                  len(chunks) == cc)
            check(f"{f}: all chunks have chunk_kind='body'",
                  all(c.get("chunk_kind") == "body" for c in chunks))
            def _ln(c):
                return ((c.get("locator") or {}).get("lines")) or None
            check(f"{f}: every chunk has locator={{'lines':[start,end]}} range",
                  all(isinstance(_ln(c), list) and len(_ln(c)) == 2
                      and _ln(c)[0] >= 1 and _ln(c)[1] >= _ln(c)[0]
                      for c in chunks))
            check(f"{f}: anchor symbol {anchor!r} appears in at least one chunk's content",
                  any(anchor in (c.get("content") or "") for c in chunks))

        # ----- robustness: broken syntax falls back gracefully (must produce >=1 chunk) -----
        broken_row = paths.get("/broken_syntax.py", {})
        check(f"broken_syntax.py: indexed despite syntax error "
              f"(chunk_count={broken_row.get('chunk_count')})",
              (broken_row.get("chunk_count") or 0) >= 1)

        # ----- empty / tiny code files -----
        empty_row = paths.get("/empty.py", {})
        check(f"empty.py: 0 chunks, no crash (chunk_count={empty_row.get('chunk_count')})",
              (empty_row.get("chunk_count") or 0) == 0)
        oneliner_row = paths.get("/oneliner.py", {})
        check(f"oneliner.py: indexed (chunk_count={oneliner_row.get('chunk_count')})",
              (oneliner_row.get("chunk_count") or 0) >= 1)

        # ----- cat density: --peek on code returns only _CODE_SYMBOL lines -----
        peek_text = await eng.cat(uri + "/auth.py", density="peek")
        peek_lines = [l for l in peek_text.splitlines() if l.strip()]
        # every non-empty peek line starts with def/class/func/fn/public/private/type
        prefixes = ("def ", "class ", "func ", "fn ", "public ", "private ", "type ")
        all_symbol = all(any(l.lstrip().startswith(p) for p in prefixes) for l in peek_lines)
        check(f"cat --peek on auth.py: every non-empty line is a code symbol "
              f"({len(peek_lines)} lines)", all_symbol and len(peek_lines) >= 3)
        check("cat --peek on auth.py: includes authenticate_via_saml_sso",
              any("authenticate_via_saml_sso" in l for l in peek_lines))
        check("cat --peek on auth.py: excludes full function bodies",
              not any("if not assertion" in l for l in peek_lines))

        # ----- cat --skim on code: at least as much info as peek (skim is peek+) -----
        skim_text = await eng.cat(uri + "/auth.py", density="skim")
        check("cat --skim returns >= peek length (skim is peek + more)",
              len(skim_text) >= len(peek_text))

        # ----- cat --range A:B: returns a contiguous slice of the source -----
        full_text = await eng.cat(uri + "/auth.py")
        lines = full_text.splitlines()
        ranged_lines = (await eng.cat(uri + "/auth.py", range=(4, 7))).splitlines()
        check(f"cat --range 4:7 returns 3 lines (got {len(ranged_lines)})", len(ranged_lines) == 3)
        # The 3 returned lines must be a contiguous substring of the source. We don't
        # hardwire 0-vs-1-indexing here — just confirm those 3 lines appear together,
        # in order, somewhere in the full file.
        joined = "\n".join(ranged_lines)
        check("cat --range slice is a contiguous substring of full cat content",
              joined and joined in full_text)

        # ----- mfs grep literal token precision -----
        grep_hits = await eng.grep("authenticate_via_saml_sso", str(repo))
        on_auth = [h for h in grep_hits if (h.get("source") or "").endswith("/auth.py")]
        check(f"grep 'authenticate_via_saml_sso' matches in auth.py "
              f"({len(on_auth)} hits / {len(grep_hits)} total)",
              len(on_auth) >= 1)
        # AND it should NOT show up in other code files where the symbol isn't present
        other_files_with_hit = {h.get("source") for h in grep_hits
                                if not (h.get("source") or "").endswith("/auth.py")}
        check(f"grep precision: token doesn't show up in other files "
              f"({len(other_files_with_hit)} other files)",
              len(other_files_with_hit) == 0)
    finally:
        try: eng.milvus.drop_collection("default")
        except Exception: pass
        await eng.shutdown()
        shutil.rmtree(repo, ignore_errors=True)
        os.system(f"rm -rf '{base}'*")

    passed = sum(results)
    print(f"\n{'='*46}\n  code deep e2e: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
