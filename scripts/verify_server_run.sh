#!/usr/bin/env bash
# 在服务器上、项目目录内执行（默认 /opt/winit-analytics）
set -euo pipefail
ROOT="${WINIT_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$ROOT"
echo "==> WINIT_ROOT=$ROOT"

if [[ ! -d .venv ]]; then
  echo "缺少 .venv，请先 python3 -m venv .venv && pip install -r requirements.txt" >&2
  exit 1
fi
# shellcheck source=/dev/null
source .venv/bin/activate

python --version
pip show playwright >/dev/null || { echo "请 pip install -r requirements.txt" >&2; exit 1; }
python -c "from playwright.sync_api import sync_playwright; print('playwright import ok')"

if [[ ! -f .env ]]; then
  echo "缺少 .env，请 cp .env.example .env 并填写账号密码" >&2
  exit 1
fi

export WINIT_HEADLESS="${WINIT_HEADLESS:-true}"
echo "==> 执行 login_winit.py (WINIT_HEADLESS=$WINIT_HEADLESS)"
set +e
python login_winit.py
code=$?
set -e
echo "==> 退出码: $code"
exit "$code"
