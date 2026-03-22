"""
每天早上触发（配合 systemd timer）：统计无动销 SKU，飞书 Webhook 推送摘要 + 详情链接。

环境变量：
  WINIT_FEISHU_WEBHOOK_URL   必填（与 run_daily 相同机器人即可）
  WINIT_PUBLIC_BASE_URL      详情页基址，如 http://8.218.58.28:8765（无尾斜杠）
  WINIT_SQLITE_PATH          可选
  WINIT_NO_SALES_*           见 winit_no_sales_report.py

手动：
  cd winit_seller_browser && source .venv/bin/activate
  python run_no_sales_morning_job.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

from winit_feishu_webhook import feishu_send_text  # noqa: E402
from winit_inventory_db import connect, init_schema, sqlite_path  # noqa: E402
from winit_no_sales_report import (  # noqa: E402
    build_no_sales_detail_url,
    collect_no_sales_rows,
    get_latest_snapshot_dates_by_account,
)


def main() -> int:
    if not os.environ.get("WINIT_FEISHU_WEBHOOK_URL", "").strip():
        print("未设置 WINIT_FEISHU_WEBHOOK_URL", file=sys.stderr)
        return 1

    conn = connect()
    init_schema(conn)
    try:
        rows, th_meta = collect_no_sales_rows(conn)
        dates = get_latest_snapshot_dates_by_account(conn)
    finally:
        conn.close()

    url = build_no_sales_detail_url()
    n = len(rows)
    by_acct: dict[int, int] = {}
    for m in rows:
        aid = int(m["account_id"])
        by_acct[aid] = by_acct.get(aid, 0) + 1

    snap_line = ", ".join(f"账号{k}:{v}" for k, v in sorted(dates.items())) or "无快照"
    acct_line = ", ".join(f"账号{k}共{v}条" for k, v in sorted(by_acct.items())) if by_acct else ""

    lines = [
        "📌 无动销预警（库存快照）",
        f"符合条件的 SKU：共 {n} 条",
        f"快照日期：{snap_line}",
        f"规则：可用≥{th_meta['min_available']} 且 30天均销≤{th_meta['max_avg30']}"
        + (" 且历史销量=0" if th_meta["require_zero_hist"] else ""),
    ]
    if acct_line:
        lines.append(acct_line)
    lines.append(f"详情（浏览器打开）：{url}")
    lines.append(f"库文件：{sqlite_path()}")

    ok, detail = feishu_send_text("\n".join(lines))
    print(f"[no_sales] 飞书：{'ok' if ok else 'fail'} ({detail})  rows={n}", flush=True)
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
