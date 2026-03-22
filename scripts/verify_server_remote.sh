#!/usr/bin/env bash
# 在本机执行：通过 SSH 在远程默认路径跑 verify_server_run.sh
# 用法:
#   export WINIT_REMOTE=root@你的IP
#   ./scripts/verify_server_remote.sh
# 或:
#   WINIT_REMOTE=root@ip ./scripts/verify_server_remote.sh
set -euo pipefail
REMOTE="${WINIT_REMOTE:-${1:-}}"
if [[ -z "$REMOTE" ]]; then
  echo "请设置 WINIT_REMOTE，例如: export WINIT_REMOTE=root@8.218.58.28" >&2
  echo "或: WINIT_REMOTE=root@ip $0" >&2
  exit 1
fi

REMOTE_DIR="${WINIT_REMOTE_DIR:-/opt/winit-analytics}"

echo "==> SSH $REMOTE 执行 $REMOTE_DIR/scripts/verify_server_run.sh"
ssh -o BatchMode=yes -o ConnectTimeout=20 "$REMOTE" \
  "cd '$REMOTE_DIR' && bash scripts/verify_server_run.sh"
