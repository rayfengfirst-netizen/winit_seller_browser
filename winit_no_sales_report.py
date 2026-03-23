"""
无动销预警：基于 inventory_daily 各账号「最新快照日」。

规则与定时任务说明见 README「无动销预警」与 run_no_sales_morning_job.py 文档字符串。

基础条件（**每个仓库行**；仅将满足条件的行纳入该 SKU 的聚合与明细）：
  - **可用库存 ≠ 0**（不再使用「7 日平均库存」字段，避免与业务侧口径不一致）

同一 SKU 多仓：只对**通过上述条件的行**求和，再判定 7/15/30 天均销与①②③分类。
仅在「聚合后 7 天平均日销量为 0」的 SKU 上，再分为三种互斥情况（另有「其它」）：
  ① 7 天=0，且 15、30 天均≠0
  ② 7、15 天=0，且 30 天≠0
  ③ 7、15、30 天均为 0

飞书报各情况 SKU **个数**；详情页按账号 → 情况分块，表内按**仓库**列库存与各期均销。

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
        "avg_sales_7": _float_from_keys(d, _KEYS_AVG_SALES_7),
        "avg_sales_15": _float_from_keys(d, _KEYS_AVG_SALES_15),
        "avg_sales_30": _float_from_keys(d, _KEYS_AVG_SALES_30),
        "hist_sales": _float_cell(d.get("历史销量")),
        "doi": d.get("DOI"),
    }


def passes_base_filter_no_sales(m: Dict[str, Any]) -> bool:
    """无动销：仅要求可用库存≠0（不使用 7 日均库）。"""
    return _is_nonzero_available(m.get("qty_available"))


def passes_base_filter(m: Dict[str, Any]) -> bool:
    """兼容旧名：与无动销当前口径一致（仅可用库存）。"""
    return passes_base_filter_no_sales(m)


def passes_strict_no_sales(m: Dict[str, Any]) -> bool:
    """单仓行：三种均销均为 0（在基础条件之上）。"""
    if not passes_base_filter_no_sales(m):
        return False
    return (
        _sales_is_zero(m["avg_sales_7"])
        and _sales_is_zero(m["avg_sales_15"])
        and _sales_is_zero(m["avg_sales_30"])
    )


def _sum_float(v: Any) -> float:
    if v is None:
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _sales_cell_str(v: Any) -> str:
    """均销等小数值：保留小数，避免四舍五入成整数 0 与分类口径矛盾。"""
    if v is None or v == "":
        return ""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return str(v)
    if abs(x) < 1e-12:
        return "0"
    if abs(x - round(x)) < 1e-9:
        return str(int(round(x)))
    return f"{x:.4f}".rstrip("0").rstrip(".")


def _sku_aggregate_metrics(grp: List[Dict[str, Any]]) -> Dict[str, Any]:
    """同一账号、同一 SKU 下多仓行的聚合指标（可用与各期均销按行求和）。"""
    qty_sum = 0.0
    s7 = s15 = s30 = 0.0
    for m in grp:
        qty_sum += _sum_float(m.get("qty_available"))
        s7 += _sum_float(m.get("avg_sales_7"))
        s15 += _sum_float(m.get("avg_sales_15"))
        s30 += _sum_float(m.get("avg_sales_30"))
    return {
        "qty_sum": qty_sum,
        "avg_sales_7": s7,
        "avg_sales_15": s15,
        "avg_sales_30": s30,
    }


def passes_base_filter_sku(agg: Dict[str, Any]) -> bool:
    """SKU 聚合后仍有可用库存（各参与行之和）。"""
    return _is_nonzero_available(agg["qty_sum"])


def classify_sku_case(s7: float, s15: float, s30: float) -> int:
    """
    在「聚合后 7 天均销已为 0」前提下，分为 1 / 2 / 3；
    无法归入上述三者时返回 0（其它）。
    """
    if not _sales_is_zero(s7):
        return 0
    z15 = _sales_is_zero(s15)
    z30 = _sales_is_zero(s30)
    if z15 and z30:
        return 3
    if z15 and not z30:
        return 2
    if not z15 and not z30:
        return 1
    return 0


CASE_KIND_LABEL = {
    1: "① 7天=0，15/30天≠0",
    2: "② 7/15天=0，30天≠0",
    3: "③ 7/15/30天均=0",
    0: "其它（仅7天=0）",
}

CASE_KIND_SECTION_HTML = {
    1: "情况①：聚合后 7 天均销=0，且 15、30 天均销≠0（先 SKU 汇总，再分仓）",
    2: "情况②：聚合后 7、15 天均销=0，且 30 天均销≠0（先 SKU 汇总，再分仓）",
    3: "情况③：聚合后 7、15、30 天均销=0（先 SKU 汇总，再分仓）",
    0: "其它：聚合后 7 天均销=0，但不属于上列三种（先 SKU 汇总，再分仓）",
}

# 页面模块标题（短标题 + 副标题，便于扫读）
CASE_KIND_UI = {
    1: {"title": "情况 ①", "sub": "7 天均销=0，15 与 30 天均≠0"},
    2: {"title": "情况 ②", "sub": "7、15 天=0，30 天≠0"},
    3: {"title": "情况 ③", "sub": "7、15、30 天均为 0"},
    0: {"title": "其它", "sub": "仅 7 天=0，不满足上列"},
}

STAT_RULE_LINE = (
    "统计口径：仅「单仓可用≠0」的仓库行参与该 SKU 加总；"
    "再对聚合后 7 天均销=0 的 SKU 分①②③（或其它）。均销列保留小数。"
)


def collect_no_sales_rows(
    conn: sqlite3.Connection,
    *,
    account_id: Optional[int] = None,
    snapshot_date: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], dict]:
    """
    返回 (明细行列表, meta)。
    明细为：通过 SKU 聚合分类后，该 SKU 下各仓库行（带 case_kind）；meta 含按账号拆分的①②③及其它计数。
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

    detail_rows: List[Dict[str, Any]] = []
    per: DefaultDict[int, Dict[str, Any]] = defaultdict(
        lambda: {
            "count_case1": 0,
            "count_case2": 0,
            "count_case3": 0,
            "count_case_other": 0,
            "username": "",
            "snapshot_date": "",
        }
    )

    for aid, sdate in pairs:
        pa = per[aid]
        pa["snapshot_date"] = sdate
        cur = conn.execute(
            """
            SELECT * FROM inventory_daily
            WHERE account_id = ? AND snapshot_date = ?
            """,
            (aid, sdate),
        )
        rows_buf: List[Dict[str, Any]] = []
        for row in cur:
            m = _metrics_from_row(row)
            if not pa["username"] and m["account_username"]:
                pa["username"] = m["account_username"]
            rows_buf.append(m)

        by_sku: DefaultDict[str, List[Dict[str, Any]]] = defaultdict(list)
        for m in rows_buf:
            sku_k = (m.get("sku") or "").strip()
            if not sku_k:
                continue
            by_sku[sku_k].append(m)

        for sku_k, grp in by_sku.items():
            # 基础条件按「仓行」生效：只把满足条件的行纳入聚合与详情，避免未达标仓污染 SKU 汇总
            grp_ok = [m for m in grp if passes_base_filter_no_sales(m)]
            if not grp_ok:
                continue
            agg = _sku_aggregate_metrics(grp_ok)
            if not passes_base_filter_sku(agg):
                continue
            if not _sales_is_zero(agg["avg_sales_7"]):
                continue
            ck = classify_sku_case(
                agg["avg_sales_7"],
                agg["avg_sales_15"],
                agg["avg_sales_30"],
            )
            if ck == 1:
                pa["count_case1"] += 1
            elif ck == 2:
                pa["count_case2"] += 1
            elif ck == 3:
                pa["count_case3"] += 1
            else:
                pa["count_case_other"] += 1
            for m in grp_ok:
                m2 = dict(m)
                m2["case_kind"] = ck
                m2["sku_agg"] = sku_k
                detail_rows.append(m2)

    id_map = account_id_display_map()
    for m in detail_rows:
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
        ck = int(x.get("case_kind") or 0)
        case_order = {1: 1, 2: 2, 3: 3, 0: 4}
        return (
            int(x["account_id"]),
            case_order.get(ck, 9),
            -qv,
            x.get("warehouse") or "",
            x.get("sku") or "",
        )

    detail_rows.sort(key=_row_sort_key)

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
                "count_case1": p["count_case1"],
                "count_case2": p["count_case2"],
                "count_case3": p["count_case3"],
                "count_case_other": p["count_case_other"],
            }
        )

    dates = get_latest_snapshot_dates_by_account(conn)
    c1 = sum(b["count_case1"] for b in by_account)
    c2 = sum(b["count_case2"] for b in by_account)
    c3 = sum(b["count_case3"] for b in by_account)
    c0 = sum(b["count_case_other"] for b in by_account)
    th_meta = {
        "by_account": by_account,
        "count_case1": c1,
        "count_case2": c2,
        "count_case3": c3,
        "count_case_other": c0,
        "ingest_time_display": format_download_ingest_time(conn, dates, id_map),
        "stat_rule_line": STAT_RULE_LINE,
    }
    return detail_rows, th_meta


