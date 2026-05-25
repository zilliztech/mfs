#!/bin/bash
# CLI end-to-end: start a real mfs-server (Lite), drive it through the Rust mfs binary.
# Run via: bash -ic 'bash /home/zhangchen/mfs/cli/test_cli_e2e.sh'  (needs OPENAI_API_KEY)
set -u
FAIL=0
ok(){ echo "  [OK] $1"; }
bad(){ echo "  [FAIL] $1"; FAIL=1; }

export MFS_HOME=$(mktemp -d)
export MFS_MILVUS_URI="$MFS_HOME/milvus.db"   # force Lite (override any ZILLIZ_URI in env)
export MFS_MILVUS_TOKEN=""
REPO=$(mktemp -d)
printf '# Session storage\n\nUser sessions are stored in Redis with a TTL.\n' > "$REPO/auth.md"
printf 'def login():\n    return True\n' > "$REPO/app.py"

cd /home/zhangchen/mfs/server/python
.venv/bin/mfs-server run --bind 127.0.0.1:8799 >/tmp/mfs_cli_srv.log 2>&1 &
SV=$!
trap "kill $SV 2>/dev/null; rm -rf $MFS_HOME $REPO" EXIT

for i in $(seq 1 40); do
  curl -s http://127.0.0.1:8799/v1/server/info >/dev/null 2>&1 && break
  sleep 1
done
export MFS_API_URL=http://127.0.0.1:8799
MFS=/home/zhangchen/mfs/cli/target/release/mfs

echo "=== mfs add ==="
$MFS add "$REPO" && ok "add" || bad "add"
echo "=== mfs status ==="
$MFS status | grep -q connectors && ok "status" || bad "status"
echo "=== mfs ls ==="
$MFS ls "$REPO" | grep -q auth.md && ok "ls shows auth.md" || bad "ls"
echo "=== mfs search ==="
$MFS search "how are user sessions stored" "$REPO" | grep -q auth.md && ok "search hits auth.md" || bad "search"
echo "=== mfs cat ==="
$MFS cat "$REPO/auth.md" | grep -q "Session storage" && ok "cat content" || bad "cat"
echo "=== mfs grep ==="
$MFS grep "Session" "$REPO" | grep -q auth.md && ok "grep hits auth.md" || bad "grep"

echo "========================================"
if [ "$FAIL" = "0" ]; then echo "CLI e2e: ALL PASS"; else echo "CLI e2e: FAILURES"; fi
exit $FAIL
