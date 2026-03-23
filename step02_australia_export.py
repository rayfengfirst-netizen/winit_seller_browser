"""
第二步：登录 → 澳大利亚首页 → 导出「SKU 仓库级库存」→ 判定弹窗已关（任务已提交）
→ 打开 ExportCenter/index → 等待该行「导出成功」→ 点击「保存到本地」下载。

依赖 .env 中的账号配置（见 winit_accounts.py）：默认 WINIT_USERNAME / WINIT_PASSWORD；
多账号用 WINIT_ACCOUNT_2_USERNAME 等。单次用 WINIT_ACCOUNT_ID=2；全部顺序执行 WINIT_RUN_ALL_ACCOUNTS=1。

运行：
  cd winit_seller_browser && source .venv/bin/activate
  WINIT_HEADLESS=false python step02_australia_export.py

定位「哪一步慢」：
  WINIT_STEP02_PROFILE=1  — 终端输出每阶段 +累计秒 Δ间隔秒
  WINIT_STEP02_TRACE=1    — 结束生成 artifacts/step02_trace_*.zip，执行 playwright show-trace <该文件>

下载保存到 downloads/，文件名带时间戳。
"""

from __future__ import annotations

import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv
from playwright.sync_api import Frame, FrameLocator, Page, sync_playwright
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from login_winit import ROOT, _shutdown, login_on_page
from winit_accounts import (
    WinitAccount,
    list_winit_accounts,
    pick_active_account,
    resolve_download_dir_for_account,
    run_all_winit_accounts_requested,
)

load_dotenv(ROOT / ".env")

# 性能诊断：run() 内设置；各阶段调用 _prof_mark("…")
_profiler: Optional["Step02Profiler"] = None


class Step02Profiler:
    """WINIT_STEP02_PROFILE=1 时打印每阶段耗时（累计 + 与上一标记的间隔）。"""

    def __init__(self) -> None:
        self._t0 = time.perf_counter()
        self._last = self._t0

    def mark(self, label: str) -> None:
        now = time.perf_counter()
        total = now - self._t0
        delta = now - self._last
        print(
            f"[STEP02_PROFILE] +{total:7.2f}s  Δ{delta:6.2f}s  {label}",
            flush=True,
        )
        self._last = now


def _prof_mark(label: str) -> None:
    p = _profiler
    if p is not None:
        p.mark(label)


# 注意：以下入口/匹配配置需在运行时读取，便于调用方临时覆盖环境变量。
_DEFAULT_ENTRY_URL = "https://seller.winit.com.cn/Australia/index"
EXPORT_CENTER_INDEX_URL = os.environ.get(
    "WINIT_EXPORT_CENTER_URL",
    "https://seller.winit.com.cn/ExportCenter/index",
).strip()
PAGE_TAB_IFRAME = "#pageTabContent iframe"
_DEFAULT_EXPORT_DIALOG_RADIO_LABEL = "导出SKU仓库级库存 按SKU导出国家内每个仓库的库存数据"
# 导出中心列表里匹配任务行（避免写死日期）；可用环境变量覆盖
_DEFAULT_EXPORT_ROW_MATCH = "海外仓库存"
# 进入导出中心后该行可能先「正在生成」，站点自动刷新后才变为可下载状态
EXPORT_ROW_READY_STATUS = os.environ.get("WINIT_EXPORT_READY_STATUS", "导出成功").strip() or "导出成功"
# 站点实际多为「保存本地」（见 dump #exportDataList）；可用 | 分隔多个，依次尝试
EXPORT_SAVE_LOCAL_TEXT = os.environ.get("WINIT_EXPORT_SAVE_TEXT", "").strip()
EXPORT_FAIL_STATUS = os.environ.get("WINIT_EXPORT_FAIL_STATUS", "导出失败").strip()

# 隐藏 JSON 里任务完成标记（抓包 HTML 为 "status":"DONE"）
_EXPORT_JSON_DONE_MARKERS = ('"status":"DONE"', '"status":"Done"')


def _entry_url() -> str:
    return os.environ.get("WINIT_STEP02_ENTRY_URL", _DEFAULT_ENTRY_URL).strip() or _DEFAULT_ENTRY_URL


def _row_match_text() -> str:
    return os.environ.get("WINIT_EXPORT_ROW_MATCH", _DEFAULT_EXPORT_ROW_MATCH).strip() or _DEFAULT_EXPORT_ROW_MATCH


def _export_dialog_radio_label() -> str:
    return (
        os.environ.get("WINIT_EXPORT_DIALOG_RADIO_LABEL", _DEFAULT_EXPORT_DIALOG_RADIO_LABEL).strip()
    )


def _save_link_text_candidates() -> list[str]:
    if EXPORT_SAVE_LOCAL_TEXT:
        return [x.strip() for x in EXPORT_SAVE_LOCAL_TEXT.split("|") if x.strip()]
    return ["保存本地", "保存到本地"]


def _wait_ms_from_env(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, str(default)))
    except ValueError:
        return default


def _dismiss_modal_if_any(page: Page) -> None:
    try:
        page.locator(".winitd-modal > div").first.click(timeout=4000)
    except PlaywrightTimeoutError:
        pass
    except PlaywrightError:
        pass


def _iframe_nth_frame(page: Page, index: int, timeout_ms: int = 45_000) -> Optional[Frame]:
    """主文档上第 index 个 iframe 的 Frame（index 从 0 起）。"""
    loc = page.locator("iframe").nth(index)
    try:
        loc.wait_for(state="attached", timeout=timeout_ms)
    except PlaywrightTimeoutError:
        return None
    try:
        fr = loc.content_frame
        if fr is not None:
            return fr
    except Exception:
        pass
    try:
        handle = loc.element_handle(timeout=5000)
        if handle is not None:
            return handle.content_frame()
    except Exception:
        pass
    return None


def _debug_step02(page: Page, tag: str) -> None:
    if os.environ.get("WINIT_STEP02_DEBUG", "").lower() not in ("1", "true", "yes"):
        return
    d = ROOT / "screenshots"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"step02_debug_{tag}.png"
    try:
        page.screenshot(path=str(path), full_page=True)
        print(f"[DEBUG] 截图 {path}", flush=True)
    except Exception as e:
        print(f"[DEBUG] 截图失败 {e}", flush=True)
    for i, fr in enumerate(page.frames):
        try:
            u = fr.url[:120] if fr.url else ""
        except Exception:
            u = "?"
        print(f"[DEBUG] frame[{i}] {u}", flush=True)


