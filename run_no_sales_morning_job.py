"""
无动销预警定时任务：基于 inventory_daily 各账号「最新快照日」统计，并发飞书（no_sales Webhook）。

【业务规则摘要】（与飞书文末「统计口径」一致）
  1）基础条件（同时满足才参与「均销为 0」的计数）：
     - 可用库存 ≠ 0
     - 7 天平均库存 > 0（表头一般为「7天平均库存」，兼容「7日平均库存」）
  2）飞书正文：按账号分别展示
     - 在 (1) 的前提下，分别统计 7 / 15 / 30 天「平均日销量为 0」的 SKU 条数；
     - 另报「五项全满足」SKU 条数（基础两条 + 三种均销均为 0）。
  3）详情链接：inventory_viewer 的 /report/no-sales，列出「五项全满足」明细（页面内按账号分块、数量整数展示）。

【定时建议】北京时间每天早上 10:00，且在当日 **北京时间 06:00** 的 daily sync 入库完成之后执行。
  部署见 deploy 下 timer 示例：OnCalendar 用本地时区，服务器执行 timedatectl set-timezone Asia/Shanghai。

环境变量：
  WINIT_FEISHU_WEBHOOK_NO_SALES  必填
  WINIT_PUBLIC_BASE_URL          详情页基址（无尾斜杠）
  WINIT_SQLITE_PATH              可选

飞书多场景见 winit_feishu_webhook.py。

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
    if not feishu_channel_configured("no_sales"):
        print(
            "未配置无动销飞书：请在 .env 设置 WINIT_FEISHU_WEBHOOK_NO_SALES",
            file=sys.stderr,
        )
        return 1

    conn = connect()
    init_schema(conn)
    try:
        rows, th_meta = collect_no_sales_rows(conn)
    finally:
        conn.close()

    url = build_no_sales_detail_url()
    n = len(rows)
    text = format_no_sales_feishu_text(th_meta, url)

    ok, detail = feishu_send_text(text, channel="no_sales")
    acct_bits = [
        f"{b.get('account_display', b['account_id'])}:7/15/30="
        f"{b['count_7d_zero']}/{b['count_15d_zero']}/{b['count_30d_zero']} "
        f"strict={b['strict_count']}"
        for b in (th_meta.get("by_account") or [])
    ]
    acct_s = " | ".join(acct_bits) if acct_bits else "-"
    print(
        f"[no_sales] 飞书：{'ok' if ok else 'fail'} ({detail})  "
        f"strict_total={n} 合计7/15/30={th_meta['count_7d_zero']}/"
        f"{th_meta['count_15d_zero']}/{th_meta['count_30d_zero']}  "
        f"按账号 {acct_s}",
        flush=True,
    )
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
