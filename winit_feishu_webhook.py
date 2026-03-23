"""
飞书群机器人 Webhook：仅发送文本（msg_type=text）。

按「场景」配置不同 URL，便于入库通知、无动销、后续告警等发到不同群。

环境变量（整段 URL，勿提交到 Git）：
  WINIT_FEISHU_WEBHOOK_<场景大写>   例：WINIT_FEISHU_WEBHOOK_SYNC、WINIT_FEISHU_WEBHOOK_NO_SALES、WINIT_FEISHU_WEBHOOK_INOUT_SHELF
  channel 名使用小写+下划线，对应环境变量后缀为全大写下划线。

兼容旧配置：
  WINIT_FEISHU_WEBHOOK_URL         仅当场景为 sync 且未设置 WINIT_FEISHU_WEBHOOK_SYNC 时作为入库通知地址。

扩展示例：若代码里 feishu_send_text(..., channel="price_alert")，
则配置 WINIT_FEISHU_WEBHOOK_PRICE_ALERT=...

文档：https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request


def _feishu_rate_limit_env() -> tuple[int, int]:
    """频率限制时的额外重试次数与间隔（秒）。可通过环境变量覆盖。"""
    try:
        retries = int(os.environ.get("WINIT_FEISHU_RATE_LIMIT_RETRIES", "3"))
    except ValueError:
        retries = 3
    try:
        delay = int(os.environ.get("WINIT_FEISHU_RATE_LIMIT_DELAY_SEC", "45"))
    except ValueError:
        delay = 45
    retries = max(0, min(retries, 10))
    delay = max(5, min(delay, 600))
    return retries, delay


def _body_is_feishu_rate_limit(body: str) -> bool:
    """飞书自定义机器人返回频率限制（如 code 11232）。"""
    if not body or not body.strip():
        return False
    low = body.lower()
    if "11232" in body and ("frequency" in low or "limit" in low):
        return True
    try:
        j = json.loads(body)
    except json.JSONDecodeError:
        return False
    code = j.get("code")
    if code is not None and int(code) == 11232:
        return True
    for key in ("msg", "message", "error"):
        v = j.get(key)
        if isinstance(v, str) and "frequency" in v.lower():
            return True
    return False


def _post_once(
    url: str, raw: bytes
) -> tuple[bool, str, bool]:
    """
    单次 POST。返回 (成功, 说明或错误摘要, 是否为频率限制可重试)。
    """
    req = urllib.request.Request(
        url,
        data=raw,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace") if e.fp else str(e)
        combined = f"HTTP {e.code}: {err_body}"
        if e.code == 429 or _body_is_feishu_rate_limit(err_body):
            return False, combined[:500], True
        return False, combined, False
    except Exception as e:
        return False, str(e), False

    try:
        j = json.loads(body) if body.strip() else {}
    except json.JSONDecodeError:
        if _body_is_feishu_rate_limit(body):
            return False, body[:200], True
        return True, body[:200], False

    code = j.get("code")
    if code is not None and int(code) != 0:
        lim = _body_is_feishu_rate_limit(body)
        return False, body[:500], lim
    if j.get("StatusCode") not in (None, 0):
        lim = _body_is_feishu_rate_limit(body)
        return False, body[:500], lim
    return True, "ok", False


def feishu_webhook_url(channel: str) -> str:
    """
    解析某场景的 Webhook URL；未配置则返回空串。
    sync：未配 WINIT_FEISHU_WEBHOOK_SYNC 时回退 WINIT_FEISHU_WEBHOOK_URL。
    其它场景不设回退，避免误发到错误群。
    """
    c = channel.strip().lower().replace("-", "_")
    if not c:
        return ""
    suffix = c.upper()
    key = f"WINIT_FEISHU_WEBHOOK_{suffix}"
    url = os.environ.get(key, "").strip()
    if url:
        return url
    if c == "sync":
        return os.environ.get("WINIT_FEISHU_WEBHOOK_URL", "").strip()
    return ""


def feishu_channel_configured(channel: str) -> bool:
    return bool(feishu_webhook_url(channel))


def feishu_send_text(text: str, *, channel: str = "sync") -> tuple[bool, str]:
    """
    向指定场景的 Webhook 发送文本。
    成功 (True, 摘要)；失败 (False, 错误说明)。
    未配置该场景 URL 时返回 (True, skipped_…)，不视为错误（便于定时任务静默跳过）。

    若飞书返回频率限制（如 code 11232）或 HTTP 429，会按环境变量自动等待并重试：
      WINIT_FEISHU_RATE_LIMIT_RETRIES（默认 3，额外重试次数）
      WINIT_FEISHU_RATE_LIMIT_DELAY_SEC（默认 45，每次重试前等待秒数）
    无动销等 oneshot 服务建议 TimeoutStartSec≥300，避免重试中被 systemd 杀掉。
    """
    url = feishu_webhook_url(channel)
    if not url:
        return True, f"skipped_no_webhook:{channel}"

    safe = text if len(text) <= 15000 else text[:14900] + "\n…(截断)"

    payload = {"msg_type": "text", "content": {"text": safe}}
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    extra_retries, delay_sec = _feishu_rate_limit_env()
    attempts = 1 + extra_retries
    last_err = "ok"
    for attempt in range(attempts):
        ok, detail, rate_limited = _post_once(url, raw)
        if ok:
            if attempt > 0:
                return True, f"ok_after_retry:{attempt + 1}"
            return True, "ok"
        last_err = detail
        if not rate_limited or attempt >= attempts - 1:
            break
        time.sleep(float(delay_sec))
    return False, last_err