def _ensure_step02_subtab_if_configured(page: Page) -> None:
    """
    若业务区有国家/仓库子 Tab，未选中时「导出」可能不出现。
    仅当 .env 设置 WINIT_STEP02_SUBTAB 时才点击（例如 澳大利亚、美国），避免误切国家。
    """
    name = os.environ.get("WINIT_STEP02_SUBTAB", "").strip()
    if not name:
        return
    esc = re.escape(name)
    pat = re.compile(rf"^{esc}$")
    for fr in page.frames:
        for fn in (
            lambda f: f.get_by_role("tab", name=pat).first.click(timeout=2500),
            lambda f: f.locator("[role='tab']").filter(has_text=pat).first.click(timeout=2500),
        ):
            try:
                fn(fr)
                page.wait_for_timeout(1000)
                return
            except Exception:
                continue


def _try_click_query_in_frames(page: Page) -> None:
    """需设置 WINIT_STEP02_CLICK_QUERY=1 时才会点「查询」（避免误点其它页面的查询）。"""
    if os.environ.get("WINIT_STEP02_CLICK_QUERY", "").lower() not in ("1", "true", "yes"):
        return
    for fr in page.frames:
        try:
            fr.get_by_role("button", name=re.compile(r"^查询$")).first.click(timeout=2500)
            page.wait_for_timeout(2000)
            return
        except Exception:
            continue


def _try_click_export_text_force(fr: Frame) -> bool:
    """文案「导出」在子节点里时，get_by_text + force 往往比 button filter 稳。"""
    vis_ms = _wait_ms_from_env("WINIT_STEP02_EXPORT_TEXT_PROBE_MS", 200)
    for exact in (True, False):
        for i in range(8):
            try:
                loc = fr.get_by_text("导出", exact=exact).nth(i)
                if not loc.is_visible(timeout=vis_ms):
                    continue
                loc.scroll_into_view_if_needed(timeout=3000)
                loc.click(timeout=10_000, force=True)
                return True
            except Exception:
                continue
    return False


def _try_click_export_xpath(fr: Frame) -> bool:
    xps = [
        "xpath=.//button[contains(normalize-space(.),'导出')]",
        "xpath=.//span[contains(normalize-space(.),'导出')]/ancestor::button[1]",
        "xpath=.//*[self::button or self::a][contains(.,'导出')]",
    ]
    for xp in xps:
        try:
            el = fr.locator(xp).first
            if el.is_visible(timeout=400):
                el.scroll_into_view_if_needed(timeout=2000)
                el.click(timeout=8000, force=True)
                return True
        except Exception:
            continue
    return False


def _frame_is_plausible_for_export(fr: Frame, page: Page) -> bool:
    """跳过 about:blank、sharedWorker 等，避免每轮对每个 frame 做大量慢探测。"""
    if fr is page.main_frame:
        return True
    try:
        u = fr.url or ""
    except Exception:
        return False
    if not u.strip():
        return False
    ul = u.lower()
    if ul.startswith("about:"):
        return False
    if "sharedworker" in ul:
        return False
    return True


def _frames_for_export_scan(page: Page) -> List[Frame]:
    """
    业务 iframe 优先，主文档放后（导出一般在 #pageTabContent 内）。
    按 URL 关键字排序，Australia / 库存相关 frame 先扫，避免在无关 iframe 上耗满多段 click 超时。
    """
    priority = (
        "australia",
        "inventory",
        "oversea",
        "sellerstatic",
        "winit.com.cn",
    )
    scored: list[tuple[tuple[int, str], Frame]] = []
    for fr in page.frames:
        if fr is page.main_frame:
            continue
        if not _frame_is_plausible_for_export(fr, page):
            continue
        u = (fr.url or "").lower()
        rank = len(priority)
        for i, key in enumerate(priority):
            if key in u:
                rank = i
                break
        scored.append(((rank, u), fr))
    scored.sort(key=lambda x: x[0])
    out = [fr for _, fr in scored]
    out.append(page.main_frame)
    return out


def _frame_has_export_candidate(fr: Frame, gate_ms: int) -> bool:
    """
    本 frame 内是否有「可点的导出控件」且当前可见。
    勿用 get_by_text().attached：侧栏/模板里的「导出」会让门禁误通过，随后 5 段 click 仍各等满超时，
    单轮即可 ~50s+。仅认 button / 链接 / ant-btn / role=button。
    """
    loc = fr.locator(
        'button, a, .ant-btn, [role="button"]'
    ).filter(has_text=re.compile(r"导出")).first
    try:
        loc.wait_for(state="visible", timeout=gate_ms)
        return True
    except PlaywrightTimeoutError:
        return False


def _ensure_overseas_inventory_tab(page: Page) -> None:
    """若顶栏有「海外仓库存」Tab 且未选中，则点一下；不用全页 get_by_text，避免误点侧栏等。"""
    after_ms = _wait_ms_from_env("WINIT_STEP02_AFTER_OVERSEAS_TAB_MS", 700)
    try:
        tab = page.locator(".ant-tabs-nav .ant-tabs-tab").filter(has_text=re.compile(r"^海外仓库存")).first
        if tab.is_visible(timeout=2000):
            try:
                tab.click(timeout=3000)
                page.wait_for_timeout(after_ms)
            except Exception:
                pass
    except Exception:
        pass


def _try_click_export_nested_tab_iframe(page: Page) -> bool:
    """
    #pageTabContent 下常见「iframe 里再套一层 iframe」，导出按钮在内层。
    原先每次尝试 click(timeout=4500)×4，单轮轮询即可浪费 ~18s；改为先短等 visible 再点，失败则用短超时探测。
    """
    vis_ms = _wait_ms_from_env("WINIT_STEP02_NESTED_EXPORT_VISIBLE_MS", 2200)
    click_ms = _wait_ms_from_env("WINIT_STEP02_NESTED_EXPORT_CLICK_MS", 15000)
    probe_ms = _wait_ms_from_env("WINIT_STEP02_NESTED_EXPORT_PROBE_MS", 750)

    def _try_scope(scope) -> bool:
        """scope: FrameLocator（内层或外层 iframe 区域）。"""
        ctrl = scope.locator("button, a, .ant-btn").filter(has_text=re.compile(r"导出")).first
        try:
            ctrl.wait_for(state="visible", timeout=vis_ms)
            ctrl.click(timeout=click_ms)
            return True
        except PlaywrightTimeoutError:
            pass
        except Exception:
            pass
        for fn in (
            lambda: scope.locator("button").filter(has_text=re.compile(r"导出")).first.click(timeout=probe_ms),
            lambda: scope.get_by_text("导出", exact=False).first.click(timeout=probe_ms, force=True),
        ):
            try:
                fn()
                return True
            except Exception:
                continue
        return False

    try:
        outer = page.frame_locator(PAGE_TAB_IFRAME)
        inner = outer.frame_locator("iframe").first
        if _try_scope(inner):
            return True
        if _try_scope(outer):
            return True
    except Exception:
        pass
    return False


