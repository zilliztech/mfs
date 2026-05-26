#!/usr/bin/env bash
# Regenerate all language SDKs from protocol/openapi.yaml (design/10 §6).
# Requires: openapi-generator-cli (npm i -g @openapitools/openapi-generator-cli), Java 11+.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SPEC="$ROOT/protocol/openapi.yaml"
cd "$ROOT"

echo "==> python"
openapi-generator-cli generate -i "$SPEC" -g python -o sdks/python \
  --package-name mfs_sdk \
  --additional-properties=projectName=mfs-sdk,packageVersion=0.4.0,library=urllib3

echo "==> typescript (fetch)"
openapi-generator-cli generate -i "$SPEC" -g typescript-fetch -o sdks/typescript \
  --additional-properties=npmName=@mfs/sdk,npmVersion=0.4.0,supportsES6=true,typescriptThreePlus=true

echo "==> go"
openapi-generator-cli generate -i "$SPEC" -g go -o sdks/go \
  --additional-properties=packageName=mfssdk,packageVersion=0.4.0,isGoSubmodule=true,withGoMod=true
sed -i 's#github.com/GIT_USER_ID/GIT_REPO_ID/mfssdk#github.com/zilliztech/mfs-sdk-go#g' sdks/go/go.mod
grep -rl "GIT_USER_ID/GIT_REPO_ID/mfssdk" sdks/go | xargs -r sed -i 's#github.com/GIT_USER_ID/GIT_REPO_ID/mfssdk#github.com/zilliztech/mfs-sdk-go#g'

echo "==> java (okhttp-gson)"
openapi-generator-cli generate -i "$SPEC" -g java -o sdks/java \
  --additional-properties=groupId=io.zilliz,artifactId=mfs-sdk,artifactVersion=0.4.0,library=okhttp-gson,invokerPackage=io.zilliz.mfs,apiPackage=io.zilliz.mfs.api,modelPackage=io.zilliz.mfs.model

echo "done. SDKs in sdks/{python,typescript,go,java}"
