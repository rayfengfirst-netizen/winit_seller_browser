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
  WINIT_INOUT_SHELF_HIDDEN_COLUMNS  不在明细表展示的列（竖线分隔）
      默认 商品登记|类型|规格|英文名称

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


def _hidden_column_strips() -> set[str]:
    raw = _pipe_keys(
        "WINIT_INOUT_SHELF_HIDDEN_COLUMNS",
        "商品登记|类型|规格|英文名称",
    )
    return {h.strip() for h in raw if h.strip()}


def _column_visible(name: str, hidden: set[str]) -> bool:
    return str(name).strip() not in hidden


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


def _html_tr_for_inout_row(row: InoutShelfRow, detail_cols: List[str]) -> str:
    """单行 <tr>，不含账号列（由分组标题展示）。"""
    tds: List[str] = []
    for c in detail_cols:
        v = row.raw.get(c)
        if v is None or v == "":
            tds.append("<td></td>")
        elif isinstance(v, bool):
            tds.append(f"<td>{html.escape(str(v))}</td>")
        elif isinstance(v, (int, float)):
            fv = float(v)
            if abs(fv - round(fv)) < 1e-9:
                tds.append(f'<td class="num">{html.escape(cell_int_str(int(round(fv))))}</td>')
            else:
                tds.append(f'<td class="num">{html.escape(f"{fv:g}")}</td>')
        else:
            tds.append(f"<td>{html.escape(str(v)[:2000])}</td>")
    return "<tr>" + "".join(tds) + "</tr>"


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
    all_keys: set[str] = set()

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
        all_keys.update(str(k) for k in d.keys())

    ordered_dates = _sort_date_blocks(list(by_date.keys()))
    blocks = [(dt, by_date[dt]) for dt in ordered_dates]

    hidden_cols = _hidden_column_strips()
    column_order: List[str] = ["账号"]
    for cand in qty_keys:
        if cand in all_keys and cand not in column_order:
            column_order.append(cand)
            break
    for cand in remark_keys:
        if cand in all_keys and cand not in column_order:
            column_order.append(cand)
            break
    for cand in date_keys:
        if cand in all_keys and cand not in column_order:
            column_order.append(cand)
            break
    for k in sorted(all_keys):
        if k not in column_order:
            column_order.append(k)
    column_order = [c for c in column_order if _column_visible(c, hidden_cols)]

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
    cols: List[str] = list(meta.get("column_order") or ["账号"])
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
        f'<div class="stat-pills" role="list">'
        f'<span class="stat-pill" role="listitem">{html.escape(str(n_acct))} 个账号</span>'
        f'<span class="stat-pill blue" role="listitem">{html.escape(str(n_ddays))} 个业务日</span>'
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
    detail_cols = [c for c in cols if c != "账号"]
    thead_detail = "".join(f"<th>{html.escape(c)}</th>" for c in detail_cols)

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
            acct_rows.sort(key=lambda x: (-x.qty, x.remark, x.account_username))
            tag_e = html.escape(acct_rows[0].account_tag)
            sub_qty = sum(x.qty for x in acct_rows)
            if abs(sub_qty - round(sub_qty)) < 1e-9:
                sub_sum_s = cell_int_str(int(round(sub_qty)))
            else:
                sub_sum_s = html.escape(f"{sub_qty:g}")
            if not detail_cols:
                tbody_body = (
                    "<tr><td colspan=\"1\">无列可展示（可在 .env 调整 "
                    "<code>WINIT_INOUT_SHELF_HIDDEN_COLUMNS</code>）</td></tr>"
                )
            else:
                tbody_body = "".join(
                    _html_tr_for_inout_row(r, detail_cols) for r in acct_rows
                )
            sub_chunks += f"""
  <div class="ios-acct-block">
    <h3 class="ios-acct-head">{tag_e}
      <span class="ios-acct-meta"> {len(acct_rows)} 行 · 数量合计 <span class="num">{sub_sum_s}</span></span>
    </h3>
    <div style="overflow-x:auto">
    <table class="data">
      <thead><tr>{thead_detail or "<th>（无列）</th>"}</tr></thead>
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
        '<h2 class="section-title" id="ios-detail">按业务日期 · 明细（日内按账号分表）</h2>'
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
    .summary-by-acct .ok { color: var(--accent-dark); font-weight: 700; }
    .summary-by-acct .warn { color: #b45309; font-weight: 700; }
    .cov-matrix { font-size: 12px; }
    .cov-matrix th.corner, .cov-matrix th.cov-date-h { white-space: nowrap; }
    .cov-matrix th.cov-acct-h {
      max-width: 7.5rem; font-weight: 600; font-size: 11px;
      line-height: 1.25; vertical-align: bottom;
    }
    .cov-matrix td.cov-cell { text-align: center; vertical-align: middle; padding: 6px 5px; }
    .cov-matrix td.cov-yes { background: #ecfdf5; }
    .cov-matrix td.cov-no { background: #f8fafc; color: var(--muted); }
    .cov-matrix .cov-link {
      font-weight: 700; color: var(--accent-dark); text-decoration: none;
      font-variant-numeric: tabular-nums;
    }
    .cov-matrix .cov-link:hover { text-decoration: underline; color: #115e59; }
    .cov-matrix .cov-sub { font-weight: 600; font-size: 11px; color: #0f766e; }
    .cov-matrix .cov-date-link { font-weight: 700; color: #fff; text-decoration: none; }
    .cov-matrix .cov-date-link:hover { text-decoration: underline; }
    .cov-matrix th.cov-date-h { background: #0f766e; color: #fff; }
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
    <p class="sub">对照万邑通导出中备注为「标准入库-上架」「国内直发入库-上架」的流水，看清<strong>每个账号</strong>在近期业务日上是否<strong>持续有上架记录</strong>；可先扫汇总与矩阵，再点日期下钻：<strong>该日 → 按账号分表 → 表内按数量从高到低</strong>。</p>
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
</body>
</html>"""
