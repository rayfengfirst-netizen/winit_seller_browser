"""
飞书群机器人 Webhook：仅发送文本（msg_type=text）。

在 .env 设置 WINIT_FEISHU_WEBHOOK_URL（勿提交到 Git）。
文档：https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


def feishu_send_text(text: str) -> tuple[bool, str]:
    """
    发送成功返回 (True, 响应体摘要)；失败 (False, 错误说明)。
    未配置 URL 时返回 (True, "skipped")，不视为错误。
    """
    url = os.environ.get("WINIT_FEISHU_WEBHOOK_URL", "").strip()
    if not url:
        return True, "skipped_no_WINIT_FEISHU_WEBHOOK_URL"

    # 飞书单条 text 不宜过长
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

    # 飞书自定义机器人常见：{"code":0,"msg":"success"} 或带 Extra
    code = j.get("code")
    if code is not None and int(code) != 0:
        return False, body[:500]
    if j.get("StatusCode") not in (None, 0):
        return False, body[:500]
    return True, "ok"
