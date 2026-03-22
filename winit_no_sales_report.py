"""
无动销预警：基于 inventory_daily 各账号「最新快照日」。

规则与定时任务说明见 README「无动销预警」与 run_no_sales_morning_job.py 文档字符串。

基础条件（同时满足才算进入「均销为 0」相关计数）：
  - 可用库存 ≠ 0
  - 7 天平均库存 > 0（列名兼容「7天平均库存」「7日平均库存」）

在以上基础上按账号分别统计「7 / 15 / 30 天平均日销量为 0」的 SKU 条数；
明细页（飞书「详情」）只列五项全满足的 SKU（按账号分块）。

账号标识：.env 的 WINIT_ACCOUNT_n_LABEL（winit_accounts）。
WINIT_PUBLIC_BASE_URL 建议与 inventory_viewer 一致（生产常见 8765）。
"""

from __future__ import annotations

import html as html_module
import json
import os
import sqlite3
from collections import defaultdict
from typing import Any, DefaultDict, Dict, List, Optional, Tuple

from winit_accounts import account_display_for_row, account_id_display_map
from winit_view_format import cell_int_str
from winit_view_theme import VIEWER_THEME_CSS

# row_json 中可能出现的列名别名（万邑通导出表头）
_KEYS_AVG_INV_7 = ("7天平均库存", "7日平均库存")
_KEYS_AVG_SALES_7 = ("7天平均日销量",)
_KEYS_AVG_SALES_15 = ("15天平均日销量",)
_KEYS_AVG_SALES_30 = ("30天平均日销量",)


def _float_cell(x: Any) -> Optional[float]:
    if x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _float_from_keys(d: dict, keys: Tuple[str, ...]) -> Optional[float]:
    for k in keys:
        if k in d:
            return _float_cell(d.get(k))
    return None


def _is_nonzero_available(q: Optional[float]) -> bool:
    if q is None:
        return False
    return abs(float(q)) > 1e-12


def _avg_inv_7_ok(v: Optional[float]) -> bool:
    return v is not None and v > 0


def _sales_is_zero(v: Optional[float]) -> bool:
    """缺失视为 0（与历史「均销 ≤ 0」逻辑一致）。"""
    if v is None:
        return True
    return float(v) <= 1e-12


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


def latest_sync_finished_at(conn: sqlite3.Connection) -> Optional[str]:
    row = conn.execute("SELECT MAX(finished_at) AS m FROM sync_runs").fetchone()
    if row and row["m"]:
        return str(row["m"])
    return None


def format_download_ingest_time(
    conn: sqlite3.Connection,
    dates_by_account: Dict[int, str],
    id_map: Dict[int, str],
) -> str:
    ft = latest_sync_finished_at(conn)
    snap_parts: List[str] = []
    for aid, d in sorted(dates_by_account.items()):
        tag = account_display_for_row(aid, "", id_map=id_map)
        snap_parts.append(f"{tag}:{d}")
    snap = ", ".join(snap_parts)
    if ft:
        return f"{ft}" + (f"（快照日 {snap}）" if snap else "")
    return snap or "暂无同步记录"


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
        "qty_available": _float_cell(row["qty_available"]),
        "qty_on_hand": _float_cell(row["qty_on_hand"]),
        "avg_inv_7": _float_from_keys(d, _KEYS_AVG_INV_7),
        "avg_sales_7": _float_from_keys(d, _KEYS_AVG_SALES_7),
        "avg_sales_15": _float_from_keys(d, _KEYS_AVG_SALES_15),
        "avg_sales_30": _float_from_keys(d, _KEYS_AVG_SALES_30),
        "hist_sales": _float_cell(d.get("历史销量")),
        "doi": d.get("DOI"),
    }


def passes_base_filter(m: Dict[str, Any]) -> bool:
    if not _is_nonzero_available(m["qty_available"]):
        return False
    return _avg_inv_7_ok(m["avg_inv_7"])


def passes_strict_no_sales(m: Dict[str, Any]) -> bool:
    if not passes_base_filter(m):
        return False
    return (
        _sales_is_zero(m["avg_sales_7"])
        and _sales_is_zero(m["avg_sales_15"])
        and _sales_is_zero(m["avg_sales_30"])
    )


