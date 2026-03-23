"""
入库流水报表：从 inventory_inout_current（多账号合并）中筛选备注为
「标准入库-上架」「国内直发入库-上架」的行，按业务日期分块展示，块内按数量降序。

页面侧重：帮助核对「各账号在近期业务日上是否持续有上架流水」——含按账号汇总、
近 N 日 × 账号覆盖矩阵（可点日期锚点下钻明细）。

列名来自导出表头，可通过环境变量指定候选键（竖线分隔，按顺序优先匹配）：
  WINIT_INOUT_SHELF_REMARK_KEYS  默认 备注|事由备注
  WINIT_INOUT_SHELF_QTY_KEYS     默认 数量|入库数量|Qty
  WINIT_INOUT_SHELF_DATE_KEYS    默认 库存变动日期 北京时间|库存变动日期|…（见代码内完整列表）
  WINIT_INOUT_SHELF_MATRIX_DAYS  覆盖矩阵展示最近几个业务日（默认 14，范围 3～62）

明细表**固定 7 列**（顺序固定）：商品编码、数量、仓库、库存变动日期（北京时间）、期初库存、期末库存、单据号。
列名与导出表头不一致时，可用下列环境变量追加候选键（竖线分隔）：
  WINIT_INOUT_SHELF_SKU_KEYS / WH_KEYS / QTY_BEGIN_KEYS / QTY_END_KEYS / DOC_KEYS

数据依赖 run_inventory_inout_job.py 写入的 WINIT_INOUT_SQLITE_PATH（默认 artifacts/winit_inout.db）。
"""

from __future__ import annotations

import html
import json
import os
import re
import sqlite3
from datetime import datetime, timedelta
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, DefaultDict, Dict, List, Optional, Tuple

from winit_accounts import (
    account_display_for_row,
    account_id_display_map,
    list_winit_accounts,
)
from winit_inventory_inout_db import inout_sqlite_path
from winit_view_format import cell_int_str
from winit_view_theme import VIEWER_THEME_CSS

TARGET_REMARKS = frozenset({"标准入库-上架", "国内直发入库-上架"})

_DATE_RE = re.compile(r"(\d{4})[-.\/](\d{1,2})[-.\/](\d{1,2})")


def _pipe_keys(env_name: str, default: str) -> List[str]:
    raw = os.environ.get(env_name, "").strip()
    if not raw:
        raw = default
    return [x.strip() for x in raw.split("|") if x.strip()]


def _pick_first_key(d: dict, keys: List[str]) -> Tuple[Optional[str], Any]:
    """
    按候选列名取值；表头与 Excel 导出允许首尾空白不一致（用 strip 后的索引回查）。
    """
    if not d or not keys:
        return None, None
    strip_index = {str(k).strip(): k for k in d.keys()}
    for want in keys:
        if want in d and d[want] not in (None, ""):
            return want, d[want]
        w = want.strip()
        if w in strip_index:
            orig = strip_index[w]
            if d[orig] not in (None, ""):
                return str(orig), d[orig]
    return None, None


def _pick_date_value(d: dict, keys: List[str]) -> Tuple[Optional[str], Any]:
    """先按配置列名；再兜底匹配含「库存变动日期」的列（万邑通导出常见）。"""
    k, v = _pick_first_key(d, keys)
    if k is not None:
        return k, v
    for dk in d:
        label = str(dk).strip()
        if "库存变动日期" in label and d[dk] not in (None, ""):
            return str(dk), d[dk]
    return None, None


def _norm_remark(v: Any) -> str:
    if v is None:
        return ""
    return str(v).strip()


def _coerce_qty(v: Any) -> float:
    if v is None or v == "":
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "")
    try:
        return float(s)
    except ValueError:
        return 0.0