def _empty_meta(conn: sqlite3.Connection) -> dict:
    id_map = account_id_display_map()
    dates = get_latest_snapshot_dates_by_account(conn)
    return {
        "by_account": [],
        "count_case1": 0,
        "count_case2": 0,
        "count_case3": 0,
        "count_case_other": 0,
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
        lines.append(
            f"{CASE_KIND_LABEL[1]} SKU 数：{cell_int_str(blk['count_case1'])}"
        )
        lines.append(
            f"{CASE_KIND_LABEL[2]} SKU 数：{cell_int_str(blk['count_case2'])}"
        )
        lines.append(
            f"{CASE_KIND_LABEL[3]} SKU 数：{cell_int_str(blk['count_case3'])}"
        )
        if blk.get("count_case_other", 0):
            lines.append(
                f"{CASE_KIND_LABEL[0]} SKU 数：{cell_int_str(blk['count_case_other'])}"
            )
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


def _table_rows_html(sub: List[Dict[str, Any]]) -> str:
    body = ""
    for m in sub:
        body += (
            "<tr>"
            f"<td>{html_module.escape(str(m.get('snapshot_date') or ''))}</td>"
            f"<td>{html_module.escape(str(m.get('warehouse') or ''))}</td>"
            f"<td>{html_module.escape(str(m.get('sku') or ''))}</td>"
            f"<td>{html_module.escape((m.get('name_zh') or '')[:100])}</td>"
            f"<td class=\"num\">{html_module.escape(cell_int_str(m.get('qty_available')))}</td>"
            f"<td class=\"num\">{html_module.escape(_sales_cell_str(m.get('avg_sales_7')))}</td>"
            f"<td class=\"num\">{html_module.escape(_sales_cell_str(m.get('avg_sales_15')))}</td>"
            f"<td class=\"num\">{html_module.escape(_sales_cell_str(m.get('avg_sales_30')))}</td>"
            "</tr>"
        )
    return body


def _rows_grouped_by_sku_sorted(
    sub: List[Dict[str, Any]],
) -> List[Tuple[str, List[Dict[str, Any]]]]:
    """同一情况下按 SKU 分组；SKU 顺序：可用库存合计降序，再按 SKU 字符串。"""
    by_sku: DefaultDict[str, List[Dict[str, Any]]] = defaultdict(list)
    for m in sub:
        k = (m.get("sku_agg") or m.get("sku") or "").strip() or "__empty__"
        by_sku[k].append(m)

    def _sku_block_key(item: Tuple[str, List[Dict[str, Any]]]) -> Tuple[float, str]:
        sku_k, rows = item
        agg = _sku_aggregate_metrics(rows)
        return (-float(agg["qty_sum"]), sku_k)

    items = list(by_sku.items())
    items.sort(key=_sku_block_key)
    return items


def _sku_card_html(
    grp_sorted: List[Dict[str, Any]], sku_k: str, thead: str
) -> str:
    """单个 SKU：聚合指标网格 + 分仓表。"""
    agg = _sku_aggregate_metrics(grp_sorted)
    sku_disp = sku_k if sku_k != "__empty__" else "(空 SKU)"
    sku_e = html_module.escape(sku_disp)
    nm = (grp_sorted[0].get("name_zh") or "")[:120]
    nm_e = html_module.escape(nm) if nm else ""
    metrics = [
        ("可用库存（合计）", cell_int_str(agg["qty_sum"])),
        ("7 天均销（合计）", _sales_cell_str(agg["avg_sales_7"])),
        ("15 天均销（合计）", _sales_cell_str(agg["avg_sales_15"])),
        ("30 天均销（合计）", _sales_cell_str(agg["avg_sales_30"])),
    ]
    grid = "".join(
        "<div class=\"ns-metric\">"
        f"<span class=\"ns-metric-k\">{html_module.escape(k)}</span>"
        f"<span class=\"ns-metric-v\">{html_module.escape(v)}</span>"
        "</div>"
        for k, v in metrics
    )
    name_block = (
        f"<p class=\"ns-sku-name\">{nm_e}</p>" if nm_e else ""
    )
    return (
        "<article class=\"ns-sku-card\">"
        "<header class=\"ns-sku-card-head\">"
        f"<span class=\"ns-sku-code\">{sku_e}</span>"
        "</header>"
        f"{name_block}"
        "<div class=\"ns-agg-grid\" role=\"group\" aria-label=\"SKU 聚合后指标\">"
        f"{grid}"
        "</div>"
        "<p class=\"ns-wh-hint\">↓ 分仓明细（每行一仓；均销为小数属正常）</p>"
        "<div class=\"ns-table-wrap\">"
        "<table class=\"data sku-wh-table\">"
        f"{thead}<tbody>{_table_rows_html(grp_sorted)}</tbody></table>"
        "</div>"
        "</article>"
    )


def _case_section_html_sku_then_warehouse(
    sub: List[Dict[str, Any]], thead: str, case_kind: int
) -> str:
    """情况块：带色条标题 + 内层 SKU 卡片列表。"""
    if not sub:
        return ""
    ui = CASE_KIND_UI.get(case_kind, CASE_KIND_UI[0])
    badge_map = {1: "①", 2: "②", 3: "③", 0: "※"}
    badge = badge_map.get(case_kind, "?")
    parts: List[str] = []
    for sku_k, grp_rows in _rows_grouped_by_sku_sorted(sub):
        grp_sorted = sorted(grp_rows, key=_qty_available_sort_key, reverse=True)
        parts.append(_sku_card_html(grp_sorted, sku_k, thead))
    inner = "".join(parts)
    return (
        f"<section class=\"ns-case ns-case-{case_kind}\" aria-label=\"{html_module.escape(ui['title'])}\">"
        "<header class=\"ns-case-head\">"
        f"<span class=\"ns-case-badge\">{badge}</span>"
        "<div class=\"ns-case-titles\">"
        f"<span class=\"ns-case-title\">{html_module.escape(ui['title'])}</span>"
        f"<span class=\"ns-case-sub\">{html_module.escape(ui['sub'])}</span>"
        "</div>"
        "</header>"
        f"<div class=\"ns-case-body\">{inner}</div>"
        "</section>"
    )


def render_no_sales_report_html(
    rows: List[Dict[str, Any]],
    th_meta: dict,
    *,
    query_note: str = "",
) -> str:
    """每账号独立区块；块内按情况①②③分块，每块内先 SKU 汇总再分仓。"""
    n_rows = len(rows)
    rule = (
        "基础条件：单仓「可用≠0」即参与该 SKU 加总（不使用 7 日均库）。"
        "①②③ 以聚合均销为准；多账号时可用顶部 Tab 切换。"
    )
    note_e = html_module.escape(query_note) if query_note else ""
    stat_e = html_module.escape(th_meta.get("stat_rule_line", STAT_RULE_LINE))

    rows_by_aid: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for m in rows:
        rows_by_aid[int(m["account_id"])].append(m)

    panel_fragments: List[Tuple[int, str, str]] = []
    account_order = [b["account_id"] for b in (th_meta.get("by_account") or [])]
    seen = set(account_order)
    for aid in sorted(rows_by_aid.keys()):
        if aid not in seen:
            account_order.append(aid)

    thead = (
        "<thead><tr>"
        "<th>快照</th><th>仓库</th><th>SKU</th><th>品名</th>"
        "<th class=\"num\">可用</th>"
        "<th class=\"num\">7天均销</th><th class=\"num\">15天均销</th><th class=\"num\">30天均销</th>"
        "</tr></thead>"
    )

    case_block_order = (1, 2, 3, 0)

    for aid in account_order:
        sub_all = list(rows_by_aid.get(aid) or [])
        blk = next(
            (b for b in (th_meta.get("by_account") or []) if b["account_id"] == aid),
            None,
        )
        if blk:
            h = html_module.escape(str(blk["account_display"]))
            snap = html_module.escape(str(blk.get("snapshot_date") or ""))
            n1 = cell_int_str(blk["count_case1"])
            n2 = cell_int_str(blk["count_case2"])
            n3 = cell_int_str(blk["count_case3"])
            n0 = cell_int_str(blk.get("count_case_other", 0))
            stats_block = (
                "<div class=\"ns-kpi-strip\" role=\"group\" aria-label=\"本账号 SKU 数汇总\">"
                f"<div class=\"ns-kpi ns-kpi-a\"><span class=\"ns-kpi-n\">{html_module.escape(n1)}</span>"
                "<span class=\"ns-kpi-l\">①</span></div>"
                f"<div class=\"ns-kpi ns-kpi-b\"><span class=\"ns-kpi-n\">{html_module.escape(n2)}</span>"
                "<span class=\"ns-kpi-l\">②</span></div>"
                f"<div class=\"ns-kpi ns-kpi-c\"><span class=\"ns-kpi-n\">{html_module.escape(n3)}</span>"
                "<span class=\"ns-kpi-l\">③</span></div>"
            )
            if blk.get("count_case_other", 0):
                stats_block += (
                    f"<div class=\"ns-kpi ns-kpi-o\"><span class=\"ns-kpi-n\">{html_module.escape(n0)}</span>"
                    "<span class=\"ns-kpi-l\">其它</span></div>"
                )
            stats_block += "</div>"
            acct_head = (
                "<header class=\"ns-acct-head\">"
                f"<h2 class=\"acct\">{h}</h2>"
                f"<span class=\"ns-snap-chip\">快照 {snap}</span>"
                "</header>"
            )
        else:
            h = html_module.escape(
                account_display_for_row(
                    aid, sub_all[0].get("account_username", "") if sub_all else ""
                )
            )
            snap = html_module.escape(
                str(sub_all[0].get("snapshot_date") or "") if sub_all else ""
            )
            stats_block = ""
            acct_head = (
                "<header class=\"ns-acct-head\">"
                f"<h2 class=\"acct\">{h}</h2>"
                + (
                    f"<span class=\"ns-snap-chip\">快照 {snap}</span>"
                    if snap
                    else ""
                )
                + "</header>"
            )

        by_case: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
        for m in sub_all:
            by_case[int(m.get("case_kind") or 0)].append(m)

        case_sections = ""
        for ck in case_block_order:
            sub = by_case.get(ck) or []
            if not sub:
                continue
            case_sections += _case_section_html_sku_then_warehouse(sub, thead, ck)

        empty_acct = (
            "<p class=\"muted\">该账号下无符合「聚合后 7 天均销=0」的 SKU。</p>"
            if not case_sections
            else ""
        )
        section_html = (
            "<section class=\"card acct-section ns-account\">"
            f"{acct_head}"
            f"{stats_block}"
            f"<div class=\"ns-account-cases\">{case_sections or empty_acct}</div>"
            "</section>"
        )
        panel_fragments.append((aid, h, section_html))

    tables_html = ""
    if not panel_fragments and not rows:
        tables_html = (
            "<section class=\"card acct-section\"><h2 class=\"acct\">数据</h2>"
            "<table class=\"data\">"
            f"{thead}<tbody>"
            "<tr><td colspan=8>无符合条件的数据</td></tr></tbody></table></section>"
        )
    elif len(panel_fragments) == 1:
        tables_html = (
            f'<div class="ns-tabs-wrap ns-tabs-single">{panel_fragments[0][2]}</div>'
        )
    else:
        tab_btns: List[str] = []
        tab_panels: List[str] = []
        for i, (aid, lbl_e, inner) in enumerate(panel_fragments):
            act = " active" if i == 0 else ""
            sel = "true" if i == 0 else "false"
            tab_btns.append(
                f"<button type=\"button\" class=\"ns-tab{act}\" role=\"tab\" "
                f'aria-selected="{sel}" data-panel="{aid}" id="ns-tabbtn-{aid}">{lbl_e}</button>'
            )
            tab_panels.append(
                f'<div class="ns-tab-panel{act}" role="tabpanel" id="ns-panel-{aid}" '
                f'aria-labelledby="ns-tabbtn-{aid}" tabindex="0">{inner}</div>'
            )
        tables_html = (
            '<div class="ns-tabs-wrap">'
            '<div class="ns-tab-bar" role="tablist" aria-label="按账号切换">'
            f"{''.join(tab_btns)}"
            "</div>"
            f'<div class="ns-tab-panels">{"".join(tab_panels)}</div>'
            "</div>"
        )

    c1 = cell_int_str(th_meta.get("count_case1", 0))
    c2 = cell_int_str(th_meta.get("count_case2", 0))
    c3 = cell_int_str(th_meta.get("count_case3", 0))
    c0 = th_meta.get("count_case_other", 0)
    cnt_line = (
        f"全账号合计 — ① {c1} 个 SKU · ② {c2} 个 SKU · ③ {c3} 个 SKU"
        + (f" · 其它 {cell_int_str(c0)} 个 SKU" if c0 else "")
        + f"。共 {cell_int_str(n_rows)} 条仓库明细行（同一 SKU 多仓多行）。"
    )

    NO_SALES_EXTRA_CSS = """
    .sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
      overflow: hidden; clip: rect(0,0,0,0); white-space: nowrap; border: 0; }
    .ns-legend {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 0.55rem 0.9rem;
      margin-bottom: 1rem;
      box-shadow: 0 1px 3px rgba(15,23,42,.06);
    }
    .ns-legend-row {
      display: flex; flex-wrap: wrap; gap: 0.5rem 1.25rem;
      font-size: 0.82rem; color: var(--muted); align-items: center;
    }
    .ns-leg { display: inline-flex; align-items: center; gap: 0.35rem; }
    i.ns-dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; font-style: normal; }
    i.ns-dot.c1 { background: #0d9488; }
    i.ns-dot.c2 { background: #2563eb; }
    i.ns-dot.c3 { background: #d97706; }
    i.ns-dot.c0 { background: #94a3b8; }
    .ns-account .ns-acct-head {
      display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between;
      gap: 0.5rem 1rem; margin: 0 0 0.65rem 0; padding-bottom: 0.6rem;
      border-bottom: 2px solid var(--accent-soft);
    }
    .ns-account .ns-acct-head h2.acct {
      margin: 0; padding: 0; border: none; font-size: 1.2rem; color: var(--accent-dark);
    }
    .ns-snap-chip {
      font-size: 0.8rem; font-weight: 600; color: #1e40af;
      background: var(--accent2-soft); padding: 0.25rem 0.65rem; border-radius: 999px;
    }
    .ns-kpi-strip {
      display: flex; flex-wrap: wrap; gap: 0.5rem; margin: 0 0 1rem 0;
    }
    .ns-kpi {
      min-width: 4.5rem; text-align: center; padding: 0.45rem 0.65rem;
      border-radius: 10px; border: 1px solid var(--border);
      background: linear-gradient(180deg, #fff 0%, #f8fafc 100%);
    }
    .ns-kpi-n { display: block; font-size: 1.25rem; font-weight: 800;
      font-variant-numeric: tabular-nums; color: var(--text); line-height: 1.15; }
    .ns-kpi-l { font-size: 0.72rem; font-weight: 700; color: var(--muted);
      text-transform: uppercase; letter-spacing: 0.04em; }
    .ns-kpi-a { border-left: 4px solid #0d9488; }
    .ns-kpi-b { border-left: 4px solid #2563eb; }
    .ns-kpi-c { border-left: 4px solid #d97706; }
    .ns-kpi-o { border-left: 4px solid #94a3b8; }
    .ns-account-cases { display: flex; flex-direction: column; gap: 1.1rem; }
    .ns-case {
      border-radius: 12px; border: 1px solid var(--border);
      overflow: hidden; background: #fafafa;
    }
    .ns-case-1 { border-left: 5px solid #0d9488; }
    .ns-case-2 { border-left: 5px solid #2563eb; }
    .ns-case-3 { border-left: 5px solid #d97706; }
    .ns-case-0 { border-left: 5px solid #94a3b8; }
    .ns-case-head {
      display: flex; align-items: flex-start; gap: 0.65rem;
      padding: 0.65rem 0.85rem;
      background: linear-gradient(90deg, rgba(255,255,255,.95), #f1f5f9);
      border-bottom: 1px solid var(--border);
    }
    .ns-case-badge {
      flex-shrink: 0; width: 2rem; height: 2rem; border-radius: 10px;
      display: flex; align-items: center; justify-content: center;
      font-size: 1.1rem; font-weight: 800; color: #fff;
      background: var(--accent-dark);
    }
    .ns-case-1 .ns-case-badge { background: #0d9488; }
    .ns-case-2 .ns-case-badge { background: #2563eb; }
    .ns-case-3 .ns-case-badge { background: #d97706; }
    .ns-case-0 .ns-case-badge { background: #64748b; font-size: 0.85rem; }
    .ns-case-titles { display: flex; flex-direction: column; gap: 0.15rem; }
    .ns-case-title { font-weight: 800; font-size: 0.95rem; color: var(--text); }
    .ns-case-sub { font-size: 0.82rem; color: var(--muted); line-height: 1.35; }
    .ns-case-body { padding: 0.75rem 0.75rem 0.85rem; display: flex; flex-direction: column; gap: 0.85rem; }
    .ns-sku-card {
      background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
      padding: 0.65rem 0.75rem 0.75rem; box-shadow: 0 2px 8px rgba(15,23,42,.05);
    }
    .ns-sku-card-head { margin-bottom: 0.35rem; }
    .ns-sku-code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 0.92rem; font-weight: 700; color: #0f172a; word-break: break-all; }
    .ns-sku-name { margin: 0 0 0.5rem 0; font-size: 0.82rem; color: var(--muted); line-height: 1.4; }
    .ns-agg-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(9.5rem, 1fr));
      gap: 0.4rem 0.65rem; margin-bottom: 0.45rem;
    }
    .ns-metric {
      background: #f8fafc; border-radius: 8px; padding: 0.35rem 0.5rem;
      border: 1px solid #e2e8f0;
    }
    .ns-metric-k { display: block; font-size: 0.68rem; color: var(--muted);
      font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em; margin-bottom: 0.15rem; }
    .ns-metric-v { font-size: 0.9rem; font-weight: 700; font-variant-numeric: tabular-nums;
      color: var(--accent-dark); }
    .ns-wh-hint { margin: 0 0 0.35rem 0; font-size: 0.75rem; color: var(--muted); }
    .ns-table-wrap { overflow-x: auto; -webkit-overflow-scrolling: touch; border-radius: 8px; }
    .ns-table-wrap table.data { font-size: 12px; min-width: 620px; }
    .ns-table-wrap table.data th, .ns-table-wrap table.data td { padding: 6px 8px; }
    section.card.ns-account { padding-top: 1rem; }
    .ns-tabs-wrap { margin-bottom: 1.25rem; }
    .ns-tab-bar {
      display: flex; flex-wrap: wrap; gap: 0.4rem;
      padding: 0.5rem 0.65rem; margin-bottom: 0.75rem;
      background: var(--surface); border: 1px solid var(--border); border-radius: 12px;
      box-shadow: 0 1px 3px rgba(15,23,42,.06);
    }
    .ns-tab {
      cursor: pointer; border: 1px solid var(--border); background: #f8fafc;
      color: var(--text); padding: 0.45rem 0.95rem; border-radius: 999px;
      font-size: 0.85rem; font-weight: 700; transition: background .15s, color .15s, border-color .15s;
    }
    .ns-tab:hover { background: #e2e8f0; border-color: #94a3b8; }
    .ns-tab.active {
      background: var(--accent-dark); color: #fff; border-color: var(--accent-dark);
      box-shadow: 0 2px 8px rgba(13,148,136,.35);
    }
    .ns-tab-panel { display: none; }
    .ns-tab-panel.active { display: block; animation: ns-tab-in .18s ease-out; }
    @keyframes ns-tab-in { from { opacity: 0.55; } to { opacity: 1; } }
    .ns-tabs-single .ns-tab-panel.active { display: block; }
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
    <p class="sub">多账号用 Tab 切换；块内分①②③ → 每 SKU 聚合 + 分仓表</p>
  </header>
  <nav class="ns-legend" aria-label="分类说明">
    <div class="ns-legend-row">
      <span class="ns-leg"><i class="ns-dot c1"></i>① 7天=0，15/30≠0</span>
      <span class="ns-leg"><i class="ns-dot c2"></i>② 7/15=0，30≠0</span>
      <span class="ns-leg"><i class="ns-dot c3"></i>③ 7/15/30=0</span>
      <span class="ns-leg"><i class="ns-dot c0"></i>其它</span>
    </div>
  </nav>
  <div class="note-strip">
    <strong>规则与合计</strong> · {html_module.escape(rule)}<br/>
    <span class="muted">入库/快照：</span>{html_module.escape(str(th_meta.get("ingest_time_display") or ""))}<br/>
    <strong>{html_module.escape(cnt_line)}</strong>
  </div>
  <p class="muted">多账号时点击顶部 Tab 切换；单账号不显示 Tab。{note_e}</p>
  <p class="muted" style="margin-bottom:1rem">{stat_e}</p>
  {tables_html}
</div>
<script>
(function(){{
  var bar = document.querySelector(".ns-tab-bar");
  if (!bar) return;
  function activate(aid) {{
    var s = String(aid);
    bar.querySelectorAll(".ns-tab").forEach(function(btn) {{
      var on = btn.getAttribute("data-panel") === s;
      btn.classList.toggle("active", on);
      btn.setAttribute("aria-selected", on ? "true" : "false");
    }});
    document.querySelectorAll(".ns-tab-panel").forEach(function(p) {{
      p.classList.toggle("active", p.id === "ns-panel-" + s);
    }});
  }}
  bar.addEventListener("click", function(e) {{
    var t = e.target.closest(".ns-tab");
    if (!t) return;
    activate(t.getAttribute("data-panel"));
  }});
}})();
</script>
</body>
</html>"""
