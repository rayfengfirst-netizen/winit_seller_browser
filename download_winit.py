"""
登录万邑通卖家后台后，按配置执行「点击 / 跳转 / 下载文件」。

两种方式（二选一）：
  A) 项目根目录放置 download_flow.json（或用环境变量 WINIT_DOWNLOAD_FLOW_FILE 指定路径）
  B) 简单模式：在 .env 里设置 WINIT_DOWNLOAD_PAGE_URL + WINIT_DOWNLOAD_NAME_PATTERN

运行：
  cd winit_seller_browser && source .venv/bin/activate
  python download_winit.py

下载文件默认保存到 downloads/（可用 WINIT_DOWNLOAD_DIR 修改）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import Page, sync_playwright

from login_winit import ROOT, _shutdown, login_on_page
from winit_accounts import list_winit_accounts, pick_active_account, resolve_download_dir_for_account
from winit_download_flow import load_flow, run_all_steps, run_flow_step

load_dotenv(ROOT / ".env")


def _simple_download_after_goto(page: Page, save_dir: Path) -> Path:
    page_url = os.environ.get("WINIT_DOWNLOAD_PAGE_URL", "").strip()
    if not page_url:
        print("请设置 WINIT_DOWNLOAD_PAGE_URL，或使用 download_flow.json", file=sys.stderr)
        sys.exit(6)
    page.goto(page_url, wait_until="domcontentloaded", timeout=90_000)
    try:
        page.wait_for_load_state("load", timeout=30_000)
    except Exception:
        pass
    pat = os.environ.get("WINIT_DOWNLOAD_NAME_PATTERN", r"导出|下载|Export|Download")
    step = {
        "action": "download",
        "role": "button",
        "name_pattern": pat,
        "timeout_ms": int(os.environ.get("WINIT_DOWNLOAD_TIMEOUT_MS", "120000")),
    }
    out = run_flow_step(page, step, save_dir=save_dir)
    if out is None:
        print("简单模式：未触发下载", file=sys.stderr)
        sys.exit(7)
    return out


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

    flow_path_str = os.environ.get("WINIT_DOWNLOAD_FLOW_FILE", "").strip()
    flow_path = Path(flow_path_str) if flow_path_str else ROOT / "download_flow.json"
    use_flow_file = flow_path.is_file()
    has_simple_url = bool(os.environ.get("WINIT_DOWNLOAD_PAGE_URL", "").strip())
    if not use_flow_file and not has_simple_url:
        print(
            "请二选一：\n"
            f"  1) 复制 download_flow.example.json 为 {flow_path} 并按页面改步骤；或\n"
            "  2) 在 .env 设置 WINIT_DOWNLOAD_PAGE_URL（简单模式）。\n"
            "详见 .env.example。",
            file=sys.stderr,
        )
        return 6

    save_dir = resolve_download_dir_for_account(account)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            locale="zh-CN",
            viewport={"width": 1360, "height": 900},
            ignore_https_errors=True,
            accept_downloads=True,
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

            if use_flow_file:
                steps = load_flow(flow_path)
                if not steps:
                    print("流程文件里没有 steps", file=sys.stderr)
                    return 5
                print(f"执行流程文件: {flow_path}，共 {len(steps)} 步")
                last = run_all_steps(page, steps, save_dir=save_dir)
                if last is None:
                    print("流程里没有 download 步骤，未保存文件", file=sys.stderr)
                    return 8
                print("已下载:", last)
                return 0

            out = _simple_download_after_goto(page, save_dir)
            print("已下载:", out)
            return 0
        finally:
            _shutdown(page, context, browser)


if __name__ == "__main__":
    raise SystemExit(main())