STAT_RULE_LINE = (
    "统计口径：可用库存不等于0，7日平均库存大于0，"
    "7天平均日销量为0，15天平均日销量为0，30天平均日销量为0"
)


def collect_no_sales_rows(
    conn: sqlite3.Connection,
    *,
    account_id: Optional[int] = None,
    snapshot_date: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], dict]:
    """
    返回 (明细行列表, meta)。
    明细为统计口径五项全满足的 SKU（含 account_display）；meta 含按账号拆分的计数与合计。
    """
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
            return [], _empty_meta(conn)
        pairs = [(account_id, m[account_id])]
    else:
        m = get_latest_snapshot_dates_by_account(conn)
        pairs = [(aid, d) for aid, d in m.items()]

    strict_rows: List[Dict[str, Any]] = []
    per: DefaultDict[int, Dict[str, Any]] = defaultdict(
        lambda: {
            "c7": 0,
            "c15": 0,
            "c30": 0,
            "strict": 0,
            "username": "",
            "snapshot_date": "",
        }
    )

    for aid, sdate in pairs:
        pa0 = per[aid]
        pa0["snapshot_date"] = sdate
        cur = conn.execute(
            """
            SELECT * FROM inventory_daily
            WHERE account_id = ? AND snapshot_date = ?
            """,
            (aid, sdate),
        )
        for row in cur:
            m = _metrics_from_row(row)
            pa = per[aid]
            pa["snapshot_date"] = sdate
            if not pa["username"] and m["account_username"]:
                pa["username"] = m["account_username"]
            if passes_base_filter(m):
                if _sales_is_zero(m["avg_sales_7"]):
                    pa["c7"] += 1
                if _sales_is_zero(m["avg_sales_15"]):
                    pa["c15"] += 1
                if _sales_is_zero(m["avg_sales_30"]):
                    pa["c30"] += 1
            if passes_strict_no_sales(m):
                pa["strict"] += 1
                strict_rows.append(m)

    id_map = account_id_display_map()
    for m in strict_rows:
        m["account_display"] = account_display_for_row(
            int(m["account_id"]),
            m.get("account_username") or "",
            id_map=id_map,
        )

    def _row_sort_key(x: Dict[str, Any]) -> Tuple:
        q = x.get("qty_available")
        try:
            qv = float(q) if q is not None else float("-inf")
        except (TypeError, ValueError):
            qv = float("-inf")
        return (int(x["account_id"]), -qv, x.get("warehouse") or "", x.get("sku") or "")

    strict_rows.sort(key=_row_sort_key)

    by_account: List[Dict[str, Any]] = []
    for aid in sorted(per.keys()):
        p = per[aid]
        by_account.append(
            {
                "account_id": aid,
                "account_display": account_display_for_row(
                    aid, str(p["username"] or ""), id_map=id_map
                ),
                "snapshot_date": p["snapshot_date"],
                "count_7d_zero": p["c7"],
                "count_15d_zero": p["c15"],
                "count_30d_zero": p["c30"],
                "strict_count": p["strict"],
            }
        )

    dates = get_latest_snapshot_dates_by_account(conn)
    c7 = sum(b["count_7d_zero"] for b in by_account)
    c15 = sum(b["count_15d_zero"] for b in by_account)
    c30 = sum(b["count_30d_zero"] for b in by_account)
    th_meta = {
        "by_account": by_account,
        "count_7d_zero": c7,
        "count_15d_zero": c15,
        "count_30d_zero": c30,
        "ingest_time_display": format_download_ingest_time(conn, dates, id_map),
        "stat_rule_line": STAT_RULE_LINE,
    }
    return strict_rows, th_meta


def _empty_meta(conn: sqlite3.Connection) -> dict:
    id_map = account_id_display_map()
    dates = get_latest_snapshot_dates_by_account(conn)
    return {
        "by_account": [],
        "count_7d_zero": 0,
        "count_15d_zero": 0,
        "count_30d_zero": 0,
        "ingest_time_display": format_download_ingest_time(conn, dates, id_map),
        "stat_rule_line": STAT_RULE_LINE,
    }


