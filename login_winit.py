"""
Winit 卖家后台：用 Playwright 打开页面并尝试登录。

环境变量（.env）：
  WINIT_USERNAME / WINIT_PASSWORD  必填（账号 1，与多账号方案二选一或并存）
  多账号见 winit_accounts.py；WINIT_ACCOUNT_ID 选账号；WINIT_RUN_ALL_ACCOUNTS=1 顺序登录验证
  WINIT_HEADLESS                     可选 true/false，默认 false
  WINIT_LOGIN_URL                    可选，默认官方登录页

运行：
  cd winit_seller_browser && source .venv/bin/activate
  pip install -r requirements.txt && playwright install chromium
  cp .env.example .env
  python login_winit.py
"""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple, Union

from dotenv import load_dotenv
from playwright.sync_api import (
    Frame,
    Locator,
    Page,
    sync_playwright,
    Error as PlaywrightError,
    TimeoutError as PlaywrightTimeoutError,
)

from winit_accounts import list_winit_accounts, pick_active_account, run_all_winit_accounts_requested

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

DEFAULT_LOGIN_URL = "https://seller.winit.com.cn/User/login"
BASE_URL = "https://seller.winit.com.cn/"

# 主文档 / iframe 里找控件时用 Union（勿使用 Page | Frame，Python 3.9 会报错）
Container = Union[Page, Frame]

# 按常见后台栈多给几条；仍找不到再在开发者工具里加选择器
ACCOUNT_LOCATORS: List[str] = [
    'input#username',
    'input#account',
    'input[name="username"]',
    'input[name="account"]',
    'input[name="loginName"]',
    'input[type="email"]',
    'input[type="tel"]',
    'input.ant-input',
    'input.el-input__inner',
    'input[type="text"]',
    'input[placeholder*="账号" i]',
    'input[placeholder*="手机" i]',
    'input[placeholder*="邮箱" i]',
    'input[placeholder*="用户名" i]',
    'input[placeholder*="Account" i]',
    'input[placeholder*="User" i]',
    'input[name*="user" i]',
    'input[name*="account" i]',
    'input[name*="login" i]',
]

PASSWORD_LOCATORS: List[str] = [
    'input#password',
    'input[name="password"]',
    'input[type="password"]',
    'input.ant-input[type="password"]',
    'input.el-input__inner[type="password"]',
    'input[placeholder*="密码" i]',
    'input[placeholder*="Password" i]',
]


def _first_visible(container: Container, selectors: List[str], timeout_ms: int) -> Optional[Locator]:
    for sel in selectors:
        loc = container.locator(sel).first
        try:
            loc.wait_for(state="visible", timeout=timeout_ms)
            return loc
        except PlaywrightTimeoutError:
            continue
        except PlaywrightError:
            continue
    return None


def _wait_for_password_field_anywhere(page: Page, timeout_ms: int) -> bool:
    """
    SPA / 微前端下不要用 networkidle（会一直有长连接，等很久且不一定触发）。
    改为：直到任意 frame 里出现可见的密码框，或超时。
    """
    deadline = time.perf_counter() + timeout_ms / 1000.0
    while time.perf_counter() < deadline:
        frames = [page.main_frame] + [f for f in page.frames if f != page.main_frame]
        for fr in frames:
            loc = fr.locator('input[type="password"]').first
            try:
                if loc.is_visible(timeout=350):
                    return True
            except Exception:
                pass
        try:
            page.wait_for_timeout(120)
        except PlaywrightError:
            return False
    return False


def _click_login(container: Container) -> bool:
    candidates = [
        lambda c: c.get_by_role("button", name=re.compile(r"登录|登陆|Log\s*in|Sign\s*in", re.I)),
        lambda c: c.locator('button[type="submit"]'),
        lambda c: c.locator('input[type="submit"]'),
        lambda c: c.locator(".ant-btn-primary"),
        lambda c: c.locator("button.el-button--primary"),
        lambda c: c.get_by_text(re.compile(r"^登录$|^Log\s*in$", re.I)),
    ]
    for factory in candidates:
        target = factory(container)
        try:
            target.first.wait_for(state="visible", timeout=2500)
            target.first.click(timeout=15_000)
            return True
        except PlaywrightTimeoutError:
            continue
    return False


def _looks_logged_in(page: Page) -> bool:
    """先认 DOM 再认 URL：SPA 登录后 URL 可能仍是 /User/login。"""
    try:
        if page.get_by_text("待办事项", exact=False).first.is_visible(timeout=2000):
            return True
    except (PlaywrightTimeoutError, PlaywrightError):
        pass
    except Exception:
        pass
    try:
        if page.get_by_role("link", name=re.compile(r"^\s*首页\s*$")).first.is_visible(timeout=1500):
            return True
    except (PlaywrightTimeoutError, PlaywrightError):
        pass
    except Exception:
        pass
    try:
        if page.get_by_text("商品管理", exact=False).first.is_visible(timeout=1200):
            return True
    except (PlaywrightTimeoutError, PlaywrightError):
        pass
    except Exception:
        pass
    url = page.url.lower()
    if re.search(r"/index(?:/|$)|/home(?:/|$)|/dashboard|/main(?:/|$)", url):
        return True
    if "/user/login" in url:
        return False
    return False


