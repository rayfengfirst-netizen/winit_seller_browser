"""
第一步（仅此一步）：登录万邑通 → 进入澳大利亚相关首页。

后续你在本机用 Playwright/浏览器录好具体操作后，再把步骤合并进新项目或 download_flow。

运行：
  cd winit_seller_browser && source .venv/bin/activate
  WINIT_HEADLESS=false python step01_australia_index.py

默认进入的 URL 可用环境变量覆盖（见 .env.example）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import Page, sync_playwright

from login_winit import ROOT, _shutdown, login_on_page
from winit_accounts import list_winit_accounts, pick_active_account

load_dotenv(ROOT / ".env")

# 与产品约定：登录后先到这一页，再往下的点击/下载等你一步步加
DEFAULT_AUSTRALIA_INDEX_URL = "https://seller.winit.com.cn/Australia/index"


def main() -> int:
    headless = os.environ.get("WINIT_HEADLESS", "false").lower() in ("1", "true", "yes")
    try:
        form_wait_ms = int(os.environ.get("WINIT_FORM_WAIT_MS", "45000"))
    except ValueError:
        form_wait_ms = 45_000

    accs = list_winit_accounts()
    account = pick_active_account(accs)
    if account is None:
        print("请在 .env 中配置账号（WINIT_USERNAME/WINIT_PASSWORD 或 WINIT_ACCOUNT_*）", file=sys.stderr)
        return 1
    print(f"当前账号：{account.display_name()}  {account.username}", flush=True)

    australia_url = (
        os.environ.get("WINIT_AUSTRALIA_INDEX_URL", DEFAULT_AUSTRALIA_INDEX_URL).strip()
        or DEFAULT_AUSTRALIA_INDEX_URL
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            locale="zh-CN",
            viewport={"width": 1360, "height": 900},
            ignore_https_errors=True,
        )
        page: Page = context.new_page()
        try:
            code = login_on_page(
                page,
                user=account.username,
                password=account.password,
                form_wait_ms=form_wait_ms,
            )
            if code != 0:
                return code

            print("登录完成，正在进入澳大利亚页面：", australia_url)
            page.goto(australia_url, wait_until="domcontentloaded", timeout=90_000)
            try:
                page.wait_for_load_state("load", timeout=45_000)
            except Exception:
                pass
            try:
                page.wait_for_timeout(2500)
            except Exception:
                pass

            shot = ROOT / "screenshots" / "step01_australia_index.png"
            shot.parent.mkdir(parents=True, exist_ok=True)
            try:
                page.screenshot(path=str(shot), full_page=True)
            except Exception:
                pass

            try:
                print("当前 URL：", page.url)
            except Exception:
                pass
            print("第一步完成：已进入 Australia/index。截图：", shot)
            print("（后续操作请你本地用浏览器/Playwright 录一遍，把脚本或步骤发我再加。）")
            return 0
        finally:
            _shutdown(page, context, browser)


if __name__ == "__main__":
    raise SystemExit(main())