def public_report_base_url() -> str:
    """未设置时默认本机 8765（与生产环境 inventory 首页端口一致）。"""
    raw = os.environ.get("WINIT_PUBLIC_BASE_URL", "").strip().rstrip("/")
    if raw:
        return raw
    return "http://127.0.0.1:8765"


def build_no_sales_detail_url() -> str:
    return f"{public_report_base_url()}/report/no-sales"


def format_no_sales_feishu_text(th_meta: dict, detail_url: str) -> str:
    lines = [
        "无动销预警通知",
        f"下载入库文件时间：{th_meta['ingest_time_display']}",
        "",
    ]
    blocks = th_meta.get("by_account") or []
    if not blocks:
        lines.append("（暂无账号快照数据）")
        lines.append("")
    for blk in blocks:
        lines.append(f"【{blk['account_display']}】快照日：{blk['snapshot_date']}")
        lines.append(f"7天平均日销量为0总计：{cell_int_str(blk['count_7d_zero'])}")
        lines.append(f"15天平均日销量为0总计：{cell_int_str(blk['count_15d_zero'])}")
        lines.append(f"30天平均日销量为0总计：{cell_int_str(blk['count_30d_zero'])}")
        lines.append(f"五项全满足 SKU：{cell_int_str(blk['strict_count'])}")
        lines.append("")
    lines.append(f"详情查看：{detail_url}")
    lines.append("")
    lines.append(th_meta["stat_rule_line"])
    return "\n".join(lines)


def _qty_available_sort_key(m: Dict[str, Any]) -> float:
    q = m.get("qty_available")
    try:
        return float(q) if q is not None else float("-inf")
    except (TypeError, ValueError):
        return float("-inf")


