"""
根据 download_flow.json 在已登录的 Page 上执行步骤，最后一步可触发浏览器下载。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import Error as PlaywrightError


def load_flow(path: Path) -> List[Dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "steps" in data:
        steps = data["steps"]
    elif isinstance(data, list):
        steps = data
    else:
        raise ValueError("流程文件应为 { \"steps\": [...] } 或 JSON 数组")
    if not isinstance(steps, list):
        raise ValueError("steps 必须是数组")
    return steps


def run_flow_step(
    page: Page,
    step: Dict[str, Any],
    *,
    save_dir: Path,
    default_download_timeout_ms: int = 120_000,
) -> Optional[Path]:
    """
    执行单步。若 action 为 download，返回保存后的本地路径；否则返回 None。
    """
    action = (step.get("action") or "").lower()
    if action == "goto":
        url = step["url"]
        page.goto(url, wait_until="domcontentloaded", timeout=int(step.get("timeout_ms", 90_000)))
        return None
    if action == "wait_ms":
        page.wait_for_timeout(int(step["ms"]))
        return None
    if action == "click_text":
        loc = page.get_by_text(step["text"], exact=bool(step.get("exact", False))).first
        loc.click(timeout=int(step.get("timeout_ms", 30_000)))
        return None
    if action == "click_role":
        role = step.get("role", "button")
        name_pat = step["name_pattern"]
        page.get_by_role(role, name=re.compile(name_pat, re.I)).first.click(
            timeout=int(step.get("timeout_ms", 30_000))
        )
        return None
    if action == "click_selector":
        page.locator(step["selector"]).first.click(timeout=int(step.get("timeout_ms", 30_000)))
        return None
    if action == "download":
        timeout_ms = int(step.get("timeout_ms", default_download_timeout_ms))
        save_dir.mkdir(parents=True, exist_ok=True)

        def _trigger_click() -> None:
            if "click_text" in step:
                page.get_by_text(step["click_text"], exact=bool(step.get("exact", False))).first.click(
                    timeout=timeout_ms
                )
            elif "click_selector" in step:
                page.locator(step["click_selector"]).first.click(timeout=timeout_ms)
            elif "click_role" in step:
                cr = step["click_role"]
                role = cr.get("role", "button")
                pat = cr["name_pattern"]
                page.get_by_role(role, name=re.compile(pat, re.I)).first.click(timeout=timeout_ms)
            else:
                role = step.get("role", "button")
                pat = step.get("name_pattern", r"导出|下载|Export|Download")
                page.get_by_role(role, name=re.compile(pat, re.I)).first.click(timeout=timeout_ms)

        with page.expect_download(timeout=timeout_ms) as dl_info:
            _trigger_click()
        received = dl_info.value
        fname = step.get("save_as") or received.suggested_filename or "download.bin"
        target = save_dir / fname
        received.save_as(str(target))
        return target

    raise ValueError(f"未知 action: {action}")


def run_all_steps(
    page: Page,
    steps: List[Dict[str, Any]],
    *,
    save_dir: Path,
) -> Optional[Path]:
    last_saved: Optional[Path] = None
    for i, step in enumerate(steps):
        try:
            saved = run_flow_step(page, step, save_dir=save_dir)
        except (PlaywrightTimeoutError, PlaywrightError) as e:
            raise RuntimeError(f"步骤 {i + 1} 失败: {step!r}") from e
        if saved is not None:
            last_saved = saved
    return last_saved
