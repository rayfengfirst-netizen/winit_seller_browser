#!/usr/bin/env bash
# 全流程：按 .env 中配置的每个 Winit 账号依次
#   浏览器登录 → 导出并下载 zip → 解压 xlsx → 写入 SQLite 当日快照（覆盖同日同账号）。
#
# 使用：
#   cd winit_seller_browser && ./scripts/run_full_inventory_sync.sh
#
# 可选环境变量（与 run_daily_winit_job.py 一致）：
#   WINIT_HEADLESS=true          服务器无界面
#   WINIT_SQLITE_PATH=...        库路径（默认 artifacts/winit_inventory.db）
#   WINIT_SNAPSHOT_DATE=YYYY-MM-DD
#   WINIT_SKIP_DOWNLOAD=1      仅入库（不打开浏览器），用各账号目录下最新 zip
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -x .venv/bin/python ]]; then
  PY=".venv/bin/python"
else
  PY="python3"
fi

echo "==> 项目根: $ROOT"
echo "==> Python: $PY"
echo "==> 开始 run_daily_winit_job.py（下载 → 解压 → 入库）"
exec "$PY" run_daily_winit_job.py
