"""
只读 Web 页：浏览 SQLite 中的库存快照（inventory_daily）与同步记录（sync_runs）。

默认仅监听本机 127.0.0.1，请勿在未加认证的情况下对公网暴露。

运行：
  cd winit_seller_browser && source .venv/bin/activate
  pip install -r requirements.txt
  python inventory_viewer.py

环境变量：
  WINIT_SQLITE_PATH   同 run_daily_winit_job
  WINIT_VIEWER_HOST   默认 127.0.0.1（需公网访问时可设 0.0.0.0，务必配下面账号密码）
  WINIT_VIEWER_PORT   默认 8765（服务器上常用 8765 作为库存首页；飞书详情链接请设 WINIT_PUBLIC_BASE_URL 同端口）
  WINIT_VIEWER_USER / WINIT_VIEWER_PASSWORD  若均非空，则整站 HTTP Basic 认证

报表：
  /report/no-sales  无动销预警（SKU 聚合后分①②③；表内为各仓明细，见 winit_no_sales_report.py）
"""

from __future__ import annotations

import hmac
import html
import json
import os
import sqlite3
from pathlib import Path

from collections import defaultdict

from dotenv import load_dotenv
from flask import Flask, abort, request, Response

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

from winit_accounts import account_display_for_row, account_id_display_map  # noqa: E402
from winit_inventory_db import connect, init_schema, sqlite_path  # noqa: E402
from winit_view_format import cell_int_str  # noqa: E402
from winit_view_theme import VIEWER_THEME_CSS  # noqa: E402
from winit_no_sales_report import (  # noqa: E402
    collect_no_sales_rows,
    render_no_sales_report_html,
)

app = Flask(__name__)

_VIEWER_USER = os.environ.get("WINIT_VIEWER_USER", "").strip()
_VIEWER_PASSWORD = os.environ.get("WINIT_VIEWER_PASSWORD", "")

PAGE_SIZE = 80


@app.before_request
def _require_basic_auth_if_configured() -> Response | None:
    if not _VIEWER_USER:
        return None
    auth = request.authorization
    ok_user = auth is not None and auth.username == _VIEWER_USER
    ok_pass = auth is not None and hmac.compare_digest(
        auth.password or "",
        _VIEWER_PASSWORD or "",
    )
    if ok_user and ok_pass:
        return None
    return Response(
        "需要登录（HTTP Basic）",
        401,
        {"WWW-Authenticate": 'Basic realm="Winit inventory"'},
        mimetype="text/plain; charset=utf-8",
    )


def _conn() -> sqlite3.Connection:
    conn = connect()
    init_schema(conn)
    return conn


@app.route("/")
def index() -> str:
    db = html.escape(str(sqlite_path()))
    id_map = account_id_display_map()
    with _conn() as conn:
        cur = conn.execute(
            """
            SELECT snapshot_date, account_id,
                   MAX(account_username) AS account_username,
                   COUNT(*) AS n
            FROM inventory_daily
            GROUP BY snapshot_date, account_id
            ORDER BY snapshot_date DESC, account_id
            LIMIT 200
            """
        )
        blocks = cur.fetchall()

    by_acct: defaultdict[int, list] = defaultdict(list)
    for snapshot_date, account_id, username, n in blocks:
        by_acct[int(account_id)].append((snapshot_date, account_id, username, n))

    sections_html = ""
    for aid in sorted(by_acct.keys()):
        acct_rows = by_acct[aid]
        acct_rows.sort(key=lambda r: r[0], reverse=True)
        uname = (acct_rows[0][2] or "") if acct_rows else ""
        tag = html.escape(account_display_for_row(aid, uname, id_map=id_map))
        inner = ""
        for snapshot_date, account_id, username, n in acct_rows:
            u = html.escape(username or "")
            inner += (
                f"<tr><td>{html.escape(snapshot_date)}</td>"
                f"<td>{account_id}</td><td>{u}</td>"
                f"<td class=\"num\">{cell_int_str(n)}</td>"
                f"<td><a href=\"/table?snapshot_date={html.escape(snapshot_date, quote=True)}"
                f"&account_id={account_id}\">浏览</a></td></tr>"
            )
        sections_html += (
            f"<section class=\"card acct-home\">"
            f"<h2>账号 {tag}</h2>"
            "<table class=\"data\">"
            "<thead><tr><th>快照日期</th><th>账号 ID</th><th>登录名</th>"
            "<th class=\"num\">行数</th><th></th></tr></thead>"
            f"<tbody>{inner}</tbody></table></section>"
        )

    empty_msg = (
        "<section class=\"card acct-home\"><p><strong>暂无快照数据。</strong><br/>"
        "在本机项目目录执行：<code>python run_daily_winit_job.py</code>（有 zip 可先 "
        "<code>WINIT_SKIP_DOWNLOAD=1 python run_daily_winit_job.py</code>）。<br/>"
        "若曾把数据写入其它库，请在 <code>.env</code> 里设置 <code>WINIT_SQLITE_PATH</code> "
        "与这里一致后重启本页服务。</p></section>"
    )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Winit 库存快照</title>
  <style>{VIEWER_THEME_CSS}</style>
