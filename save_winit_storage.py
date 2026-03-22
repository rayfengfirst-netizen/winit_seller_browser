"""
类似 myapp 的 login_dianxiaomi.py：打开浏览器 → 你手动登录万邑通 → 回车 → 保存登录态。

保存后，再运行 ./scripts/winit_codegen.sh 会自动带上 --load-storage，录制时不用再登录。

  cd winit_seller_browser && source .venv/bin/activate
  WINIT_HEADLESS=false python save_winit_storage.py
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

from login_winit import ROOT

load_dotenv(ROOT / ".env")

STORAGE_DIR = ROOT / ".playwright"
DEFAULT_URL = os.environ.get("WINIT_LOGIN_URL", "https://seller.winit.com.cn/User/login")


def main() -> None:
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    state_path = STORAGE_DIR / "winit_storage.json"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(locale="zh-CN", viewport={"width": 1360, "height": 900})
        page = context.new_page()
        page.goto(DEFAULT_URL, wait_until="domcontentloaded", timeout=90_000)
        print("请在浏览器里完成登录（如需）。登录完成后回到本终端按【回车】保存登录态…")
        input()
        context.storage_state(path=str(state_path))
        print(f"已保存: {state_path}")
        print("接下来可执行: ./scripts/winit_codegen.sh")
        context.close()
        browser.close()


if __name__ == "__main__":
    main()