def _try_click_export_on_main_document(page: Page, click_timeout_ms: int) -> bool:
    """
    壳层 + 微前端时，「导出」按钮常挂在主文档（Ant Portal / 顶栏），不在 #pageTabContent iframe 里。
    若每轮先跑嵌套 iframe 的 visible+probe（~7s/轮），再把主文档点击放在最后，会出现多轮空转 ~30s+。
    """
    try:
        page.locator("button").filter(has_text=re.compile(r"导出")).first.click(
            timeout=click_timeout_ms, force=True
        )
        return True
    except Exception:
        pass
    try:
        page.get_by_text("导出", exact=False).first.click(timeout=click_timeout_ms, force=True)
        return True
    except Exception:
        return False


def _try_click_export_in_frame(fr: Frame) -> bool:
    """在单个 Frame 内尝试多种方式点「导出」：先快后慢，避免在无关 frame 上耗太久。"""
    gate_ms = _wait_ms_from_env("WINIT_STEP02_EXPORT_FRAME_GATE_MS", 600)
    if not _frame_has_export_candidate(fr, gate_ms):
        return False
    click_to = _wait_ms_from_env("WINIT_STEP02_EXPORT_IN_FRAME_CLICK_MS", 1800)
    quick = [
        lambda: fr.locator("button").filter(has_text=re.compile(r"导出")).first.click(timeout=click_to),
        lambda: fr.locator(".ant-btn").filter(has_text=re.compile(r"导出")).first.click(timeout=click_to),
        lambda: fr.get_by_role("button", name=re.compile(r"^\s*导出\s*$")).first.click(timeout=click_to),
        lambda: fr.get_by_role("button", name="导出").first.click(timeout=click_to),
        lambda: fr.locator("a").filter(has_text=re.compile(r"导出")).first.click(timeout=click_to),
    ]
    for fn in quick:
        try:
            fn()
            return True
        except Exception:
            continue
    if _try_click_export_xpath(fr):
        return True
    if _try_click_export_text_force(fr):
        return True
    return False


def _wait_for_export_button_in_tab_iframe(page: Page, timeout_ms: int) -> bool:
    """
    用「导出」按钮可见替代部分固定 sleep；失败则返回 False，由外层轮询继续。
    """
    outer = page.frame_locator(PAGE_TAB_IFRAME)
    try:
        inner = outer.frame_locator("iframe").first
        inner.locator("button, a, .ant-btn").filter(has_text=re.compile(r"导出")).first.wait_for(
            state="visible", timeout=timeout_ms
        )
        return True
    except Exception:
        pass
    try:
        outer.locator("button, a, .ant-btn").filter(has_text=re.compile(r"导出")).first.wait_for(
            state="visible", timeout=min(timeout_ms, 12_000)
        )
        return True
    except Exception:
        return False


def _click_export_by_scanning_all_frames(page: Page) -> None:
    """
    不只用 #pageTabContent 第一层 iframe：万邑通可能嵌套多层 iframe，
    「导出」在子 frame 里时，frame_locator 单路径会找不到。
    遍历 page.frames（含主文档 + 全部 iframe）直到点到为止。
    """
    _prof_mark("导出：开始扫描并点击「导出」")
    poll_ms = _wait_ms_from_env("WINIT_STEP02_EXPORT_POLL_MS", 120_000)
    step_sleep = _wait_ms_from_env("WINIT_STEP02_AFTER_GOTO_WAIT_MS", 1200)
    attach_ms = _wait_ms_from_env("WINIT_STEP02_IFRAME_WAIT_MS", 90_000)
    iframe_hint_ms = _wait_ms_from_env("WINIT_STEP02_EXPORT_BUTTON_HINT_MS", 18_000)
    poll_step_ms = _wait_ms_from_env("WINIT_STEP02_EXPORT_POLL_STEP_MS", 280)
    hint_cap = _wait_ms_from_env("WINIT_STEP02_EXPORT_BUTTON_HINT_CAP_MS", 4500)

    print("正在定位并点击「导出」…", flush=True)

    try:
        page.locator(PAGE_TAB_IFRAME).first.wait_for(state="attached", timeout=attach_ms)
    except PlaywrightTimeoutError:
        pass

    # iframe 一挂上就点嵌套「导出」，常比先 sleep 再轮询快几十秒
    _prof_mark("导出：tab iframe attach 后立即试嵌套")
    if _try_click_export_nested_tab_iframe(page):
        print("已通过 #pageTabContent 内层 iframe 点击「导出」。", flush=True)
        _prof_mark("导出：嵌套 iframe 立即成功")
        return

    page.wait_for_timeout(step_sleep)
    try:
        page.locator(PAGE_TAB_IFRAME).first.scroll_into_view_if_needed(timeout=10_000)
    except Exception:
        pass
    _ensure_overseas_inventory_tab(page)
    page.wait_for_timeout(_wait_ms_from_env("WINIT_STEP02_AFTER_OVERSEAS_GAP_MS", 400))
    _ensure_step02_subtab_if_configured(page)
    page.wait_for_timeout(_wait_ms_from_env("WINIT_STEP02_AFTER_SUBTAB_MS", 350))
    _debug_step02(page, "before_export_loop")

    _wait_for_export_button_in_tab_iframe(page, min(iframe_hint_ms, hint_cap))

    main_preflight_ms = _wait_ms_from_env("WINIT_STEP02_EXPORT_MAIN_PREFLIGHT_MS", 3200)
    main_probe_ms = _wait_ms_from_env("WINIT_STEP02_EXPORT_MAIN_PROBE_MS", 1200)
    main_fallback_ms = _wait_ms_from_env("WINIT_STEP02_EXPORT_MAIN_CLICK_MS", 2400)

    if _try_click_export_on_main_document(page, main_preflight_ms):
        print("已在主文档点击「导出」。", flush=True)
        _prof_mark("导出：主文档点到「导出」（预检）")
        return

    deadline = time.monotonic() + poll_ms / 1000.0
    last_err: Optional[Exception] = None
    iteration = 0
    last_log = 0.0
    while time.monotonic() < deadline:
        iteration += 1
        if iteration % 8 == 0:
            _try_click_query_in_frames(page)
            _ensure_step02_subtab_if_configured(page)
            _debug_step02(page, f"poll_{iteration}")

        if _try_click_export_on_main_document(page, main_probe_ms):
            print("已在主文档点击「导出」。", flush=True)
            _prof_mark("导出：主文档点到「导出」（轮询优先）")
            return

        if _try_click_export_nested_tab_iframe(page):
            _prof_mark("导出：轮询中通过嵌套 iframe 点到「导出」")
            return
        for fr in _frames_for_export_scan(page):
            if _try_click_export_in_frame(fr):
                _prof_mark("导出：轮询中通过某 frame 点到「导出」")
                return
        if _try_click_export_on_main_document(page, main_fallback_ms):
            print("已在主文档点击「导出」。", flush=True)
            _prof_mark("导出：主文档点到「导出」（轮询补试）")
            return
        now = time.monotonic()
        if now - last_log >= 6.0:
            print("仍在等待「导出」按钮可点（页面或 iframe 尚未就绪）…", flush=True)
            last_log = now
        page.wait_for_timeout(poll_step_ms)

    raise RuntimeError(
        f"{poll_ms}ms 内未在任意 frame 中点到「导出」。last={last_err!r}"
    )