def _find_account_password(
    page: Page, per_selector_ms: int = 700
) -> Tuple[Optional[Container], Optional[Locator], Optional[Locator]]:
    """密码框就绪后，每条选择器只等 per_selector_ms，避免 4s×N 条拖很久。"""
    frames_order = [page.main_frame] + [f for f in page.frames if f != page.main_frame]
    for fr in frames_order:
        pwd_loc = fr.locator('input[type="password"]').first
        try:
            if not pwd_loc.is_visible(timeout=500):
                continue
        except Exception:
            continue
        acc = _first_visible(fr, ACCOUNT_LOCATORS, timeout_ms=per_selector_ms)
        if acc is None:
            acc = fr.locator(
                'input[type="text"], input[type="email"], input[type="tel"], '
                'input.ant-input:not([type="password"]), input.el-input__inner:not([type="password"])'
            ).first
            try:
                acc.wait_for(state="visible", timeout=2000)
            except PlaywrightTimeoutError:
                acc = None
            except PlaywrightError:
                acc = None
        if acc is not None:
            return fr, acc, pwd_loc
    return None, None, None


def _wait_for_login_outcome(page: Page, timeout_sec: float = 45.0) -> None:
    """提交后轮询：后台 DOM 出现或 URL 离开登录页即结束等待。"""
    deadline = time.perf_counter() + timeout_sec
    while time.perf_counter() < deadline:
        if _looks_logged_in(page):
            return
        if "/user/login" not in page.url.lower():
            return
        try:
            page.wait_for_timeout(400)
        except PlaywrightError:
            return


def _probably_still_login_form(page: Page) -> bool:
    """仍像登录页：有可见密码框且不像已进后台。"""
    if _looks_logged_in(page):
        return False
    loc = page.locator('input[type="password"]').first
    try:
        return loc.is_visible(timeout=1500)
    except (PlaywrightTimeoutError, PlaywrightError):
        return False
    except Exception:
        return False


def _gather_visible_error_texts(page: Page) -> List[str]:
    """尽量抓取登录失败时页面上的提示（Ant/Element/aria-alert 等）。"""
    selectors = [
        '[role="alert"]',
        ".ant-message-notice-content",
        ".ant-notification-notice-description",
        ".ant-form-item-explain-error",
        ".el-message__content",
        ".el-form-item__error",
        ".el-notification__content",
        ".login-tips",
        ".login-error",
        '[class*="error-tip"]',
        '[class*="ErrorMessage"]',
    ]
    seen: set = set()
    out: List[str] = []

    def scan(container: Union[Page, Frame]) -> None:
        for sel in selectors:
            loc = container.locator(sel)
            try:
                count = loc.count()
            except PlaywrightError:
                continue
            for i in range(min(count, 8)):
                cell = loc.nth(i)
                try:
                    if not cell.is_visible(timeout=600):
                        continue
                    text = cell.inner_text(timeout=800).strip()
                except PlaywrightTimeoutError:
                    continue
                except PlaywrightError:
                    continue
                except Exception:
                    continue
                if not text or len(text) > 800:
                    continue
                key = text[:200]
                if key in seen:
                    continue
                seen.add(key)
                out.append(text)

    scan(page)
    for fr in page.frames:
        if fr != page.main_frame:
            scan(fr)
    return out


def _print_login_failure_feedback(page: Page) -> None:
    """登录未成功时把页面上能读到的错误文案打到 stderr。"""
    try:
        page.wait_for_timeout(1200)
    except PlaywrightError:
        pass
    hints = _gather_visible_error_texts(page)
    if hints:
        print("页面上的提示（请对照核对账号密码或验证码）：", file=sys.stderr)
        for h in hints:
            for line in h.splitlines():
                line = line.strip()
                if line:
                    print("  ", line, file=sys.stderr)
    else:
        print(
            "页面上未抓到明确错误文案（可能被验证码/风控拦截，或提示在 Shadow DOM 里）。",
            file=sys.stderr,
        )
    joined = " ".join(hints).lower()
    if any(
        k in joined
        for k in (
            "密码",
            "账号",
            "用户名",
            "错误",
            "失败",
            "不正确",
            "invalid",
            "incorrect",
            "wrong",
        )
    ):
        print("根据关键词判断：很可能是账号或密码不正确，或站点返回了校验错误。", file=sys.stderr)


def _shutdown(page: Optional[Page], context, browser) -> None:
    for obj in (page, context, browser):
        if obj is None:
            continue
        try:
            obj.close()
        except Exception:
            pass


