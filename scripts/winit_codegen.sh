#!/usr/bin/env bash
# 在本机终端执行：启动 Playwright 录制器（与 myapp 里用 playwright codegen 一样）
# 用法：
#   ./scripts/winit_codegen.sh
#   ./scripts/winit_codegen.sh "https://seller.winit.com.cn/Australia/index"
#
# 窗口里左侧是网页，右侧/下方会实时生成 Python 代码；你点页面元素，代码里会出现对应 locator。
# 录完后把生成的代码复制出来即可。

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -d .venv ]]; then
  echo "请先: python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && playwright install chromium" >&2
  exit 1
fi
# shellcheck source=/dev/null
source .venv/bin/activate

URL="${1:-https://seller.winit.com.cn/Australia/index}"
STORAGE="${WINIT_STORAGE_STATE:-$ROOT/.playwright/winit_storage.json}"

if [[ -f "$STORAGE" ]]; then
  echo "==> 使用已保存登录态: $STORAGE"
  exec playwright codegen --load-storage="$STORAGE" "$URL"
else
  echo "==> 未找到登录态文件: $STORAGE"
  echo "==> 将打开空白会话；请在页面里先登录，再操作要录的步骤。"
  echo "==> （可选）先运行 python save_winit_storage.py 保存登录后再录，可自动带登录态。"
  exec playwright codegen "$URL"
fi