def _export_dialog_act(page: Page, tab_frame: FrameLocator) -> None:
    """导出弹窗里的单选 + 确定；可能在 iframe 或 Ant Design Portal（主文档）。"""
    if os.environ.get("WINIT_EXPORT_DIALOG_SKIP", "").lower() in ("1", "true", "yes"):
        print("已跳过导出弹窗确认（WINIT_EXPORT_DIALOG_SKIP）", flush=True)
        return

    radio_label = _export_dialog_radio_label()

    def _radio_then_ok(use_page: bool) -> None:
        loc = page if use_page else tab_frame
        if radio_label:
            loc.get_by_role("radio", name=radio_label).check(timeout=20_000)
        loc.get_by_role("button", name="确定").click(timeout=20_000)

    try:
        _radio_then_ok(use_page=False)
    except Exception:
        try:
            _radio_then_ok(use_page=True)
        except Exception:
            if radio_label:
                page.get_by_role("dialog").get_by_role("radio", name=radio_label).check(timeout=15_000)
            page.get_by_role("dialog").get_by_role("button", name="确定").click(timeout=15_000)


def _export_config_dialog_still_visible(page: Page, tab_frame: FrameLocator) -> bool:
    """只认「导出 SKU 仓库级库存」这类配置弹窗，避免把其它 dialog 算进去。"""
    hint = re.compile(r"SKU|仓库级|导出SKU|按SKU")
    roots: list = [page, tab_frame]
    for root in roots:
        try:
            d = root.locator('[role="dialog"]').filter(has_text=hint)
            if d.count() > 0 and d.first.is_visible(timeout=400):
                return True
        except Exception:
            continue
    for fr in page.frames:
        if fr is page.main_frame:
            continue
        try:
            d = fr.locator('[role="dialog"]').filter(has_text=hint)
            if d.count() > 0 and d.first.is_visible(timeout=400):
                return True
        except Exception:
            continue
    return False


def _wait_export_task_submitted(page: Page, tab_frame: FrameLocator) -> None:
    """
    如何标定「点击导出并成功提交任务」：
    1) _export_dialog_act 已无异常完成（已选 SKU 仓库级库存并点了「确定」）。
    2) 等待「导出 SKU 仓库级库存」配置弹窗从可见变为不可见（关窗 ≈ 前端已提交并结束该步）。

    若站点关窗很慢，可加大 WINIT_STEP02_EXPORT_SUCCESS_WAIT_MS。
    调试可设 WINIT_STEP02_SKIP_SUCCESS_GATE=1 跳过本步（不推荐生产）。
    """
    if os.environ.get("WINIT_STEP02_SKIP_SUCCESS_GATE", "").lower() in ("1", "true", "yes"):
        print("已跳过「弹窗关闭」判定（WINIT_STEP02_SKIP_SUCCESS_GATE）", flush=True)
        return

    settle = _wait_ms_from_env("WINIT_STEP02_AFTER_OK_MS", 800)
    page.wait_for_timeout(settle)

    ms = _wait_ms_from_env("WINIT_STEP02_EXPORT_SUCCESS_WAIT_MS", 90_000)
    poll_ms = _wait_ms_from_env("WINIT_STEP02_EXPORT_SUCCESS_POLL_MS", 200)
    deadline = time.monotonic() + ms / 1000.0
    while time.monotonic() < deadline:
        if not _export_config_dialog_still_visible(page, tab_frame):
            print("判定：导出配置弹窗已关闭，视为任务已提交。", flush=True)
            return
        page.wait_for_timeout(poll_ms)

    raise RuntimeError(
        f"{ms}ms 内导出配置弹窗仍可见，无法判定已提交（可加大 WINIT_STEP02_EXPORT_SUCCESS_WAIT_MS，"
        "或看页面是否报错；调试可临时设 WINIT_STEP02_SKIP_SUCCESS_GATE=1）"
    )


def _goto_export_center(page: Page) -> None:
    print("正在打开导出中心页面…", flush=True)
    page.goto(EXPORT_CENTER_INDEX_URL, wait_until="domcontentloaded", timeout=90_000)
    load_ms = _wait_ms_from_env("WINIT_STEP02_EXPORT_CENTER_LOAD_MS", 20_000)
    try:
        page.wait_for_load_state("load", timeout=load_ms)
    except PlaywrightTimeoutError:
        pass
    print("当前 URL（导出中心）：", page.url, flush=True)


def _frame_with_matching_export_row(page: Page, match: str) -> Optional[Frame]:
    """任意 frame 里已有含 match 文案的表格行时，直接锁定该 frame（避免盲等 iframe）。"""
    pat = re.compile(re.escape(match))
    for fr in page.frames:
        try:
            loc = fr.get_by_role("row").filter(has_text=pat)
            if loc.count() > 0:
                return fr
        except Exception:
            continue
    return None


def _export_list_inner_frame(page: Page) -> Optional[Frame]:
    """
    导出中心列表可能在主文档或 iframe。
    导出中心页往往没有 #pageTabContent iframe：若对其 wait_for 3s，轮询里每一轮都会卡满超时，表现为「URL 对了却很久不往下走」。
    """
    page_url = (page.url or "").lower()
    on_export_center = "exportcenter" in page_url

    # 必须放在最前：全帧 × get_by_role("row").count() 在导出中心大页面上极慢，且列表实为 #exportDataList ul/li 无 row
    if on_export_center:
        _prof_mark("导出中心：跳过 iframe/role=row 扫描，使用主文档")
        return page.main_frame

    hit = _frame_with_matching_export_row(page, _row_match_text())
    if hit is not None:
        return hit

    for fr in page.frames:
        if fr is page.main_frame:
            continue
        try:
            u = (fr.url or "").lower()
        except Exception:
            continue
        if "exportcenter" in u or "/export" in u or "export" in u:
            return fr

    # 仅当 DOM 里确实存在该 iframe 时才 attach，且用短超时（Australia 等页才有 #pageTabContent）
    try:
        tab_if = page.locator(PAGE_TAB_IFRAME)
        if tab_if.count() > 0:
            tab_ms = _wait_ms_from_env("WINIT_STEP02_PAGE_TAB_IFRAME_MS", 1500)
            tab_if.first.wait_for(state="attached", timeout=tab_ms)
            cf = tab_if.first.content_frame
            if cf is not None:
                return cf
    except Exception:
        pass

    list_iframe_ms = _wait_ms_from_env("WINIT_STEP02_EXPORT_LIST_IFRAME_MS", 800)
    for idx in (0, 1, 2):
        fr = _iframe_nth_frame(page, idx, timeout_ms=list_iframe_ms)
        if fr is not None:
            return fr

    try:
        if page.get_by_role("row").count() >= 1:
            return page.main_frame
    except Exception:
        pass
    return None