</head>
<body>
<div class="page">
  <header class="banner">
    <h1>Winit 库存快照</h1>
    <p class="sub">按账号分块；库存明细中数量以整数展示，并按可用库存从高到低排序</p>
  </header>
  <div class="toolbar">
    <a href="/report/no-sales" class="primary">无动销预警</a>
    <a href="/runs">同步运行记录</a>
  </div>
  <p class="muted">数据库文件 <code>{db}</code></p>
  <h2 class="section-title">按账号 · 快照汇总</h2>
  {sections_html if sections_html else empty_msg}
</div>
</body>
</html>"""


@app.route("/table")
def table() -> str:
    snapshot_date = request.args.get("snapshot_date", "").strip()
    account_id_s = request.args.get("account_id", "").strip()
    page = max(1, int(request.args.get("page", "1") or "1"))
    if not snapshot_date or not account_id_s:
        abort(400, "需要参数 snapshot_date、account_id")
    try:
        account_id = int(account_id_s)
    except ValueError:
        abort(400, "account_id 无效")

    offset = (page - 1) * PAGE_SIZE
    id_map = account_id_display_map()
    with _conn() as conn:
        uname_row = conn.execute(
            """
            SELECT MAX(account_username) FROM inventory_daily
            WHERE snapshot_date = ? AND account_id = ?
            """,
            (snapshot_date, account_id),
        ).fetchone()
        uname = (uname_row[0] or "") if uname_row else ""
        acct_tag = account_display_for_row(account_id, uname, id_map=id_map)
        total = conn.execute(
            """
            SELECT COUNT(*) FROM inventory_daily
            WHERE snapshot_date = ? AND account_id = ?
            """,
            (snapshot_date, account_id),
        ).fetchone()[0]
        cur = conn.execute(
            """
            SELECT country, warehouse, sku, name_zh, name_en,
                   qty_available, qty_on_hand, row_json
            FROM inventory_daily
            WHERE snapshot_date = ? AND account_id = ?
            ORDER BY (qty_available IS NULL), qty_available DESC, warehouse, sku
            LIMIT ? OFFSET ?
            """,
            (snapshot_date, account_id, PAGE_SIZE, offset),
        )
        data = cur.fetchall()

    rows_html = ""
    for country, wh, sku, nzh, nen, qav, qoh, rj in data:
        j = html.escape(rj[:500] + ("…" if len(rj) > 500 else ""))
        rows_html += (
            f"<tr><td>{html.escape(str(country or ''))}</td>"
            f"<td>{html.escape(str(wh or ''))}</td>"
            f"<td>{html.escape(str(sku or ''))}</td>"
            f"<td>{html.escape(str(nzh or '')[:80])}</td>"
            f"<td class=\"num\">{html.escape(cell_int_str(qav))}</td>"
            f"<td class=\"num\">{html.escape(cell_int_str(qoh))}</td>"
            f"<td><details><summary>row_json</summary><pre>{j}</pre></details></td></tr>"
        )

    prev_q = f"snapshot_date={html.escape(snapshot_date, quote=True)}&account_id={account_id}&page={page - 1}"
    next_q = f"snapshot_date={html.escape(snapshot_date, quote=True)}&account_id={account_id}&page={page + 1}"
    nav = f'<p class="muted">共 {cell_int_str(total)} 行，第 {cell_int_str(page)} 页 '
    if page > 1:
        nav += f'<a href="/table?{prev_q}">上一页</a> '
    if offset + len(data) < total:
        nav += f'<a href="/table?{next_q}">下一页</a>'
    nav += "</p>"

    title = f"{snapshot_date} · {acct_tag}"
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>{html.escape(title)}</title>
  <style>{VIEWER_THEME_CSS}
    pre {{ white-space: pre-wrap; word-break: break-all; font-size: 11px; max-height: 240px; overflow: auto; background: #f8fafc; padding: 0.5rem; border-radius: 6px; }}
  </style>
</head>
<body>
<div class="page">
  <div class="toolbar" style="margin-bottom:0.75rem">
    <a href="/">← 返回汇总</a>
  </div>
  <header class="banner" style="padding:1rem 1.25rem">
    <h1 style="font-size:1.2rem">{html.escape(title)}</h1>
    <p class="sub">可用 / 在库数量为整数；本页按可用库存从高到低排序</p>
  </header>
  {nav}
  <section class="card" style="padding:0.75rem 1rem 1rem">
  <table class="data">
    <thead>
      <tr>
        <th>国家</th><th>仓库</th><th>SKU</th><th>中文名</th>
        <th class="num">可用</th><th class="num">在库</th><th>全字段 JSON</th>
      </tr>
    </thead>
    <tbody>{rows_html}</tbody>
  </table>
  </section>
  {nav}
</div>
</body>
</html>"""