def login_on_page(
    page: Page,
    *,
    user: str,
    password: str,
    form_wait_ms: int,
    login_url: Optional[str] = None,
) -> int:
    """
    在已有 Page 上完成登录（不创建浏览器）。
    返回码：0 成功；2 无表单；3 无按钮；4 登录失败。
    """
    if login_url is None:
        login_url = os.environ.get("WINIT_LOGIN_URL", DEFAULT_LOGIN_URL).strip() or DEFAULT_LOGIN_URL
    page.goto(login_url, wait_until="domcontentloaded", timeout=90_000)
    _wait_for_password_field_anywhere(page, form_wait_ms)

    if _looks_logged_in(page):
        print("已登录状态，无需填表。当前 URL：", page.url)
        return 0

    container, acc, pwd = _find_account_password(page)
    if acc is None or pwd is None:
        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=90_000)
        _wait_for_password_field_anywhere(page, min(form_wait_ms, 30_000))
        if _looks_logged_in(page):
            print("从首页判断已登录。当前 URL：", page.url)
            return 0
        container, acc, pwd = _find_account_password(page)

    if acc is None or pwd is None:
        shot = ROOT / "screenshots" / "login_form_not_found.png"
        shot.parent.mkdir(parents=True, exist_ok=True)
        try:
            page.screenshot(path=str(shot), full_page=True)
        except PlaywrightError:
            pass
        print(
            f"未找到账号或密码框，已截图：{shot}\n"
            "请用开发者工具查看 input 的 id/name/class，加到 ACCOUNT_LOCATORS / PASSWORD_LOCATORS。",
            file=sys.stderr,
        )
        return 2

    acc.click()
    acc.fill("")
    acc.fill(user)
    pwd.click()
    pwd.fill("")
    pwd.fill(password)

    clicked = _click_login(container)
    if not clicked:
        try:
            pwd.press("Enter")
            clicked = True
        except PlaywrightError:
            pass
    if not clicked:
        shot = ROOT / "screenshots" / "login_button_not_found.png"
        shot.parent.mkdir(parents=True, exist_ok=True)
        try:
            page.screenshot(path=str(shot), full_page=True)
        except PlaywrightError:
            pass
        print(f"未找到登录按钮，已截图：{shot}", file=sys.stderr)
        return 3

    try:
        page.wait_for_load_state("load", timeout=20_000)
    except PlaywrightTimeoutError:
        pass
    _wait_for_login_outcome(page, timeout_sec=45.0)

    if _looks_logged_in(page):
        try:
            print("登录成功（按页面内容判断）。当前 URL：", page.url)
        except PlaywrightError:
            print("登录成功（按页面内容判断）。")
        return 0

    if _probably_still_login_form(page) or "/user/login" in page.url.lower():
        print(
            "登录未成功：未检测到后台界面。当前 URL：",
            page.url,
            file=sys.stderr,
        )
        _print_login_failure_feedback(page)
        shot = ROOT / "screenshots" / "login_still_on_login_page.png"
        shot.parent.mkdir(parents=True, exist_ok=True)
        try:
            page.screenshot(path=str(shot), full_page=True)
        except PlaywrightError:
            pass
        print(f"已保存截图便于人工查看：{shot}", file=sys.stderr)
        return 4

    try:
        print("已离开登录 URL。当前 URL：", page.url)
    except PlaywrightError:
        print("已离开登录 URL。")
    return 0


def _run_login_for_credentials(user: str, password: str) -> int:
    headless = os.environ.get("WINIT_HEADLESS", "false").lower() in ("1", "true", "yes")
    try:
        form_wait_ms = int(os.environ.get("WINIT_FORM_WAIT_MS", "45000"))
    except ValueError:
        form_wait_ms = 45_000

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            locale="zh-CN",
            viewport={"width": 1360, "height": 900},
            ignore_https_errors=True,
        )
        page: Optional[Page] = None
        try:
            page = context.new_page()
            return login_on_page(page, user=user, password=password, form_wait_ms=form_wait_ms)
        finally:
            _shutdown(page, context, browser)


def run() -> int:
    accs = list_winit_accounts()
    if not accs:
        print(
            "请在 .env 中配置账号：WINIT_USERNAME/WINIT_PASSWORD 或 WINIT_ACCOUNT_*（见 .env.example）",
            file=sys.stderr,
        )
        return 1
    if run_all_winit_accounts_requested():
        exit_code = 0
        for account in accs:
            print(
                f"\n{'=' * 60}\n登录验证：账号 {account.display_name()}  {account.username}\n{'=' * 60}\n",
                flush=True,
            )
            code = _run_login_for_credentials(account.username, account.password)
            if code != 0:
                exit_code = code
        return exit_code
    account = pick_active_account(accs)
    if account is None:
        return 1
    print(f"当前账号：{account.display_name()}  {account.username}", flush=True)
    return _run_login_for_credentials(account.username, account.password)


if __name__ == "__main__":
    raise SystemExit(run())