def _export_list_inner_frame_wait(page: Page, timeout_ms: int) -> Frame:
    """
    短轮询定位表格所在 frame。此前 bug：内层对 iframe 使用 12s attach，单次调用可卡 30s+，
    页面虽已画出仍像「卡住」。
    """
    step = _wait_ms_from_env("WINIT_STEP02_EXPORT_CENTER_POLL_MS", 200)
    deadline = time.monotonic() + timeout_ms / 1000.0
    last_log = 0.0
    while time.monotonic() < deadline:
        inner = _export_list_inner_frame(page)
        if inner is not None:
            return inner
        now = time.monotonic()
        if now - last_log >= 4.0:
            print("仍在定位导出中心表格（iframe/主文档）…", flush=True)
            last_log = now
        page.wait_for_timeout(step)

    quick = _wait_ms_from_env("WINIT_STEP02_EXPORT_LIST_FALLBACK_IFRAME_MS", 1200)
    fr = _iframe_nth_frame(page, 0, timeout_ms=quick)
    if fr is not None:
        print("已用兜底 iframe[0] 作为列表 frame。", flush=True)
        return fr
    print("使用主文档作为列表 frame（兜底）。", flush=True)
    return page.main_frame


def _click_save_on_export_list_item(item) -> None:
    """#exportDataList 内 li：站点使用 a.down + 文案「保存本地」。"""
    try:
        item.locator("a.down").first.click(timeout=20_000)
        return
    except Exception:
        pass
    last_err: Optional[Exception] = None
    for st in _save_link_text_candidates():
        try:
            item.get_by_text(st, exact=False).first.click(timeout=20_000, force=True)
            return
        except Exception as e:
            last_err = e
    try:
        item.locator("a").filter(has_text=re.compile(r"保存.*本地")).first.click(timeout=20_000)
        return
    except Exception as e:
        last_err = e
    raise RuntimeError(f"无法点击保存链接：{last_err!r}")


def _pick_export_data_list_root(page: Page):
    """
    页面上常有多个 #exportDataList（顶栏下拉 vs 导出中心主内容）。优先主内容区，避免指到隐藏容器后一直 wait visible。
    可用 WINIT_EXPORT_DATA_LIST_CSS 完全指定，例如：#root .winitd-card-body #exportDataList
    """
    custom = os.environ.get("WINIT_EXPORT_DATA_LIST_CSS", "").strip()
    if custom:
        loc = page.locator(custom).first
        if loc.count() > 0:
            return loc
    for sel in (
        "#root .winitd-card-body #exportDataList",
        "#root .winitd-card #exportDataList",
        "#root #exportDataList",
        ".winitd-layout-content #exportDataList",
        "micro-app #exportDataList",
        "#exportDataList",
    ):
        loc = page.locator(sel).first
        try:
            if loc.count() > 0:
                return loc
        except Exception:
            continue
    return page.locator("#exportDataList").first


