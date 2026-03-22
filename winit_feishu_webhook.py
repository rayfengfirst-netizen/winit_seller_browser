"""
飞书群机器人 Webhook：仅发送文本（msg_type=text）。

按「场景」配置不同 URL，便于入库通知、无动销、后续告警等发到不同群。

环境变量（整段 URL，勿提交到 Git）：
  WINIT_FEISHU_WEBHOOK_<场景大写>   例：WINIT_FEISHU_WEBHOOK_SYNC、WINIT_FEISHU_WEBHOOK_NO_SALES
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
import urllib.error
import urllib.request


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
    """
    url = feishu_webhook_url(channel)
    if not url:
        return True, f"skipped_no_webhook:{channel}"

    safe = text if len(text) <= 15000 else text[:14900] + "\n…(截断)"

    payload = {"msg_type": "text", "content": {"text": safe}}
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
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
        return False, f"HTTP {e.code}: {err_body}"
    except Exception as e:
        return False, str(e)

    try:
        j = json.loads(body) if body.strip() else {}
    except json.JSONDecodeError:
        return True, body[:200]

    code = j.get("code")
    if code is not None and int(code) != 0:
        return False, body[:500]
    if j.get("StatusCode") not in (None, 0):
        return False, body[:500]
    return True, "ok"
