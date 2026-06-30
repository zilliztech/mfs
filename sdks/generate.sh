#!/usr/bin/env bash
# Regenerate the language SDKs from protocol/openapi.yaml (design/10 §6).
# Requires: openapi-generator-cli (npm i -g @openapitools/openapi-generator-cli), Java 11+.
# Scope: Python + TypeScript. Other languages can be regenerated from the same spec
# on demand (e.g. -g go / -g java) — dropped from the shipped set to cut maintenance.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SPEC="$ROOT/protocol/openapi.yaml"
cd "$ROOT"

# Single source of truth for the package version: server/python/pyproject.toml.
# Keeps the generated SDK package version in lockstep with the rest of the repo
# instead of a hard-coded literal that silently drifts at each release.
VERSION="$(grep -m1 '^version' "$ROOT/server/python/pyproject.toml" | sed -E 's/.*"([^"]+)".*/\1/')"
echo "==> package version: $VERSION"

echo "==> python"
openapi-generator-cli generate -i "$SPEC" -g python -o sdks/python \
  --package-name mfs_sdk \
  --additional-properties=projectName=mfs-sdk,packageVersion="$VERSION",library=urllib3

echo "==> typescript (fetch)"
openapi-generator-cli generate -i "$SPEC" -g typescript-fetch -o sdks/typescript \
  --additional-properties=npmName=@mfs/sdk,npmVersion="$VERSION",supportsES6=true,typescriptThreePlus=true

echo "done. SDKs in sdks/{python,typescript}"