def _try_download_via_export_data_list(
    page: Page,
    row_pat: re.Pattern,
    save_dir: Path,
    dl_timeout: int,
    pop_timeout: int,
    li_attach_timeout_ms: Optional[int] = None,
) -> Optional[Path]:
    """
    #exportDataList > ul > li，a.down「保存本地」；就绪以 a.down 可见为主，JSON status=DONE 为辅。
    li 在 overflow 区域时 wait(state=visible) 会卡死，故用 attached + 轮询 a.down。
    li 的等待单独用较短超时（默认 12s），避免主表格可走通时仍空等 120s。
    """
    li_cap = (
        li_attach_timeout_ms
        if li_attach_timeout_ms is not None
        else _wait_ms_from_env("WINIT_STEP02_EXPORT_LIST_LI_WAIT_MS", 12_000)
    )
    ready_wait = _wait_ms_from_env("WINIT_STEP02_EXPORT_READY_WAIT_MS", 180_000)

    use_first_li = os.environ.get("WINIT_EXPORT_USE_FIRST_LI", "").lower() in ("1", "true", "yes")
    per_try = max(2000, min(li_cap, 8000))

    lists = page.locator("#exportDataList")
    n = lists.count()
    root = None
    item = None
    if n > 0:
        _prof_mark(f"下载：扫描页面上 {n} 个 #exportDataList 找匹配 li…")
        for i in range(n):
            cand_root = lists.nth(i)
            if use_first_li:
                cand_item = cand_root.locator("li").first
            else:
                cand_item = cand_root.locator("li").filter(has_text=row_pat).first
            try:
                cand_item.wait_for(state="attached", timeout=per_try)
                root, item = cand_root, cand_item
                _prof_mark(f"下载：在第 {i} 个 #exportDataList 找到 li")
                break
            except PlaywrightTimeoutError:
                continue

    if item is None:
        root = _pick_export_data_list_root(page)
        if root.count() == 0:
            _prof_mark("下载：无 #exportDataList")
            return None
        if use_first_li:
            item = root.locator("li").first
        else:
            item = root.locator("li").filter(has_text=row_pat).first
        print("检测到 #exportDataList，等待匹配 li（短超时）…", flush=True)
        try:
            item.wait_for(state="attached", timeout=li_cap)
        except PlaywrightTimeoutError:
            _prof_mark(f"下载：#exportDataList li attached 超时（{li_cap}ms）")
            return None

    _prof_mark("下载：使用 #exportDataList 流程")
    print("等待该条出现可点「保存」或 JSON 就绪…", flush=True)

    _prof_mark("下载：匹配 li 已 attached，轮询 a.down / JSON")
    step = _wait_ms_from_env("WINIT_STEP02_EXPORT_STATUS_POLL_MS", 600)
    poll_cap_ms = _wait_ms_from_env("WINIT_STEP02_EXPORT_LIST_POLL_CAP_MS", 45_000)
    # 顶栏 li 无「保存」时会空转；封顶后回退 iframe/ant-table
    deadline = time.monotonic() + min(ready_wait, poll_cap_ms) / 1000.0
    last_log = 0.0
    ready_text_pat = re.compile(re.escape(EXPORT_ROW_READY_STATUS))
    iter_n = 0

    while time.monotonic() < deadline:
        iter_n += 1
        if iter_n % 15 == 0:
            _prof_mark(f"下载：仍在等待就绪（已轮询 {iter_n} 次）")

        # 主判据：保存链接已显示（生成完成后才有）
        try:
            down = item.locator("a.down").first
            if down.is_visible(timeout=400):
                print("检测到「保存」链接可点，开始下载…", flush=True)
                with page.expect_download(timeout=dl_timeout) as dl_info:
                    with page.expect_popup(timeout=pop_timeout) as pop_info:
                        down.click(timeout=20_000)
                    popup = pop_info.value
                download = dl_info.value
                try:
                    popup.close()
                except Exception:
                    pass
                _prof_mark("下载：a.down 点击后已保存文件")
                return _save_download(download, save_dir)
        except Exception:
            pass

        try:
            ta = item.locator("textarea").first
            if ta.count() > 0:
                val = ""
                try:
                    val = ta.input_value(timeout=500)
                except Exception:
                    try:
                        val = ta.evaluate("el => el.value || ''") or ""
                    except Exception:
                        val = ""
                if val:
                    if EXPORT_FAIL_STATUS and EXPORT_FAIL_STATUS in val:
                        raise RuntimeError("导出任务失败（JSON 含失败信息），请到导出中心查看。")
                    if '"status":"FAIL"' in val or '"status":"FAILED"' in val:
                        raise RuntimeError("导出任务失败（JSON status 为 FAIL），请到导出中心查看。")
                    if any(m in val for m in _EXPORT_JSON_DONE_MARKERS):
                        item.locator("a.down").first.wait_for(state="visible", timeout=20_000)
                        print("任务已完成（JSON status=DONE），正在点击保存…", flush=True)
                        with page.expect_download(timeout=dl_timeout) as dl_info:
                            with page.expect_popup(timeout=pop_timeout) as pop_info:
                                _click_save_on_export_list_item(item)
                            popup = pop_info.value
                        download = dl_info.value
                        try:
                            popup.close()
                        except Exception:
                            pass
                        _prof_mark("下载：JSON DONE 后已保存文件")
                        return _save_download(download, save_dir)
        except RuntimeError:
            raise
        except Exception:
            pass

        try:
            inner = item.inner_text(timeout=500) or ""
            if ready_text_pat.search(inner):
                item.locator("a.down").first.wait_for(state="visible", timeout=10_000)
                print(f"界面已出现「{EXPORT_ROW_READY_STATUS}」，正在点击保存…", flush=True)
                with page.expect_download(timeout=dl_timeout) as dl_info:
                    with page.expect_popup(timeout=pop_timeout) as pop_info:
                        _click_save_on_export_list_item(item)
                    popup = pop_info.value
                download = dl_info.value
                try:
                    popup.close()
                except Exception:
                    pass
                _prof_mark("下载：界面「导出成功」后已保存文件")
                return _save_download(download, save_dir)
        except Exception:
            pass

        now = time.monotonic()
        if now - last_log >= 6.0:
            print("等待导出完成（「保存」链接出现 / JSON=DONE / 导出成功）…", flush=True)
            last_log = now
        page.wait_for_timeout(step)

    _prof_mark("下载：#exportDataList 等待就绪超时，回退表格流程")
    return None


def _wait_export_row_status_ready(
    inner: Frame,
    row_content_pat: re.Pattern,
    timeout_ms: int,
) -> None:
    """
    同一行会先出现「正在生成」等，站点自动刷新后状态变为 EXPORT_ROW_READY_STATUS 才可点「保存到本地」。
    使用「内容 + 就绪状态」双 filter 的 locator，表格整表刷新后仍会重新匹配到同一逻辑行。
    """
    ready_pat = re.compile(re.escape(EXPORT_ROW_READY_STATUS))
    ready_row = inner.get_by_role("row").filter(has_text=row_content_pat).filter(has_text=ready_pat).first
    step = _wait_ms_from_env("WINIT_STEP02_EXPORT_STATUS_POLL_MS", 1500)
    deadline = time.monotonic() + timeout_ms / 1000.0
    last_log = 0.0
    fail_pat = re.compile(re.escape(EXPORT_FAIL_STATUS)) if EXPORT_FAIL_STATUS else None

    while time.monotonic() < deadline:
        if fail_pat is not None:
            bad = inner.get_by_role("row").filter(has_text=row_content_pat).filter(has_text=fail_pat).first
            try:
                if bad.is_visible(timeout=400):
                    raise RuntimeError(
                        f"导出任务失败：行内出现「{EXPORT_FAIL_STATUS}」，请登录导出中心查看原因。"
                    )
            except RuntimeError:
                raise
            except Exception:
                pass
        remain_s = deadline - time.monotonic()
        if remain_s <= 0:
            break
        chunk_ms = min(step, max(200, int(remain_s * 1000)))
        try:
            ready_row.wait_for(state="visible", timeout=chunk_ms)
            return
        except PlaywrightTimeoutError:
            pass
        now = time.monotonic()
        if now - last_log >= 6.0:
            print(
                f"等待该行变为「{EXPORT_ROW_READY_STATUS}」（此前可能为「正在生成」等，页面会自动刷新）…",
                flush=True,
            )
            last_log = now

    raise RuntimeError(
        f"{timeout_ms}ms 内该行未变为「{EXPORT_ROW_READY_STATUS}」，请检查导出中心是否仍显示正在生成。"
    )


def _click_save_to_local_row(row) -> None:
    """表格行：优先 a.down，再按候选文案点链接/按钮。"""
    last_err: Optional[Exception] = None
    try:
        row.locator("a.down").first.click(timeout=20_000)
        return
    except Exception as e:
        last_err = e
    for st in _save_link_text_candidates():
        try:
            row.get_by_role("link", name=re.compile(re.escape(st))).first.click(timeout=20_000)
            return
        except Exception as e:
            last_err = e
        try:
            row.get_by_text(st, exact=False).first.click(timeout=20_000, force=True)
            return
        except Exception as e:
            last_err = e
        try:
            row.locator("a").filter(has_text=re.compile(re.escape(st))).first.click(timeout=20_000)
            return
        except Exception as e:
            last_err = e
    try:
        row.get_by_role("button").first.click(timeout=15_000)
        return
    except Exception as e:
        last_err = e
    raise RuntimeError(f"无法点击保存/下载：{last_err!r}")


def _save_download(download, save_dir: Path) -> Path:
    save_dir.mkdir(parents=True, exist_ok=True)
    suggested = download.suggested_filename or "export.bin"
    stem = Path(suggested).stem
    suf = Path(suggested).suffix or ".bin"
    name = f"{stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{suf}"
    target = save_dir / name
    download.save_as(str(target))
    return target


