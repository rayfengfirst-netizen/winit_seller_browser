#!/usr/bin/env bash
# 用法: ./scripts/verify_ssh.sh user@host
set -euo pipefail
TARGET="${1:-}"
if [[ -z "$TARGET" ]]; then
  echo "用法: $0 user@host" >&2
  echo "示例: $0 root@8.218.58.28" >&2
  exit 1
fi

echo "==> 测试 SSH: $TARGET"
ssh -o BatchMode=yes -o ConnectTimeout=15 "$TARGET" \
  'echo "SSH_OK host=$(hostname) user=$(whoami) date=$(date -Iseconds)"'
echo "==> 连通性正常"
