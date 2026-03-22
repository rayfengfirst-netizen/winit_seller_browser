"""
用于本机录屏：登录 → 打开 Australia/index → 浏览器保持打开，直到你在终端按 Enter。

请在 Mac 本机终端执行（不要 ssh 到服务器；无图形界面的服务器无法这样录屏）。

  cd /Users/fengchangrui/Desktop/cursor/winit_seller_browser
  source .venv/bin/activate
  WINIT_HEADLESS=false python record_manual.py

然后用 QuickTime / OBS 等录你的屏幕即可在页面上随意操作。
"""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv
from playwright.sync_api import Page, sync_playwright

from login_winit import ROOT, _shutdown, login_on_page
from winit_accounts import list_winit_accounts, pick_active_account

load_dotenv(ROOT / ".env")

DEFAULT_AUSTRALIA_INDEX_URL = "https://seller.winit.com.cn/Australia/index"


def main() -> int:
    if os.environ.get("WINIT_HEADLESS", "").lower() in ("1", "true", "yes"):
        print("录屏请使用有界面浏览器：请设置 WINIT_HEADLESS=false 或不设置", file=sys.stderr)
        return 9

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

    print("即将打开浏览器：登录后进入", australia_url)
    print("录屏软件现在就可以开始录你的屏幕。")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
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
            page.goto(australia_url, wait_until="domcontentloaded", timeout=90_000)
            try:
                page.wait_for_load_state("load", timeout=45_000)
            except Exception:
                pass
            print("\n当前 URL：", page.url)
            print("—— 浏览器会一直保持打开，请你在页面上操作并录屏 ——")
            input("\n全部录完后，回到这个终端窗口按【回车】关闭浏览器…\n")
            return 0
        finally:
            _shutdown(page, context, browser)


if __name__ == "__main__":
    raise SystemExit(main())