def _frame_for_export_table(page: Page, row_pat: re.Pattern) -> Frame:
    """
    导出中心表格常在 main_frame，也可能在微前端子 frame（main_frame 上 get_by_role(row) 为 0，
    会误判失败并误入顶栏 #exportDataList 的「假 li」）。
    """
    cands: List[Frame] = [page.main_frame]
    for fr in page.frames:
        if fr is page.main_frame:
            continue
        u = (fr.url or "").lower()
        if "sharedworker" in u or u.startswith("about:"):
            continue
        if any(x in u for x in ("exportcenter", "sellerstatic", "seller.winit")):
            cands.append(fr)

    for fr in cands:
        try:
            loc = fr.get_by_role("row").filter(has_text=row_pat)
            if loc.count() > 0:
                _prof_mark(f"ant-table：选用含匹配行的 frame url={(fr.url or '')[:85]!r}")
                return fr
        except Exception:
            continue

    for fr in cands:
        try:
            loc = fr.locator("tbody tr, tr.ant-table-row").filter(has_text=row_pat)
            if loc.count() > 0:
                _prof_mark("ant-table：用 tbody tr 匹配到行，选用该 frame")
                return fr
        except Exception:
            continue

    _prof_mark("ant-table：未找到含匹配行的 frame，退回 main_frame（可能需等表格渲染）")
    return page.main_frame


def _try_ant_table_export_download(
    inner: Frame,
    page: Page,
    row_pat: re.Pattern,
    save_dir: Path,
    dl_timeout: int,
    pop_timeout: int,
    row_wait_ms: int,
    ready_wait_ms: int,
) -> Optional[Path]:
    """
    导出中心主界面多为 ant-table（role=row + 导出成功 + 保存链接）。
    返回 None 表示本路径未成功（不抛错，便于再试 #exportDataList）。
    """
    try:
        _prof_mark("ant-table：查找含匹配文案的行")
        row = inner.get_by_role("row").filter(has_text=row_pat).first
        try:
            row.wait_for(state="visible", timeout=row_wait_ms)
        except PlaywrightTimeoutError:
            try:
                row = inner.get_by_role("row").nth(1)
                row.wait_for(state="visible", timeout=min(30_000, row_wait_ms))
            except PlaywrightTimeoutError:
                _prof_mark("ant-table：未找到可见匹配行")
                return None

        _prof_mark("ant-table：等待状态「导出成功」")
        try:
            _wait_export_row_status_ready(inner, row_pat, timeout_ms=ready_wait_ms)
        except RuntimeError:
            _prof_mark("ant-table：等待「导出成功」超时")
            return None

        ready_row = (
            inner.get_by_role("row")
            .filter(has_text=row_pat)
            .filter(has_text=re.compile(re.escape(EXPORT_ROW_READY_STATUS)))
            .first
        )
        try:
            ready_row.wait_for(state="visible", timeout=15_000)
        except PlaywrightTimeoutError:
            _prof_mark("ant-table：就绪行不可见")
            return None

        print("ant-table：状态已就绪，点击保存…", flush=True)
        _prof_mark("ant-table：点击保存并等待下载")
        with page.expect_download(timeout=dl_timeout) as dl_info:
            with page.expect_popup(timeout=pop_timeout) as pop_info:
                _click_save_to_local_row(ready_row)
            popup = pop_info.value
        download = dl_info.value
        try:
            popup.close()
        except Exception:
            pass
        return _save_download(download, save_dir)
    except PlaywrightTimeoutError:
        return None
    except PlaywrightError as e:
        if "Target closed" in str(e) or type(e).__name__ == "TargetClosedError":
            raise
        _prof_mark(f"ant-table：失败 {type(e).__name__}")
        return None
    except Exception as e:
        _prof_mark(f"ant-table：失败 {type(e).__name__}")
        return None


def run_step02_export_for_account(account: WinitAccount) -> int:
    """供定时任务等调用：单账号完整「登录 → 导出 → 下载 zip」流程。"""
    return _run_step02_for_account(account)


