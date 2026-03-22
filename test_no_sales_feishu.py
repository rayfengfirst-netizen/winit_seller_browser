"""
预览无动销飞书文案；若 .env 已配置 WINIT_FEISHU_WEBHOOK_NO_SALES 则实际发送一条测试。

  cd winit_seller_browser && source .venv/bin/activate
  python test_no_sales_feishu.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

from winit_feishu_webhook import (  # noqa: E402
    feishu_channel_configured,
    feishu_send_text,
)
from winit_inventory_db import connect, init_schema  # noqa: E402
from winit_no_sales_report import (  # noqa: E402
    build_no_sales_detail_url,
    collect_no_sales_rows,
    format_no_sales_feishu_text,
)


def main() -> int:
    conn = connect()
    init_schema(conn)
    try:
        rows, th_meta = collect_no_sales_rows(conn)
    finally:
        conn.close()

    url = build_no_sales_detail_url()
    text = format_no_sales_feishu_text(th_meta, url)

    print("========== 当前飞书模板（实际发送内容）==========")
    print(text)
    print("================================================")

    if not feishu_channel_configured("no_sales"):
        print(
            "\n未配置 WINIT_FEISHU_WEBHOOK_NO_SALES，未发送。"
            "\n在 .env 写入无动销专用 Webhook 后重跑本脚本即可测真发。",
            file=sys.stderr,
        )
        return 0

    ok, detail = feishu_send_text(text, channel="no_sales")
    print(f"\n发送结果：{'成功' if ok else '失败'} — {detail}")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
