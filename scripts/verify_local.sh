#!/usr/bin/env bash
# 在本机项目根目录执行：检查 Python、语法、依赖声明（不连服务器）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "==> 项目根: $ROOT"

if ! command -v python3 >/dev/null 2>&1; then
  echo "未找到 python3" >&2
  exit 1
fi

pyver=$(python3 -c 'import sys; print("%d.%d"%sys.version_info[:2])')
echo "==> Python: $pyver"
python3 -c 'import sys; assert sys.version_info >= (3, 9), "需要 Python 3.9+"'

echo "==> 校验 login_winit.py 语法（不写 .pyc）"
python3 -c "import ast, pathlib; ast.parse(pathlib.Path('login_winit.py').read_text(encoding='utf-8'))"

test -f requirements.txt || { echo "缺少 requirements.txt" >&2; exit 1; }
test -f .env.example || { echo "缺少 .env.example" >&2; exit 1; }

echo "LOCAL_OK"