def render_no_sales_report_html(
    rows: List[Dict[str, Any]],
    th_meta: dict,
    *,
    query_note: str = "",
) -> str:
    """Flask 直接 return 的 HTML 片段（完整页面）；每账号独立区块 + 表内按可用库存降序。"""
    n = len(rows)
    rule = (
        "可用库存≠0，7日平均库存>0；下列 SKU 同时满足 "
        "7 / 15 / 30 天平均日销量均为 0（见页底统计口径）。"
    )
    note_e = html_module.escape(query_note) if query_note else ""
    stat_e = html_module.escape(th_meta.get("stat_rule_line", STAT_RULE_LINE))

    rows_by_aid: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for m in rows:
        rows_by_aid[int(m["account_id"])].append(m)

    tables_html = ""
    account_order = [b["account_id"] for b in (th_meta.get("by_account") or [])]
    seen = set(account_order)
    for aid in sorted(rows_by_aid.keys()):
        if aid not in seen:
            account_order.append(aid)
    for aid in account_order:
        sub = list(rows_by_aid.get(aid) or [])
        sub.sort(key=_qty_available_sort_key, reverse=True)
        blk = next(
            (b for b in (th_meta.get("by_account") or []) if b["account_id"] == aid),
            None,
        )
        if blk:
            h = html_module.escape(str(blk["account_display"]))
            snap = html_module.escape(str(blk.get("snapshot_date") or ""))
            c7 = cell_int_str(blk["count_7d_zero"])
            c15 = cell_int_str(blk["count_15d_zero"])
            c30 = cell_int_str(blk["count_30d_zero"])
            cs = cell_int_str(blk["strict_count"])
            stats_block = (
                "<div class=\"stat-pills\">"
                f"<span class=\"stat-pill\">快照日 {snap}</span>"
                f"<span class=\"stat-pill blue\">7天均销=0：{html_module.escape(c7)}</span>"
                f"<span class=\"stat-pill blue\">15天均销=0：{html_module.escape(c15)}</span>"
                f"<span class=\"stat-pill blue\">30天均销=0：{html_module.escape(c30)}</span>"
                f"<span class=\"stat-pill\" style=\"background:#fef08a;color:#854d0e;font-weight:700\">"
                f"五项全满足：{html_module.escape(cs)} 条</span>"
                "</div>"
            )
        else:
            h = html_module.escape(
                account_display_for_row(aid, sub[0].get("account_username", "") if sub else "")
            )
            snap = html_module.escape(str(sub[0].get("snapshot_date") or "") if sub else "")
            stats_block = (
                f"<div class=\"stat-pills\"><span class=\"stat-pill\">快照日 {snap}</span></div>"
                if snap
                else ""
            )

        body = ""
        for m in sub:
            body += (
                "<tr>"
                f"<td>{html_module.escape(str(m.get('snapshot_date') or ''))}</td>"
                f"<td>{html_module.escape(str(m.get('warehouse') or ''))}</td>"
                f"<td>{html_module.escape(str(m.get('sku') or ''))}</td>"
                f"<td>{html_module.escape((m.get('name_zh') or '')[:100])}</td>"
                f"<td class=\"num\">{html_module.escape(cell_int_str(m.get('qty_available')))}</td>"
                f"<td class=\"num\">{html_module.escape(cell_int_str(m.get('avg_inv_7')))}</td>"
                f"<td class=\"num\">{html_module.escape(cell_int_str(m.get('avg_sales_7')))}</td>"
                f"<td class=\"num\">{html_module.escape(cell_int_str(m.get('avg_sales_15')))}</td>"
                f"<td class=\"num\">{html_module.escape(cell_int_str(m.get('avg_sales_30')))}</td>"
                "</tr>"
            )
        empty_row = "<tr><td colspan=9>该账号无五项全满足 SKU</td></tr>"
        tables_html += (
            f"<section class=\"card acct-section\">"
            f"<h2 class=\"acct\">{h}</h2>"
            f"{stats_block}"
            "<table class=\"data\">"
            "<thead><tr>"
            "<th>快照日</th><th>仓库</th><th>SKU</th><th>中文名</th>"
            "<th class=\"num\">可用库存</th><th class=\"num\">7日均库</th>"
            "<th class=\"num\">7天均销</th><th class=\"num\">15天均销</th><th class=\"num\">30天均销</th>"
            "</tr></thead><tbody>"
            f"{body or empty_row}"
            "</tbody></table>"
            "</section>"
        )

    if not tables_html and not rows:
        tables_html = (
            "<section class=\"card acct-section\"><h2 class=\"acct\">数据</h2>"
            "<table class=\"data\"><thead><tr>"
            "<th>快照日</th><th>仓库</th><th>SKU</th><th>中文名</th>"
            "<th class=\"num\">可用库存</th><th class=\"num\">7日均库</th>"
            "<th class=\"num\">7天均销</th><th class=\"num\">15天均销</th><th class=\"num\">30天均销</th>"
            "</tr></thead><tbody>"
            "<tr><td colspan=9>无符合条件的数据</td></tr></tbody></table></section>"
        )

    cnt_line = (
        f"全账号合计 — 基础条件下均销为 0："
        f"7天 {cell_int_str(th_meta['count_7d_zero'])} · "
        f"15天 {cell_int_str(th_meta['count_15d_zero'])} · "
        f"30天 {cell_int_str(th_meta['count_30d_zero'])} · "
        f"五项全满足 SKU {cell_int_str(n)} 条。"
        f"下表按账号分块，数量均为整数，可用库存从高到低。"
    )

    NO_SALES_EXTRA_CSS = """
    h2.acct { font-size: 1.15rem; margin: 0 0 0.6rem 0; color: var(--accent-dark); }
    section.card.acct-section { margin-bottom: 1.25rem; }
    """

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>无动销预警</title>
  <style>{VIEWER_THEME_CSS}{NO_SALES_EXTRA_CSS}</style>
</head>
<body>
<div class="page">
  <div class="toolbar" style="margin-bottom:0.75rem">
    <a href="/">← 返回汇总</a>
  </div>
  <header class="banner">
    <h1>无动销预警</h1>
    <p class="sub">可用库存≠0 且 7日均库&gt;0；五项统计口径全满足的 SKU（页面数字均为整数）</p>
  </header>
  <div class="note-strip">
    <strong>规则与合计</strong> · {html_module.escape(rule)}<br/>
    <span class="muted">入库/快照：</span>{html_module.escape(str(th_meta.get("ingest_time_display") or ""))}<br/>
    <strong>{html_module.escape(cnt_line)}</strong>
  </div>
  <p class="muted">按账号分块；新增账号会自动多一块。{note_e}</p>
  <p class="muted" style="margin-bottom:1rem">{stat_e}</p>
  {tables_html}
</div>
</body>
</html>"""
