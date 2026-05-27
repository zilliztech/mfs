#!/bin/bash
# CLI async-add e2e (matrix F1/F2/L1/L2): `mfs add` returns immediately with a queued job
# id, the in-process worker drains it, status reaches succeeded; `mfs add --wait` blocks
# until done. Run via: bash -ic 'bash /home/zhangchen/mfs/cli/test_async_add.sh' (needs OPENAI_API_KEY)
set -u
FAIL=0
ok(){ echo "  [OK] $1"; }
bad(){ echo "  [FAIL] $1"; FAIL=1; }

export MFS_HOME=$(mktemp -d)
export MFS_MILVUS_URI="$MFS_HOME/milvus.db"   # force Lite
export MFS_MILVUS_TOKEN=""
REPO=$(mktemp -d)
printf '# Sessions\n\nUser sessions live in Redis with a TTL.\n' > "$REPO/auth.md"
printf 'def login():\n    return True\n' > "$REPO/app.py"

cd /home/zhangchen/mfs/server/python
.venv/bin/mfs-server run --bind 127.0.0.1:8808 >/tmp/mfs_async_srv.log 2>&1 &
SV=$!
trap "kill $SV 2>/dev/null; rm -rf $MFS_HOME $REPO" EXIT
for i in $(seq 1 40); do curl -s http://127.0.0.1:8808/v1/server/info >/dev/null 2>&1 && break; sleep 1; done
export MFS_API_URL=http://127.0.0.1:8808
MFS=/home/zhangchen/mfs/cli/target/release/mfs

echo "=== async add returns immediately with a queued job id ==="
start=$(date +%s)
OUT=$($MFS add "$REPO")
dur=$(( $(date +%s) - start ))
echo "  -> $OUT"
echo "$OUT" | grep -q "queued (job" && ok "prints 'queued (job ...)'" || bad "async add message"
[ "$dur" -le 5 ] && ok "returns fast (${dur}s <= 5s)" || bad "add blocked ${dur}s"

echo "=== poll status until the job drains ==="
done=0
for i in $(seq 1 60); do
  s=$($MFS status --json 2>/dev/null)
  echo "$s" | grep -q '"running"' || { echo "$s" | grep -q '"succeeded"' && { done=1; break; }; }
  sleep 1
done
[ "$done" = "1" ] && ok "worker drained job to succeeded" || bad "job never reached succeeded"

echo "=== search works after async drain ==="
$MFS search "how are user sessions stored" "$REPO" | grep -q auth.md && ok "search hits auth.md" || bad "search"

echo "=== mfs add --wait blocks and reports done ==="
WOUT=$($MFS add --wait "$REPO")
echo "  -> $WOUT"
echo "$WOUT" | grep -q "^done:" && ok "--wait prints 'done: ...'" || bad "--wait message"

echo "========================================"
if [ "$FAIL" = "0" ]; then echo "CLI async-add e2e: ALL PASS"; else echo "CLI async-add e2e: FAILURES"; fi
exit $FAIL
