"""
抓取 step02 相关页面的「渲染后」HTML 与各 iframe 源码，便于离线看结构、优化选择器。

说明：
  - 无法在无账号环境下替你从万邑通服务器拉已登录页面；须在你本机跑本脚本（使用 .env 账号）。
  - SPA 的「查看网页源代码」只有空壳；这里保存的是 Playwright 渲染后的 page.content() / 各 frame 内容。

用法：
  cd winit_seller_browser && source .venv/bin/activate
  WINIT_HEADLESS=false python dump_step02_dom.py

可选环境变量：
  WINIT_DUMP_AFTER_AU_MS   进入 Australia 后额外等待毫秒（默认 4000）
  WINIT_DUMP_AFTER_EC_MS   进入导出中心后额外等待毫秒（默认 6000）
  WINIT_DUMP_PAUSE=1       每步保存完后 page.pause()，方便你在 DevTools 里再点一轮

输出目录：artifacts/dump_<时间戳>/
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

from login_winit import ROOT, _shutdown, login_on_page
from winit_accounts import list_winit_accounts, pick_active_account

load_dotenv(ROOT / ".env")

AUSTRALIA_INDEX_URL = "https://seller.winit.com.cn/Australia/index"
EXPORT_CENTER_INDEX_URL = os.environ.get(
    "WINIT_EXPORT_CENTER_URL",
    "https://seller.winit.com.cn/ExportCenter/index",
).strip()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", errors="replace")


def _dump_context(page, label: str, out: Path) -> None:
    _write(out / f"{label}_main_document.html", page.content())
    try:
        page.screenshot(path=str(out / f"{label}_viewport.png"), full_page=True)
    except Exception as e:
        _write(out / f"{label}_screenshot_error.txt", str(e))

    lines: list[str] = [f"# {label} frames @ {page.url}\n"]
    for i, fr in enumerate(page.frames):
        u = ""
        try:
            u = fr.url or ""
        except Exception:
            u = "?"
        nm = ""
        try:
            nm = fr.name or ""
        except Exception:
            pass
        lines.append(f"[{i}] name={nm!r} url={u}\n")
        try:
            html = fr.content()
            _write(out / f"{label}_frame_{i}.html", html)
        except Exception as e:
            _write(out / f"{label}_frame_{i}_SKIP.txt", f"{type(e).__name__}: {e}\n")
    _write(out / f"{label}_frames.txt", "".join(lines))


def run() -> int:
    headless = os.environ.get("WINIT_HEADLESS", "false").lower() in ("1", "true", "yes")
    pause = os.environ.get("WINIT_DUMP_PAUSE", "").lower() in ("1", "true", "yes")
    try:
        form_wait_ms = int(os.environ.get("WINIT_FORM_WAIT_MS", "45000"))
    except ValueError:
        form_wait_ms = 45_000
    after_au = int(os.environ.get("WINIT_DUMP_AFTER_AU_MS", "4000"))
    after_ec = int(os.environ.get("WINIT_DUMP_AFTER_EC_MS", "6000"))

    accs = list_winit_accounts()
    account = pick_active_account(accs)
    if account is None:
        print("请在 .env 中配置账号（WINIT_USERNAME/WINIT_PASSWORD 或 WINIT_ACCOUNT_*）", file=sys.stderr)
        return 1
    print(f"当前账号：{account.display_name()}  {account.username}", flush=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = ROOT / "artifacts" / f"dump_{stamp}"
    out.mkdir(parents=True, exist_ok=True)
    print("输出目录：", out, flush=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            locale="zh-CN",
            viewport={"width": 1920, "height": 1080},
            ignore_https_errors=True,
            accept_downloads=True,
        )
        page = context.new_page()
        try:
            code = login_on_page(
                page,
                user=account.username,
                password=account.password,
                form_wait_ms=form_wait_ms,
            )
            if code != 0:
                return code

            print("→ Australia …", flush=True)
            page.goto(AUSTRALIA_INDEX_URL, wait_until="domcontentloaded", timeout=90_000)
            page.wait_for_timeout(after_au)
            _dump_context(page, "01_australia", out)
            if pause:
                print("（暂停）可手动操作后按 Enter 继续…", flush=True)
                page.pause()

            print("→ ExportCenter …", flush=True)
            page.goto(EXPORT_CENTER_INDEX_URL, wait_until="domcontentloaded", timeout=90_000)
            page.wait_for_timeout(after_ec)
            _dump_context(page, "02_export_center", out)
            if pause:
                page.pause()

            _write(
                out / "README.txt",
                "将本目录打包 zip 发给协作者分析；勿提交含敏感信息的 HTML 到公开仓库。\n"
                "注意：部分页面内联脚本可能含账号/令牌等明文，分享前请全文检索 password、token 并删改；"
                "若已外泄请尽快改密码。\n",
            )
            print("完成。请查看上述目录内的 html、png、frames.txt。", flush=True)
            return 0
        finally:
            _shutdown(page, context, browser)


if __name__ == "__main__":
    raise SystemExit(run())