def _date_bucket(v: Any) -> str:
    if v is None or v == "":
        return "__未知__"
    if hasattr(v, "strftime"):
        try:
            return v.strftime("%Y-%m-%d")  # type: ignore[union-attr]
        except Exception:
            pass
    s = str(v).strip()
    m = _DATE_RE.search(s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    # Excel 序列日期（仅合理区间，避免把普通数量误当序列）
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        fv = float(v)
        if 35000 < fv < 65000:
            try:
                base = datetime(1899, 12, 30)
                dt = base + timedelta(days=fv)
                return dt.strftime("%Y-%m-%d")
            except (OverflowError, ValueError):
                pass
    return "__未知__"


def _sort_date_blocks(keys: List[str]) -> List[str]:
    known = [k for k in keys if k != "__未知__"]
    unk = [k for k in keys if k == "__未知__"]
    return sorted(known, reverse=True) + unk


def _uniq_strings(seq: List[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _detail_column_definitions(
    qty_keys: List[str],
    date_keys: List[str],
) -> List[Tuple[str, List[str]]]:
    """页面明细表：固定列标题顺序 + 每列候选导出表头（从左到右优先）。"""
    date_for_cell = _uniq_strings(
        list(date_keys)
        + [
            "库存变动日期（北京时间）",
            "库存变动日期(北京时间)",
        ]
    )
    return [
        (
            "商品编码",
            _pipe_keys("WINIT_INOUT_SHELF_SKU_KEYS", "商品编码|SKU|sku|产品编码"),
        ),
        ("数量", list(qty_keys)),
        (
            "仓库",
            _pipe_keys("WINIT_INOUT_SHELF_WH_KEYS", "仓库|仓库名称|所在仓库"),
        ),
        ("库存变动日期（北京时间）", date_for_cell),
        (
            "期初库存",
            _pipe_keys("WINIT_INOUT_SHELF_QTY_BEGIN_KEYS", "期初库存|期初数量"),
        ),
        (
            "期末库存",
            _pipe_keys("WINIT_INOUT_SHELF_QTY_END_KEYS", "期末库存|期末数量"),
        ),
        (
            "单据号",
            _pipe_keys("WINIT_INOUT_SHELF_DOC_KEYS", "单据号|单号|业务单号|流水号"),
        ),
    ]


def _matrix_day_cap() -> int:
    try:
        v = int(os.environ.get("WINIT_INOUT_SHELF_MATRIX_DAYS", "14"))
    except ValueError:
        v = 14
    return max(3, min(v, 62))


def _matrix_account_list(
    blocks: List[Tuple[str, List[InoutShelfRow]]],
) -> List[Tuple[int, str]]:
    """矩阵列：先 .env 已配置账号，再补上数据里出现但未配置的 id（便于对齐「应有账号」）。"""
    id_map = account_id_display_map()
    data_tags: dict[int, str] = {}
    data_ids: set[int] = set()
    for _, rows in blocks:
        for row in rows:
            data_ids.add(row.account_id)
            data_tags[row.account_id] = row.account_tag

    out_ids: List[int] = []
    tags: dict[int, str] = {}
    configured = list_winit_accounts()
    if configured:
        for a in configured:
            out_ids.append(a.id)
            tags[a.id] = account_display_for_row(a.id, a.username, id_map=id_map)
        for aid in sorted(data_ids):
            if aid not in tags:
                out_ids.append(aid)
                tags[aid] = data_tags[aid]
    else:
        for aid in sorted(data_ids):
            out_ids.append(aid)
            tags[aid] = data_tags[aid]
    return [(i, tags[i]) for i in out_ids]


@dataclass
class InoutShelfRow:
    account_id: int
    account_username: str
    account_tag: str
    qty: float
    remark: str
    date_bucket: str
    raw: dict


_DETAIL_NUM_LABELS = frozenset({"数量", "期初库存", "期末库存"})
_DETAIL_SKU_LABEL = "商品编码"


def _html_td_numeric(v: Any) -> str:
    if v is None or v == "":
        return "<td></td>"
    if isinstance(v, bool):
        return f"<td>{html.escape(str(v))}</td>"
    if isinstance(v, (int, float)):
        fv = float(v)
        if abs(fv - round(fv)) < 1e-9:
            return f'<td class="num inout-em">{html.escape(cell_int_str(int(round(fv))))}</td>'
        return f'<td class="num inout-em">{html.escape(f"{fv:g}")}</td>'
    return _html_td_plain(v)


def _html_td_plain(v: Any) -> str:
    if v is None or v == "":
        return "<td></td>"
    if isinstance(v, bool):
        return f"<td>{html.escape(str(v))}</td>"
    s = str(v).strip()
    return f"<td>{html.escape(s[:2000])}</td>"


def _html_td_sku(v: Any) -> str:
    if v is None or v == "":
        return '<td class="cell-sku"></td>'
    text = str(v).strip()
    if not text:
        return '<td class="cell-sku"></td>'
    attr = html.escape(text, quote=True)
    disp = html.escape(text)
    return (
        f'<td class="cell-sku">'
        f'<button type="button" class="sku-copy" data-sku="{attr}" '
        f'title="点击复制商品编码">{disp}</button>'
        f"</td>"
    )


def _html_tr_detail_row(
    row: InoutShelfRow,
    detail_def: List[Tuple[str, List[str]]],
) -> str:
    """单行明细 <tr>：列顺序由 detail_def 决定。"""
    tds: List[str] = []
    for label, keys in detail_def:
        _, v = _pick_first_key(row.raw, keys)
        if label == _DETAIL_SKU_LABEL:
            tds.append(_html_td_sku(v))
        elif label in _DETAIL_NUM_LABELS:
            tds.append(_html_td_numeric(v))
        else:
            tds.append(_html_td_plain(v))
    return "<tr>" + "".join(tds) + "</tr>"


def _collect_unique_texts(vals: List[Any]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for v in vals:
        if v is None:
            continue
        s = str(v).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _join_preview(items: List[str], *, max_items: int = 3, sep: str = "、") -> str:
    if not items:
        return ""
    if len(items) <= max_items:
        return sep.join(items)
    return sep.join(items[:max_items]) + f" 等{len(items)}项"


def _build_detail_key_map(detail_def: List[Tuple[str, List[str]]]) -> Dict[str, List[str]]:
    return {label: keys for label, keys in detail_def}


def _detail_val(row: InoutShelfRow, key_map: Dict[str, List[str]], label: str) -> Any:
    keys = key_map.get(label, [])
    _, v = _pick_first_key(row.raw, keys)
    return v


def _merge_rows_by_sku(
    rows: List[InoutShelfRow],
    detail_def: List[Tuple[str, List[str]]],
) -> List[InoutShelfRow]:
    """
    同账号同日期内按「商品编码」合并，数量求和，其它字段做可读聚合。
    """
    if not rows:
        return []
    key_map = _build_detail_key_map(detail_def)
    groups: DefaultDict[str, List[InoutShelfRow]] = defaultdict(list)
    # 无 SKU 的行保持原子，不与其它行误合并。
    anon_idx = 0
    for row in rows:
        sku_v = _detail_val(row, key_map, _DETAIL_SKU_LABEL)
        sku = str(sku_v).strip() if sku_v not in (None, "") else ""
        if not sku:
            anon_idx += 1
            sku = f"__NO_SKU__#{anon_idx}"
        groups[sku].append(row)

    merged: List[InoutShelfRow] = []
    for _, grp in groups.items():
        if len(grp) == 1:
            merged.append(grp[0])
            continue
        base = grp[0]
        qty_sum = sum(r.qty for r in grp)

        sku = str(_detail_val(base, key_map, _DETAIL_SKU_LABEL) or "").strip()
        whs = _collect_unique_texts([_detail_val(r, key_map, "仓库") for r in grp])
        docs = _collect_unique_texts([_detail_val(r, key_map, "单据号") for r in grp])
        dts = _collect_unique_texts([_detail_val(r, key_map, "库存变动日期（北京时间）") for r in grp])

        begin_vals = [_coerce_qty(_detail_val(r, key_map, "期初库存")) for r in grp]
        end_vals = [_coerce_qty(_detail_val(r, key_map, "期末库存")) for r in grp]
        begin_min = min(begin_vals) if begin_vals else 0.0
        end_max = max(end_vals) if end_vals else 0.0

        dt_merged = ""
        if dts:
            dt_sorted = sorted(dts)
            dt_merged = dt_sorted[0] if len(dt_sorted) == 1 else f"{dt_sorted[0]} ~ {dt_sorted[-1]}"

        raw_merged = {
            "商品编码": sku,
            "数量": qty_sum,
            "仓库": _join_preview(whs, max_items=3, sep=" / "),
            "库存变动日期（北京时间）": dt_merged,
            "期初库存": begin_min,
            "期末库存": end_max,
            "单据号": _join_preview(docs, max_items=3, sep="、"),
        }
        merged.append(
            InoutShelfRow(
                account_id=base.account_id,
                account_username=base.account_username,
                account_tag=base.account_tag,
                qty=qty_sum,
                remark=base.remark,
                date_bucket=base.date_bucket,
                raw=raw_merged,
            )
        )
    return merged


def collect_inout_shelf_rows(
    conn: sqlite3.Connection,
) -> Tuple[List[Tuple[str, List[InoutShelfRow]]], dict]:
    remark_keys = _pipe_keys("WINIT_INOUT_SHELF_REMARK_KEYS", "备注|事由备注")
    qty_keys = _pipe_keys("WINIT_INOUT_SHELF_QTY_KEYS", "数量|入库数量|Qty")
    date_keys = _pipe_keys(
        "WINIT_INOUT_SHELF_DATE_KEYS",
        "库存变动日期 北京时间|库存变动日期|变动日期|日期|业务日期|入仓日期|创建时间|单据日期|发生日期",
    )

    id_map = account_id_display_map()
    cur = conn.execute(
        """
        SELECT account_id, account_username, row_json
        FROM inventory_inout_current
        ORDER BY account_id, row_no
        """
    )

    by_date: DefaultDict[str, List[InoutShelfRow]] = defaultdict(list)

    for r in cur.fetchall():
        try:
            d = json.loads(r["row_json"])
        except json.JSONDecodeError:
            continue
        if not isinstance(d, dict):
            continue

        _, rv = _pick_first_key(d, remark_keys)
        remark = _norm_remark(rv)
        if remark not in TARGET_REMARKS:
            continue

        _, qv = _pick_first_key(d, qty_keys)
        qty = _coerce_qty(qv)

        _, dv = _pick_date_value(d, date_keys)
        bucket = _date_bucket(dv)

        aid = int(r["account_id"])
        uname = str(r["account_username"] or "")
        tag = account_display_for_row(aid, uname, id_map=id_map)

        by_date[bucket].append(
            InoutShelfRow(
                account_id=aid,
                account_username=uname,
                account_tag=tag,
                qty=qty,
                remark=remark,
                date_bucket=bucket,
                raw=d,
            )
        )

    ordered_dates = _sort_date_blocks(list(by_date.keys()))
    blocks = [(dt, by_date[dt]) for dt in ordered_dates]

    detail_defs = _detail_column_definitions(qty_keys, date_keys)
    detail_spec: List[List[Any]] = [[lbl, keys] for lbl, keys in detail_defs]
    column_order = [lbl for lbl, _ in detail_defs]

    total = sum(len(rows) for _, rows in blocks)
    matrix_dates = [d for d in ordered_dates if d != "__未知__"][: _matrix_day_cap()]
    matrix_accounts = _matrix_account_list(blocks)

    cell_n: DefaultDict[Tuple[str, int], int] = defaultdict(int)
    cell_qty: DefaultDict[Tuple[str, int], float] = defaultdict(float)
    for date_label, rows in blocks:
        if date_label == "__未知__":
            continue
        for row in rows:
            kc = (date_label, row.account_id)
            cell_n[kc] += 1
            cell_qty[kc] += row.qty

    by_aid: dict[int, dict] = {}
    for aid, _t in matrix_accounts:
        by_aid[aid] = {"rows": 0, "qty": 0.0, "dates": set()}
    for date_label, rows in blocks:
        for row in rows:
            bid = row.account_id
            if bid not in by_aid:
                by_aid[bid] = {"rows": 0, "qty": 0.0, "dates": set()}
            rec = by_aid[bid]
            rec["rows"] += 1
            rec["qty"] += row.qty
            if date_label != "__未知__":
                rec["dates"].add(date_label)

    n_win = len(matrix_dates)
    account_summaries: List[dict] = []
    for aid, tag in matrix_accounts:
        rec = by_aid.get(aid, {"rows": 0, "qty": 0.0, "dates": set()})
        dlist = sorted(rec["dates"], reverse=True)
        last_d = dlist[0] if dlist else ""
        hits = sum(1 for d in matrix_dates if cell_n[(d, aid)] > 0)
        account_summaries.append(
            {
                "account_id": aid,
                "tag": tag,
                "rows": rec["rows"],
                "qty": rec["qty"],
                "distinct_dates": len(rec["dates"]),
                "last_date": last_d,
                "window_hits": hits,
                "window_total": n_win,
            }
        )

    gap_dates: List[str] = []
    if matrix_dates and matrix_accounts:
        for d in matrix_dates:
            if any(cell_n[(d, aid)] == 0 for aid, _ in matrix_accounts):
                gap_dates.append(d)
    gap_dates = sorted(set(gap_dates), reverse=True)

    n_unknown = len(by_date.get("__未知__", []))

    meta = {
        "total": total,
        "db_path": str(inout_sqlite_path()),
        "column_order": column_order,
        "detail_spec": detail_spec,
        "remark_keys": remark_keys,
        "qty_keys": qty_keys,
        "date_keys": date_keys,
        "matrix_dates": matrix_dates,
        "matrix_accounts": matrix_accounts,
        "cell_n": dict(cell_n),
        "cell_qty": dict(cell_qty),
        "account_summaries": account_summaries,
        "gap_dates": gap_dates,
        "n_distinct_dates": len([d for d in ordered_dates if d != "__未知__"]),
        "n_accounts": len(matrix_accounts),
        "n_unknown_rows": n_unknown,
        "matrix_day_cap": _matrix_day_cap(),
    }
    return blocks, meta


def build_inout_shelf_detail_url() -> str:
    base = os.environ.get("WINIT_PUBLIC_BASE_URL", "").strip().rstrip("/")
    if not base:
        return ""
    return f"{base}/report/inout-shelf"


def _fmt_qty_compact(q: float) -> str:
    if abs(q - round(q)) < 1e-9:
        return cell_int_str(int(round(q)))
    return f"{q:g}"


def format_inout_shelf_feishu_text(
    blocks: List[Tuple[str, List[InoutShelfRow]]],
    url: str,
    *,
    meta: Optional[Dict[str, Any]] = None,
) -> str:
    total = sum(len(r) for _, r in blocks)
    lines: List[str] = [
        "📌 各账号上架流水核对（备注：标准入库-上架 / 国内直发入库-上架）",
        f"共 {total} 条明细；网页按业务日分块，块内按数量降序。",
    ]
    if meta:
        lines.append(
            f"账号 {meta.get('n_accounts', 0)} 个 · 业务日（去重）{meta.get('n_distinct_dates', 0)} 天"
            f" · 矩阵展示最近 {meta.get('matrix_day_cap', 0)} 个业务日。"
        )
        gaps: List[str] = list(meta.get("gap_dates") or [])
        if gaps:
            head = "、".join(gaps[:6])
            more = f" 等共{len(gaps)}天" if len(gaps) > 6 else ""
            lines.append(f"⚠️ 以下业务日存在某账号无上架条数：{head}{more}")
        nu = int(meta.get("n_unknown_rows") or 0)
        if nu:
            lines.append(f"另有 {nu} 条日期字段未解析（网页「日期未知」块）。")
    lines.append("")
    for date_label, rows in blocks:
        if date_label == "__未知__":
            title = "日期未知"
        else:
            title = date_label
        qty_sum = sum(x.qty for x in rows)
        q_s = _fmt_qty_compact(qty_sum)
        lines.append(f"【{title}】{len(rows)} 行 · 数量合计 {q_s}")

    if url:
        lines.extend(["", f"明细页：{url}"])
    return "\n".join(lines)


def render_inout_shelf_report_html(
    blocks: List[Tuple[str, List[InoutShelfRow]]],
    meta: dict,
    *,
    query_note: str = "",
) -> str:
    note_e = html.escape(query_note) if query_note else ""
    db_e = html.escape(str(meta.get("db_path", "")))
    total = int(meta.get("total", 0))
    raw_spec = meta.get("detail_spec") or []
    detail_def: List[Tuple[str, List[str]]] = [
        (str(row[0]), list(row[1])) for row in raw_spec
    ]
    cap = int(meta.get("matrix_day_cap") or _matrix_day_cap())
    n_acct = int(meta.get("n_accounts") or 0)
    n_ddays = int(meta.get("n_distinct_dates") or 0)
    n_unk = int(meta.get("n_unknown_rows") or 0)
    gap_dates: List[str] = list(meta.get("gap_dates") or [])
    matrix_dates: List[str] = list(meta.get("matrix_dates") or [])
    matrix_accounts: List[Tuple[int, str]] = list(meta.get("matrix_accounts") or [])
    cell_n: Dict[Tuple[str, int], int] = dict(meta.get("cell_n") or {})
    cell_qty: Dict[Tuple[str, int], float] = dict(meta.get("cell_qty") or {})
    summaries: List[dict] = list(meta.get("account_summaries") or [])

    pills = (
        f'<div class="stat-pills inout-meta-line" role="list">'
        f'<span class="stat-pill" role="listitem">{html.escape(str(n_acct))} 个账号</span>'
        f'<span class="stat-pill" role="listitem">{html.escape(str(n_ddays))} 个业务日</span>'
        f'<span class="stat-pill" role="listitem">{html.escape(cell_int_str(total))} 条明细</span>'
        f"</div>"
    )

    notes_html = ""
    if gap_dates and total > 0:
        sample = "、".join(html.escape(d) for d in gap_dates[:5])
        more = f"… 等 {len(gap_dates)} 天" if len(gap_dates) > 5 else ""
        notes_html += (
            f'<div class="note-strip" role="status">'
            f"<strong>覆盖提示：</strong>下列业务日在「最近 {cap} 个业务日」矩阵中，"
            f"至少有一个账号无上架条数（可能当日确实无单，或导出/日期列未对齐）："
            f"{sample}{more}"
            f"</div>"
        )
    if n_unk and total > 0:
        notes_html += (
            f'<div class="note-strip" role="status" style="margin-top:0.5rem">'
            f"<strong>日期未解析：</strong>有 {html.escape(cell_int_str(n_unk))} 条无法归入具体业务日，"
            f"已集中在文末「日期未知」块，请检查导出表日期列或 <code>WINIT_INOUT_SHELF_DATE_KEYS</code>。"
            f"</div>"
        )

    summary_card = ""
    if total > 0 and summaries:
        sum_rows = ""
        for s in summaries:
            tag_e = html.escape(str(s.get("tag", "")))
            rows_n = int(s.get("rows", 0))
            qty_v = float(s.get("qty", 0.0))
            dd = int(s.get("distinct_dates", 0))
            last_d = str(s.get("last_date") or "")
            last_e = html.escape(last_d) if last_d else "—"
            wh = int(s.get("window_hits", 0))
            wt = int(s.get("window_total", 0))
            if wt <= 0:
                cov = "—"
                cov_cls = "ok"
            elif wh >= wt:
                cov = f"{wh}/{wt}"
                cov_cls = "ok"
            else:
                cov = f"{wh}/{wt}"
                cov_cls = "warn"
            sum_rows += (
                f"<tr>"
                f"<td>{tag_e}</td>"
                f'<td class="num">{html.escape(cell_int_str(rows_n))}</td>'
                f'<td class="num">{html.escape(_fmt_qty_compact(qty_v))}</td>'
                f'<td class="num">{html.escape(cell_int_str(dd))}</td>'
                f"<td>{last_e}</td>"
                f'<td class="num"><span class="{cov_cls}">{html.escape(cov)}</span></td>'
                f"</tr>"
            )
        summary_card = f"""
<section class="card" aria-labelledby="ios-sum-h">
  <h2 id="ios-sum-h">按账号速览</h2>
  <p class="muted" style="margin-top:0;margin-bottom:0.75rem">
    快速确认各账号在两类「上架」备注下的总量与最近业务日。
    「近{cap}天覆盖」指下方矩阵所展示的最新 {cap} 个业务日中，该账号有上架条数的天数（满分 {cap} 表示这些天每天都有单）。
  </p>
  <div style="overflow-x:auto">
  <table class="data summary-by-acct">
    <thead>
      <tr>
        <th scope="col">账号</th>
        <th scope="col" class="num">匹配条数</th>
        <th scope="col" class="num">数量合计</th>
        <th scope="col" class="num">涉及业务日</th>
        <th scope="col">最近业务日</th>
        <th scope="col" class="num">近{cap}天覆盖</th>
      </tr>
    </thead>
    <tbody>{sum_rows}</tbody>
  </table>
  </div>
</section>
"""

    matrix_card = ""
    if total > 0 and matrix_dates and matrix_accounts:
        th_acct = "".join(
            f'<th scope="col" class="cov-acct-h">{html.escape(tag)}</th>'
            for _aid, tag in matrix_accounts
        )
        body_m = ""
        cn = cell_n
        cq = cell_qty
        for d in matrix_dates:
            href = html.escape(f"#d-{d}")
            row_h = (
                f'<th scope="row" class="cov-date-h">'
                f'<a class="cov-date-link" href="{href}">{html.escape(d)}</a>'
                f"</th>"
            )
            cells = ""
            for aid, _tag in matrix_accounts:
                n = int(cn.get((d, aid), 0))
                qv = float(cq.get((d, aid), 0.0))
                if n > 0:
                    title_attr = html.escape(
                        f"数量合计 {_fmt_qty_compact(qv)}", quote=True
                    )
                    cells += (
                        f'<td class="cov-cell cov-yes">'
                        f'<a class="cov-link" href="{href}" title="{title_attr}">'
                        f"{html.escape(cell_int_str(n))}<span class=\"cov-sub\"> 行</span>"
                        f"</a></td>"
                    )
                else:
                    cells += '<td class="cov-cell cov-no"><span>—</span></td>'
            body_m += f"<tr>{row_h}{cells}</tr>"
        matrix_card = f"""
<section class="card" aria-labelledby="ios-mx-h">
  <h2 id="ios-mx-h">近 {cap} 个业务日 × 账号</h2>
  <p class="muted" style="margin-top:0;margin-bottom:0.75rem">
    行数 = 当日该账号在两类上架备注下的条数；点击日期或数字可跳到下方对应日期；该日期下再按账号分表，表内按数量降序。
    矩阵仅含能解析出业务日的记录；天数上限可用环境变量 <code>WINIT_INOUT_SHELF_MATRIX_DAYS</code> 调整。
  </p>
  <div style="overflow-x:auto">
  <table class="data cov-matrix">
    <thead>
      <tr>
        <th scope="col" class="corner">业务日 \\ 账号</th>
        {th_acct}
      </tr>
    </thead>
    <tbody>{body_m}</tbody>
  </table>
  </div>
</section>
"""

    matrix_acct_ids: List[int] = [a for a, _ in matrix_accounts]
    th_parts: List[str] = []
    for lbl, _keys in detail_def:
        th_cls = "inout-th"
        if lbl in _DETAIL_NUM_LABELS:
            th_cls += " inout-th-num"
        th_parts.append(f'<th scope="col" class="{th_cls}">{html.escape(lbl)}</th>')
    thead_detail = "".join(th_parts)

    sections = ""
    for date_label, rows in blocks:
        if date_label == "__未知__":
            h = "日期未知"
            sec_id = "d-unknown"
        else:
            h = html.escape(date_label)
            sec_id = f"d-{html.escape(date_label)}"
        qty_sum = sum(x.qty for x in rows)
        if abs(qty_sum - round(qty_sum)) < 1e-9:
            sum_s = cell_int_str(int(round(qty_sum)))
        else:
            sum_s = html.escape(f"{qty_sum:g}")

        by_acct: DefaultDict[int, List[InoutShelfRow]] = defaultdict(list)
        for row in rows:
            by_acct[row.account_id].append(row)

        acct_order: List[int] = []
        seen_id: set[int] = set()
        for aid in matrix_acct_ids:
            if aid in by_acct:
                acct_order.append(aid)
                seen_id.add(aid)
        for aid in sorted(by_acct.keys()):
            if aid not in seen_id:
                acct_order.append(aid)

        sub_chunks = ""
        for aid in acct_order:
            acct_rows = by_acct[aid]
            merged_rows = _merge_rows_by_sku(acct_rows, detail_def)
            merged_rows.sort(key=lambda x: (-x.qty, x.remark, x.account_username))
            tag_e = html.escape(acct_rows[0].account_tag)
            sub_qty = sum(x.qty for x in acct_rows)
            if abs(sub_qty - round(sub_qty)) < 1e-9:
                sub_sum_s = cell_int_str(int(round(sub_qty)))
            else:
                sub_sum_s = html.escape(f"{sub_qty:g}")
            tbody_body = "".join(_html_tr_detail_row(r, detail_def) for r in merged_rows)
            merge_note = (
                f"（合并后 {len(merged_rows)} 行 / 原 {len(acct_rows)} 条）"
                if len(merged_rows) != len(acct_rows)
                else ""
            )
            sub_chunks += f"""
  <div class="ios-acct-block">
    <h3 class="ios-acct-head">{tag_e}
      <span class="ios-acct-meta"> {len(merged_rows)} 行 {html.escape(merge_note)} · 数量合计 <span class="num inout-em">{sub_sum_s}</span></span>
    </h3>
    <div style="overflow-x:auto">
    <table class="data inout-detail">
      <thead><tr>{thead_detail}</tr></thead>
      <tbody>{tbody_body}</tbody>
    </table>
    </div>
  </div>
"""

        sub_line = (
            "其下按账号分表；每账号内按数量从高到低"
            if date_label != "__未知__"
            else "无法从导出表解析业务日的记录，请检查日期列；仍按账号分表"
        )
        n_acct_here = len(acct_order)
        sections += f"""
<section class="card ios-block" id="{sec_id}">
  <div class="ios-block-head">
    <h2>{h}</h2>
    <p class="sub">{html.escape(sub_line)} · {n_acct_here} 个账号 · {len(rows)} 行 · 数量合计 <span class="num">{sum_s}</span></p>
  </div>
{sub_chunks}
</section>
"""

    empty = ""
    if not blocks or total == 0:
        empty = (
            '<section class="card"><p><strong>暂无匹配数据。</strong> '
            "请确认已执行 <code>run_inventory_inout_job.py</code> 且导出表中「备注」列含上述两类文案；"
            "列名不一致时可设置 <code>WINIT_INOUT_SHELF_*_KEYS</code>。</p></section>"
        )

    detail_title = (
        '<h2 class="section-title" id="ios-detail">按业务日期 · 明细（日内按账号；表内仅 7 列）</h2>'
        if total > 0
        else ""
    )

    extra_css = """
    .ios-block { margin-bottom: 1.25rem; }
    .ios-block-head {
      display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between;
      gap: 0.5rem 1rem; margin-bottom: 0.65rem; padding-bottom: 0.5rem;
      border-bottom: 2px solid var(--accent-soft);
    }
    .ios-block-head h2 { margin: 0; font-size: 1.1rem; color: var(--accent-dark); }
    .ios-block-head .sub { margin: 0; font-size: 0.85rem; color: var(--muted); }
    .ios-acct-block {
      margin-bottom: 1.1rem;
      padding-bottom: 0.85rem;
      border-bottom: 1px solid var(--border);
    }
    .ios-acct-block:last-child {
      margin-bottom: 0;
      padding-bottom: 0;
      border-bottom: none;
    }
    .ios-acct-head {
      margin: 0 0 0.55rem 0;
      font-size: 1rem;
      font-weight: 800;
      color: var(--accent-dark);
      display: flex;
      flex-wrap: wrap;
      align-items: baseline;
      gap: 0.35rem 0.75rem;
    }
    .ios-acct-meta {
      font-size: 0.82rem;
      font-weight: 600;
      color: var(--muted);
    }
    .banner .stat-pills { margin-top: 0.75rem; }
    .inout-meta-line .stat-pill { background: #f1f5f9; color: #334155; border: 1px solid var(--border); }
    .inout-em { color: #0f172a !important; font-weight: 800 !important; }
    .summary-by-acct .ok { color: var(--accent-dark); font-weight: 700; }
    .summary-by-acct .warn { color: #b45309; font-weight: 700; }
    .inout-detail { font-size: 13px; }
    .inout-detail thead th.inout-th { font-weight: 700; color: #334155; background: #f8fafc; }
    .inout-detail thead th.inout-th-num { text-align: right; }
    .inout-detail tbody td { vertical-align: middle; }
    .inout-detail .cell-sku { white-space: nowrap; max-width: 14rem; }
    .sku-copy {
      all: unset; cursor: pointer;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 12.5px; font-weight: 700; color: #0f172a;
      border-bottom: 1px dashed #94a3b8; padding: 0.1rem 0;
      display: inline-block; max-width: 100%; overflow: hidden;
      text-overflow: ellipsis; white-space: nowrap; vertical-align: bottom;
    }
    .sku-copy:hover { border-bottom-style: solid; border-bottom-color: var(--accent-dark); color: var(--accent-dark); }
    .sku-copy.is-copied { border-bottom-color: var(--accent); color: var(--accent-dark); }
    .cov-matrix { font-size: 12px; }
    .cov-matrix th.corner, .cov-matrix th.cov-date-h { white-space: nowrap; }
    .cov-matrix th.cov-acct-h {
      max-width: 7.5rem; font-weight: 600; font-size: 11px;
      line-height: 1.25; vertical-align: bottom; color: #475569;
    }
    .cov-matrix td.cov-cell { text-align: center; vertical-align: middle; padding: 6px 5px; }
    .cov-matrix td.cov-yes { background: #fff; box-shadow: inset 0 0 0 1px #e2e8f0; }
    .cov-matrix td.cov-no { background: #fafafa; color: #94a3b8; }
    .cov-matrix .cov-link {
      font-weight: 800; color: #0f172a; text-decoration: none;
      font-variant-numeric: tabular-nums;
    }
    .cov-matrix .cov-link:hover { text-decoration: underline; color: var(--accent-dark); }
    .cov-matrix .cov-sub { font-weight: 600; font-size: 11px; color: var(--muted); }
    .cov-matrix th.cov-date-h { background: #1e293b; color: #f8fafc; font-weight: 700; }
    .cov-matrix .cov-date-link { font-weight: 700; color: #f8fafc; text-decoration: none; }
    .cov-matrix .cov-date-link:hover { text-decoration: underline; }
    """

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>各账号上架流水核对</title>
  <style>{VIEWER_THEME_CSS}
  {extra_css}
  </style>
</head>
<body>
<div class="page">
  <div class="toolbar" style="margin-bottom:0.75rem">
    <a href="/">← 返回汇总</a>
  </div>
  <header class="banner" style="padding:1rem 1.25rem">
    <h1 style="font-size:1.35rem">各账号上架流水核对</h1>
    <p class="sub">对照万邑通导出中备注为「标准入库-上架」「国内直发入库-上架」的流水，看清<strong>每个账号</strong>在近期业务日上是否<strong>持续有上架记录</strong>；可先扫汇总与矩阵，再点日期下钻：<strong>该日 → 按账号分表 → 表内按数量从高到低</strong>。明细表<strong>仅保留 7 列</strong>（商品编码、数量、仓库、库存变动日期（北京时间）、期初/期末库存、单据号）；<strong>商品编码可点击复制</strong>。</p>
    {pills}
    {f'<p class="muted" style="margin:0.75rem 0 0 0;opacity:0.9">{note_e}</p>' if note_e else ""}
    <p class="muted" style="margin:0.75rem 0 0 0;opacity:0.88;font-size:0.82rem">
      数据来自独立库 <code>{db_e}</code>（与库存快照库分离）。入库任务：<code>run_inventory_inout_job.py</code>
    </p>
  </header>
  {notes_html if total else ""}
  {summary_card}
  {matrix_card}
  {detail_title}
  {sections if total else empty}
</div>
<script>
(function () {{
  function copyText(t) {{
    if (navigator.clipboard && navigator.clipboard.writeText) {{
      return navigator.clipboard.writeText(t);
    }}
    return new Promise(function (resolve, reject) {{
      try {{
        var ta = document.createElement('textarea');
        ta.value = t;
        ta.style.position = 'fixed';
        ta.style.left = '-9999px';
        document.body.appendChild(ta);
        ta.focus();
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
        resolve();
      }} catch (e) {{ reject(e); }}
    }});
  }}
  document.querySelectorAll('.sku-copy').forEach(function (btn) {{
    btn.addEventListener('click', function () {{
      var s = btn.getAttribute('data-sku') || '';
      if (!s) return;
      var old = btn.textContent;
      copyText(s).then(function () {{
        btn.classList.add('is-copied');
        btn.textContent = '已复制';
        setTimeout(function () {{
          btn.classList.remove('is-copied');
          btn.textContent = old;
        }}, 1100);
      }}).catch(function () {{}});
    }});
  }});
}})();
</script>
</body>
</html>"""