def _run_step02_for_account(account: WinitAccount) -> int:
    headless = os.environ.get("WINIT_HEADLESS", "false").lower() in ("1", "true", "yes")
    try:
        form_wait_ms = int(os.environ.get("WINIT_FORM_WAIT_MS", "45000"))
    except ValueError:
        form_wait_ms = 45_000

    save_dir = resolve_download_dir_for_account(account)
    dl_timeout = int(os.environ.get("WINIT_STEP02_DOWNLOAD_TIMEOUT_MS", "120000"))

    global _profiler
    profile_on = os.environ.get("WINIT_STEP02_PROFILE", "").lower() in ("1", "true", "yes")
    trace_on = os.environ.get("WINIT_STEP02_TRACE", "").lower() in ("1", "true", "yes")
    trace_zip: Optional[Path] = None

    print(
        f"当前账号：{account.display_name()}  {account.username}  |  下载目录：{save_dir}",
        flush=True,
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        vw = int(os.environ.get("WINIT_VIEWPORT_WIDTH", "1920"))
        vh = int(os.environ.get("WINIT_VIEWPORT_HEIGHT", "1080"))
        context = browser.new_context(
            locale="zh-CN",
            viewport={"width": vw, "height": vh},
            ignore_https_errors=True,
            accept_downloads=True,
        )
        if trace_on:
            trace_zip = (
                ROOT
                / "artifacts"
                / f"step02_trace_a{account.id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
            )
            trace_zip.parent.mkdir(parents=True, exist_ok=True)
            context.tracing.start(screenshots=True, snapshots=True, sources=True)
            print("WINIT_STEP02_TRACE=1：结束时会写入", trace_zip, flush=True)

        page: Page = context.new_page()
        try:
            _profiler = Step02Profiler() if profile_on else None
            if profile_on:
                print(
                    "WINIT_STEP02_PROFILE=1：以下为分阶段耗时（+累计秒  Δ距上一标记秒）。",
                    flush=True,
                )
                _prof_mark("浏览器已启动")

            code = login_on_page(
                page,
                user=account.username,
                password=account.password,
                form_wait_ms=form_wait_ms,
            )
            if code != 0:
                return code
            _prof_mark("登录完成")

            _dismiss_modal_if_any(page)
            _prof_mark("首屏弹窗处理结束")

            entry_url = _entry_url()
            print(f"正在打开入口页面… {entry_url}", flush=True)
            page.goto(entry_url, wait_until="domcontentloaded", timeout=90_000)
            try:
                page.wait_for_load_state("load", timeout=45_000)
            except PlaywrightTimeoutError:
                pass
            print("当前 URL（进入 Australia 后）：", page.url, flush=True)
            _prof_mark("Australia 页 domcontentloaded/load 结束")

            _click_export_by_scanning_all_frames(page)
            _prof_mark("已点击「导出」并完成扫描阶段")
            page.wait_for_timeout(_wait_ms_from_env("WINIT_STEP02_AFTER_EXPORT_CLICK_MS", 350))
            tab_frame = page.frame_locator(PAGE_TAB_IFRAME)
            _export_dialog_act(page, tab_frame)
            _prof_mark("导出弹窗：已选类型并点确定")
            _wait_export_task_submitted(page, tab_frame)
            _prof_mark("导出弹窗已关闭（视为已提交）")
            _goto_export_center(page)
            _prof_mark("导出中心 URL 已打开")
            page.wait_for_timeout(_wait_ms_from_env("WINIT_STEP02_EXPORT_CENTER_SETTLE_MS", 400))

            row_wait = _wait_ms_from_env("WINIT_STEP02_EXPORT_ROW_WAIT_MS", 120_000)
            ready_wait = _wait_ms_from_env("WINIT_STEP02_EXPORT_READY_WAIT_MS", 180_000)
            row_match_text = _row_match_text()
            row_pat = re.compile(re.escape(row_match_text))

            shell_ms = _wait_ms_from_env("WINIT_STEP02_EXPORT_CENTER_SHELL_MS", 20_000)
            print(
                f"等待导出中心 #exportDataList 容器（最多 {shell_ms // 1000}s，可选）…",
                flush=True,
            )
            try:
                page.locator("#exportDataList").first.wait_for(state="attached", timeout=shell_ms)
            except PlaywrightTimeoutError:
                pass
            _prof_mark("导出中心：壳层已就绪（#exportDataList 可选）")

            # 主表格常在子 frame / 微前端内，勿只用 main_frame
            print(
                "导出中心：步骤 1/3 主表格 ant-table（自动选含「"
                + row_match_text
                + "」的 frame）…",
                flush=True,
            )
            tbl_fr = _frame_for_export_table(page, row_pat)
            path = _try_ant_table_export_download(
                tbl_fr,
                page,
                row_pat,
                save_dir,
                dl_timeout,
                60_000,
                row_wait,
                ready_wait,
            )
            if path is not None:
                _prof_mark("全流程结束（经 ant-table）")
                print("已下载：", path)
                return 0

            li_ms = _wait_ms_from_env("WINIT_STEP02_EXPORT_LIST_LI_WAIT_MS", 12_000)
            print(
                f"步骤 2/3：主表格未成功，尝试 #exportDataList（li 最多 {li_ms // 1000}s）…",
                flush=True,
            )
            via_list = _try_download_via_export_data_list(
                page,
                row_pat,
                save_dir,
                dl_timeout,
                pop_timeout=60_000,
                li_attach_timeout_ms=li_ms,
            )
            if via_list is not None:
                _prof_mark("全流程结束（经 #exportDataList）")
                print("已下载：", via_list)
                return 0

            ready_ms = _wait_ms_from_env("WINIT_STEP02_EXPORT_CENTER_READY_MS", 15_000)
            print(f"步骤 3/3：仍失败，尝试 iframe 内表格（最多 {ready_ms // 1000}s）…", flush=True)
            inner = _export_list_inner_frame_wait(page, timeout_ms=ready_ms)
            _prof_mark("iframe/主文档 inner 已解析")

            path = _try_ant_table_export_download(
                inner, page, row_pat, save_dir, dl_timeout, 60_000, row_wait, ready_wait
            )
            if path is not None:
                _prof_mark("全流程结束（经 iframe 内 ant-table）")
                print("已下载：", path)
                return 0

            raise RuntimeError(
                "导出中心：主表格、#exportDataList、iframe 表格均未完成下载，请开 WINIT_STEP02_PROFILE=1 查看阶段耗时。"
            )
        except RuntimeError as e:
            print("步骤失败：", e, file=sys.stderr)
            shot = ROOT / "screenshots" / "step02_runtime_error.png"
            shot.parent.mkdir(parents=True, exist_ok=True)
            try:
                page.screenshot(path=str(shot), full_page=True)
            except Exception:
                pass
            print("已截图：", shot, file=sys.stderr)
            return 13
        except PlaywrightTimeoutError as e:
            print("超时：", e, file=sys.stderr)
            shot = ROOT / "screenshots" / "step02_timeout.png"
            shot.parent.mkdir(parents=True, exist_ok=True)
            try:
                page.screenshot(path=str(shot), full_page=True)
            except Exception:
                pass
            print("已截图：", shot, file=sys.stderr)
            return 11
        except PlaywrightError as e:
            if type(e).__name__ == "TargetClosedError" or "has been closed" in str(e):
                print("页面或浏览器已关闭（TargetClosedError）。", file=sys.stderr)
                print(
                    "运行期间请勿手动关闭 Chromium 窗口；若未关窗仍出现，把本段前后 PROFILE 日志发来。",
                    file=sys.stderr,
                )
                print(
                    "若终端出现 “Future exception was never retrieved”，多为关窗后仍有后台等待未结束。",
                    file=sys.stderr,
                )
                return 14
            print("Playwright 错误：", e, file=sys.stderr)
            shot = ROOT / "screenshots" / "step02_playwright_error.png"
            shot.parent.mkdir(parents=True, exist_ok=True)
            try:
                page.screenshot(path=str(shot), full_page=True)
            except Exception:
                pass
            print("已截图：", shot, file=sys.stderr)
            return 12
        finally:
            if trace_on and trace_zip is not None:
                try:
                    context.tracing.stop(path=str(trace_zip))
                    print("Trace 已保存，用以下命令查看：", flush=True)
                    print(f"  playwright show-trace {trace_zip}", flush=True)
                except Exception as e:
                    print("保存 Trace 失败：", e, file=sys.stderr)
            _profiler = None
            _shutdown(page, context, browser)


def run() -> int:
    accs = list_winit_accounts()
    if not accs:
        print(
            "未配置账号：请在 .env 设置 WINIT_USERNAME/WINIT_PASSWORD，"
            "或 WINIT_ACCOUNT_1_* / WINIT_ACCOUNT_2_*（见 .env.example 与 winit_accounts.py）",
            file=sys.stderr,
        )
        return 1
    if run_all_winit_accounts_requested():
        exit_code = 0
        for account in accs:
            print(
                f"\n{'=' * 60}\n"
                f"多账号顺序执行：账号 {account.display_name()}  {account.username}\n"
                f"{'=' * 60}\n",
                flush=True,
            )
            code = _run_step02_for_account(account)
            if code != 0:
                exit_code = code
        return exit_code
    account = pick_active_account(accs)
    if account is None:
        return 1
    return _run_step02_for_account(account)


if __name__ == "__main__":
    raise SystemExit(run())
