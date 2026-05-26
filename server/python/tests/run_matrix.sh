#!/usr/bin/env bash
# Full E2E matrix runner. Runs every suite sequentially (Zilliz free tier has a
# ~5-collection cap, so no parallelism). Backend-sensitive suites (storage / index /
# search-modes / converter / vlm) auto-run on BOTH Milvus Lite and Zilliz when
# ZILLIZ_URI/ZILLIZ_API_KEY are set; the rest are backend-agnostic (Lite).
# Run via: bash -ic 'cd server/python && bash tests/run_matrix.sh'
cd "$(dirname "$0")/.." || exit 1
PY=.venv/bin/python
declare -a TESTS=(
  phase9_accel_unit.py            # no key — rust accel parity
  phase10_connectors_unit.py      # no key — 14 connectors offline
  phase1_storage_smoke.py         # Lite + Zilliz
  phase2_file_connector_smoke.py  # file connector components
  phase2_engine_smoke.py          # engine add/idempotent/incremental
  phase3_index_search_smoke.py    # Lite + Zilliz
  phase4_search_modes_smoke.py    # Lite + Zilliz
  phase4_commands_smoke.py        # Lite
  phase4_api_smoke.py             # Lite (HTTP)
  phase6_converter_smoke.py       # Lite + Zilliz (pdf/docx/html)
  phase6_vlm_smoke.py             # Lite + Zilliz (image VLM)
  phase6_web_smoke.py             # Lite (network)
  phase6_github_smoke.py          # Lite (network + GITHUB_TOKEN)
  phase7_rename_smoke.py          # Lite (zero re-embed)
  phase7_robustness_smoke.py      # Lite (model-change/deletion/circuit-breaker)
  phase10_message_stream_smoke.py # Lite (thread_aggregate)
  phase10_postgres_smoke.py       # local PG
  phase10_mysql_smoke.py          # local MariaDB
  phase11_pg_backend_smoke.py     # PG metadata + tx_cache backend
  phase11_s3_objectstore_smoke.py # S3/MinIO object store
  phase11_worker_smoke.py         # worker daemon + cancel
  phase11_upload_smoke.py         # CS upload flow
  phase11_summary_smoke.py        # summary/schema/directory chunk_kinds
  phase11_filter_density_smoke.py # index_filter + cat density
  phase11_onnx_embedding_smoke.py # local onnx embedding (no key)
)
PASS=0; FAIL=0; SKIP=0
declare -a FAILED=()
for t in "${TESTS[@]}"; do
  [ -f "tests/$t" ] || { echo "  -- $t (missing, skip)"; SKIP=$((SKIP+1)); continue; }
  out=$(timeout 300 "$PY" "tests/$t" 2>&1)
  rc=$?
  last=$(echo "$out" | grep -vE "FutureWarning|warnings.warn" | grep -E "passed|PASS|FAIL|skipped" | tail -1)
  if [ $rc -eq 0 ]; then
    echo "  [PASS] $t — ${last}"; PASS=$((PASS+1))
  elif [ $rc -eq 2 ]; then
    echo "  [SKIP] $t (exit 2 — missing prereq)"; SKIP=$((SKIP+1))
  else
    echo "  [FAIL] $t (rc=$rc) — ${last}"; FAIL=$((FAIL+1)); FAILED+=("$t")
    echo "$out" | tail -6 | sed 's/^/        /'
  fi
done
echo ""
echo "================ MATRIX SUMMARY ================"
echo "  PASS=$PASS  FAIL=$FAIL  SKIP=$SKIP  (of ${#TESTS[@]})"
[ $FAIL -gt 0 ] && { echo "  failed: ${FAILED[*]}"; exit 1; }
echo "  ALL GREEN"