@app.route("/runs")
def runs() -> str:
    with _conn() as conn:
        cur = conn.execute(
            """
            SELECT id, snapshot_date, account_id, account_username, zip_path,
                   row_count, status, substr(detail, 1, 200) AS detail_preview,
                   started_at, finished_at
            FROM sync_runs
            ORDER BY id DESC
            LIMIT 100
            """
        )
        rows = cur.fetchall()

    body = ""
    for r in rows:
        rid, sd, aid, user, zp, rc, st, dprev, sta, fin = r
        zp_e = html.escape(zp or "")
        user_e = html.escape(user or "")
        st_e = html.escape(st or "")
        d_e = html.escape(dprev or "")
        body += (
            f"<tr><td class=\"num\">{cell_int_str(rid)}</td><td>{html.escape(sd)}</td>"
            f"<td class=\"num\">{cell_int_str(aid)}</td><td>{user_e}</td>"
            f"<td class=\"num\">{cell_int_str(rc)}</td><td>{st_e}</td><td class=\"muted\">{zp_e}</td>"
            f"<td><small>{d_e}</small></td><td><small>{html.escape(sta or '')}</small></td>"
            f"<td><small>{html.escape(fin or '')}</small></td>"
            f"<td><a href=\"/runs/{rid}\">详情</a></td></tr>"
        )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8"/>
  <title>同步运行记录</title>
  <style>{VIEWER_THEME_CSS}
    .muted {{ word-break: break-all; }}
  </style>
</head>
<body>
<div class="page">
  <div class="toolbar" style="margin-bottom:0.75rem">
    <a href="/">← 返回汇总</a>
  </div>
  <header class="banner" style="padding:1rem 1.25rem">
    <h1 style="font-size:1.2rem">同步运行记录</h1>
    <p class="sub">最近 100 条 · 行数为整数</p>
  </header>
  <section class="card" style="padding:0.75rem 1rem 1rem">
  <table class="data">
    <thead>
      <tr>
        <th class="num">id</th><th>日期</th><th class="num">账号</th><th>用户</th>
        <th class="num">行数</th><th>状态</th>
        <th>zip</th><th>摘要</th><th>开始</th><th>结束</th><th></th>
      </tr>
    </thead>
    <tbody>{body or "<tr><td colspan=11>暂无记录</td></tr>"}</tbody>
  </table>
  </section>
</div>
</body>
</html>"""


@app.route("/report/no-sales")
def report_no_sales() -> str:
    """无动销预警明细；可选 ?account_id=1&snapshot_date=YYYY-MM-DD"""
    aid = request.args.get("account_id", type=int)
    sd = request.args.get("snapshot_date", "").strip() or None
    with _conn() as conn:
        rows, th_meta = collect_no_sales_rows(
            conn, account_id=aid, snapshot_date=sd
        )
    parts = []
    if aid is not None:
        parts.append(f"account_id={aid}")
    if sd:
        parts.append(f"snapshot_date={sd}")
    note = ("筛选：" + " ".join(parts)) if parts else "各账号使用各自最新快照日"
    return render_no_sales_report_html(rows, th_meta, query_note=note)


@app.route("/runs/<int:run_id>")
def run_detail(run_id: int) -> Response:
    with _conn() as conn:
        row = conn.execute(
            """
            SELECT * FROM sync_runs WHERE id = ?
            """,
            (run_id,),
        ).fetchone()
    if not row:
        abort(404)
    keys = [d[0] for d in row.keys()]
    obj = {k: row[k] for k in keys}
    text = json.dumps(obj, ensure_ascii=False, indent=2, default=str)
    return Response(text, mimetype="application/json; charset=utf-8")


def main() -> None:
    host = os.environ.get("WINIT_VIEWER_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = int(os.environ.get("WINIT_VIEWER_PORT", "8765"))
    print(f"打开浏览器访问 http://{host}:{port}/  （库：{sqlite_path()}）", flush=True)
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
