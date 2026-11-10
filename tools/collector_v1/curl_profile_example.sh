#!/usr/bin/env sh
set -eu

# 这个最小 curl 示例只检查 Token 对应的机器人，不上传数据。
# 完整上传请使用 hkc_upload.sh，它会先检查 ZIP、SHA-256 和重复数据。
: "${HKC_SERVER:?请设置 HKC_SERVER，例如 http://192.168.0.163:18000}"
: "${HKC_TOKEN_FILE:?请设置 HKC_TOKEN_FILE}"

TOKEN=$(tr -d '\r\n' < "$HKC_TOKEN_FILE")
printf 'header = "Authorization: Bearer %s"\n' "$TOKEN" | \
curl --config - --fail-with-body --silent --show-error \
  "$HKC_SERVER/api/v1/collector/profile"
printf '\n'
