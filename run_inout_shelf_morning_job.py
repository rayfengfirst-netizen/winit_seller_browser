"""
入库上架类流水飞书摘要（建议北京时间每天 10:10）。

从独立库 inventory_inout_current 统计备注为「标准入库-上架」「国内直发入库-上架」的行，
按业务日期分块汇总（与网页 /report/inout-shelf 一致）。不重新下载文件；依赖当日
run_inventory_inout_job（如 05:00）已入库的数据。

环境变量：
  WINIT_FEISHU_WEBHOOK_INOUT_SHELF  未配置则跳过发送（退出码 0，便于先部署 timer）
  WINIT_PUBLIC_BASE_URL             文末明细页链接
  WINIT_INOUT_SQLITE_PATH           可选，与入库任务一致
  WINIT_INOUT_SHELF_*_KEYS          可选，见 winit_inout_shelf_report.py

部署：deploy/winit-inout-shelf-alert.service.example 与 .timer.example（OnCalendar 10:10）

手动：
  cd winit_seller_browser && source .venv/bin/activate
  python run_inout_shelf_morning_job.py
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
from winit_inout_shelf_report import (  # noqa: E402
    build_inout_shelf_detail_url,
    collect_inout_shelf_rows,
    format_inout_shelf_feishu_text,
)
from winit_inventory_inout_db import connect_inout, init_inout_schema  # noqa: E402


def main() -> int:
    if not feishu_channel_configured("inout_shelf"):
        print(
            "[inout_shelf] 跳过飞书：未配置 WINIT_FEISHU_WEBHOOK_INOUT_SHELF",
            flush=True,
        )
        return 0

    conn = connect_inout()
    init_inout_schema(conn)
    try:
        blocks, meta = collect_inout_shelf_rows(conn)
    finally:
        conn.close()

    url = build_inout_shelf_detail_url()
    text = format_inout_shelf_feishu_text(blocks, url, meta=meta)
    ok, detail = feishu_send_text(text, channel="inout_shelf")
    print(
        f"[inout_shelf] 飞书：{'ok' if ok else 'fail'} ({detail})  rows={meta.get('total', 0)}",
        flush=True,
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
