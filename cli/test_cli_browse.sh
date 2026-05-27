#!/bin/bash
# CLI browse/read e2e: drive the real mfs binary through ls / tree / cat (+peek/range/meta) /
# head / tail / export / grep / job / connector against a live server.
# Run: bash -ic 'bash /home/zhangchen/mfs/cli/test_cli_browse.sh'  (needs OPENAI_API_KEY)
set -u
FAIL=0
ok(){ echo "  [OK] $1"; }
bad(){ echo "  [FAIL] $1 -- $2"; FAIL=1; }

export MFS_HOME=$(mktemp -d)
export MFS_MILVUS_URI="$MFS_HOME/milvus.db"
export MFS_MILVUS_TOKEN=""
REPO=$(mktemp -d)
mkdir -p "$REPO/src"
printf '# Billing\n\nInvoices and subscriptions overview.\nSecond line.\nThird line.\n' > "$REPO/top.md"
printf 'def charge(invoice):\n    return gateway.capture(invoice)\n' > "$REPO/src/app.py"
printf 'def helper():\n    return 1\n' > "$REPO/src/util.py"

cd /home/zhangchen/mfs/server/python
.venv/bin/mfs-server run --bind 127.0.0.1:8811 >/tmp/mfs_browse_srv.log 2>&1 &
SV=$!
trap "kill $SV 2>/dev/null; rm -rf $MFS_HOME $REPO /tmp/mfs_export_out.txt" EXIT
for i in $(seq 1 40); do curl -s http://127.0.0.1:8811/v1/server/info >/dev/null 2>&1 && break; sleep 1; done
export MFS_API_URL=http://127.0.0.1:8811
MFS=/home/zhangchen/mfs/cli/target/release/mfs

echo "=== add --wait (block until indexed) ==="
$MFS add --wait "$REPO" | grep -q "^done:" && ok "add --wait" || bad "add --wait" "no done line"

echo "=== ls ==="
LS=$($MFS ls "$REPO"); echo "$LS" | grep -q "top.md" && echo "$LS" | grep -q "src" && ok "ls shows top.md + src/" || bad "ls" "$LS"

echo "=== tree ==="
TR=$($MFS tree "$REPO"); echo "$TR" | grep -q "app.py" && ok "tree shows nested app.py" || bad "tree" "$TR"

echo "=== cat ==="
$MFS cat "$REPO/top.md" | grep -q "Invoices and subscriptions" && ok "cat content" || bad "cat" "miss"
echo "=== cat --range ==="
$MFS cat "$REPO/top.md" --range 3:5 | grep -q "Second line" && ok "cat --range slice" || bad "cat --range" "miss"
echo "=== cat --meta ==="
$MFS cat "$REPO/top.md" --meta | grep -qi "media_type\|fingerprint" && ok "cat --meta" || bad "cat --meta" "miss"
echo "=== head -n 1 ==="
$MFS head "$REPO/src/app.py" -n 1 | grep -q "def charge" && ok "head -n1" || bad "head" "miss"
echo "=== tail -n 1 ==="
$MFS tail "$REPO/top.md" -n 1 | grep -q "Third line" && ok "tail -n1" || bad "tail" "miss"
echo "=== export ==="
$MFS export "$REPO/top.md" /tmp/mfs_export_out.txt | grep -q "exported" && grep -q "Invoices" /tmp/mfs_export_out.txt && ok "export to file" || bad "export" "miss"
echo "=== grep ==="
$MFS grep "gateway" "$REPO" | grep -q "app.py" && ok "grep hits app.py" || bad "grep" "miss"

echo "=== job list ==="
$MFS job list | grep -q "succeeded" && ok "job list shows succeeded" || bad "job list" "no succeeded"
echo "=== connector list ==="
$MFS connector list | grep -q "$REPO" && ok "connector list shows repo" || bad "connector list" "miss"
echo "=== connector probe ==="
$MFS connector probe "$REPO" | grep -qi "ok=true\|file" && ok "connector probe ok" || bad "connector probe" "miss"

echo "========================================"
if [ "$FAIL" = "0" ]; then echo "CLI browse e2e: ALL PASS"; else echo "CLI browse e2e: FAILURES"; fi
exit $FAIL
