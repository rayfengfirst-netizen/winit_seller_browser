"""
无动销预警：基于 inventory_daily 最新快照，筛「仍有可用库存但 30 天平均日销量过低」的 SKU。

规则（可用环境变量调整）：
  WINIT_NO_SALES_MIN_AVAILABLE      可用库存下限，默认 1
  WINIT_NO_SALES_MAX_AVG30          30 天平均日销量上限，默认 0（≤ 此值算无动销）
  WINIT_NO_SALES_REQUIRE_ZERO_HIST   为 1 时还要求「历史销量」为 0
"""

from __future__ import annotations

import html as html_module
import json
import os
import sqlite3
from typing import Any, Dict, List, Optional, Tuple


def _env_float(name: str, default: str) -> float:
    try:
        return float(os.environ.get(name, default).strip())
    except ValueError:
        return float(default)


def no_sales_thresholds() -> dict:
    return {
        "min_available": _env_float("WINIT_NO_SALES_MIN_AVAILABLE", "1"),
        "max_avg30": _env_float("WINIT_NO_SALES_MAX_AVG30", "0"),
        "require_zero_hist": os.environ.get("WINIT_NO_SALES_REQUIRE_ZERO_HIST", "").lower()
        in ("1", "true", "yes"),
    }


def _float_cell(x: Any) -> Optional[float]:
    if x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def get_latest_snapshot_dates_by_account(conn: sqlite3.Connection) -> Dict[int, str]:
    cur = conn.execute(
        """
        SELECT account_id, MAX(snapshot_date) AS d
        FROM inventory_daily
        GROUP BY account_id
        ORDER BY account_id
        """
    )
    return {int(r["account_id"]): str(r["d"]) for r in cur.fetchall() if r["d"]}


def _metrics_from_row(row: sqlite3.Row) -> Dict[str, Any]:
    d = json.loads(row["row_json"])
    return {
        "snapshot_date": row["snapshot_date"],
        "account_id": row["account_id"],
        "account_username": row["account_username"] or "",
        "country": row["country"] or "",
        "warehouse": row["warehouse"] or "",
        "sku": row["sku"] or "",
        "name_zh": row["name_zh"] or "",
        "qty_available": _float_cell(row["qty_available"]) or 0.0,
        "qty_on_hand": _float_cell(row["qty_on_hand"]),
        "avg30": _float_cell(d.get("30天平均日销量")),
        "hist_sales": _float_cell(d.get("历史销量")),
        "doi": d.get("DOI"),
    }


def matches_no_sales(m: Dict[str, Any], th: dict) -> bool:
    if m["qty_available"] < th["min_available"]:
        return False
    a30 = m["avg30"]
    if a30 is None:
        a30 = 0.0
    if a30 > th["max_avg30"]:
        return False
    if th["require_zero_hist"]:
        h = m["hist_sales"]
        if h is not None and h > 0:
            return False
    return True


def collect_no_sales_rows(
    conn: sqlite3.Connection,
    *,
    account_id: Optional[int] = None,
    snapshot_date: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], dict]:
    """
    返回 (明细行列表, 使用的阈值说明 dict)。
    account_id / snapshot_date 为空时：每个账号用各自最新 snapshot_date。
    """
    th = no_sales_thresholds()
    th_meta = {
        "min_available": th["min_available"],
        "max_avg30": th["max_avg30"],
        "require_zero_hist": th["require_zero_hist"],
    }

    pairs: List[Tuple[int, str]] = []
    if account_id is not None and snapshot_date:
        pairs = [(account_id, snapshot_date)]
    elif snapshot_date and account_id is None:
        cur = conn.execute(
            """
            SELECT DISTINCT account_id FROM inventory_daily
            WHERE snapshot_date = ? ORDER BY account_id
            """,
            (snapshot_date,),
        )
        pairs = [(int(r["account_id"]), snapshot_date) for r in cur.fetchall()]
    elif account_id is not None:
        m = get_latest_snapshot_dates_by_account(conn)
        if account_id not in m:
            return [], th_meta
        pairs = [(account_id, m[account_id])]
    else:
        m = get_latest_snapshot_dates_by_account(conn)
        pairs = [(aid, d) for aid, d in m.items()]

    out: List[Dict[str, Any]] = []
    for aid, sdate in pairs:
        cur = conn.execute(
            """
            SELECT * FROM inventory_daily
            WHERE account_id = ? AND snapshot_date = ?
            """,
            (aid, sdate),
        )
        for row in cur:
            m = _metrics_from_row(row)
            if matches_no_sales(m, th):
                out.append(m)

    out.sort(key=lambda x: (x["account_id"], x["warehouse"] or "", x["sku"]))
    return out, th_meta


def public_report_base_url() -> str:
    raw = os.environ.get("WINIT_PUBLIC_BASE_URL", "").strip().rstrip("/")
    if raw:
        return raw
    return "http://127.0.0.1:8765"


def build_no_sales_detail_url() -> str:
    return f"{public_report_base_url()}/report/no-sales"


def render_no_sales_report_html(
    rows: List[Dict[str, Any]],
    th_meta: dict,
    *,
    query_note: str = "",
) -> str:
    """Flask 直接 return 的 HTML 片段（完整页面）。"""
    n = len(rows)
    rule = (
        f"可用库存 ≥ {th_meta['min_available']}，"
        f"30天平均日销量 ≤ {th_meta['max_avg30']}"
        + ("，且历史销量须为 0" if th_meta.get("require_zero_hist") else "")
    )
    note_e = html_module.escape(query_note) if query_note else ""
    rows_html = ""
    for m in rows:
        rows_html += (
            "<tr>"
            f"<td>{m['account_id']}</td>"
            f"<td>{html_module.escape(str(m.get('snapshot_date') or ''))}</td>"
            f"<td>{html_module.escape(str(m.get('warehouse') or ''))}</td>"
            f"<td>{html_module.escape(str(m.get('sku') or ''))}</td>"
            f"<td>{html_module.escape((m.get('name_zh') or '')[:100])}</td>"
            f"<td>{m.get('qty_available')}</td>"
            f"<td>{m.get('avg30') if m.get('avg30') is not None else ''}</td>"
            f"<td>{m.get('hist_sales') if m.get('hist_sales') is not None else ''}</td>"
            "</tr>"
        )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>无动销预警</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 1rem; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
    th, td {{ border: 1px solid #ccc; padding: 6px; text-align: left; }}
    th {{ background: #f4f4f4; }}
    .muted {{ color: #666; font-size: 14px; }}
    a {{ color: #0b57d0; }}
  </style>
</head>
<body>
  <p><a href="/">← 返回首页</a></p>
  <h1>无动销预警</h1>
  <p class="muted">规则：{html_module.escape(rule)}</p>
  <p class="muted">共 <strong>{n}</strong> 条 SKU。{note_e}</p>
  <table>
    <thead>
      <tr>
        <th>账号</th><th>快照日</th><th>仓库</th><th>SKU</th><th>中文名</th>
        <th>可用库存</th><th>30天均销</th><th>历史销量</th>
      </tr>
    </thead>
    <tbody>
      {rows_html or "<tr><td colspan=8>无符合条件的数据</td></tr>"}
    </tbody>
  </table>
</body>
</html>"""
