"""
只读 Web 页：浏览 SQLite 中的库存快照（inventory_daily）与同步记录（sync_runs）。

默认仅监听本机 127.0.0.1，请勿在未加认证的情况下对公网暴露。

运行：
  cd winit_seller_browser && source .venv/bin/activate
  pip install -r requirements.txt
  python inventory_viewer.py

环境变量：
  WINIT_SQLITE_PATH   同 run_daily_winit_job
  WINIT_VIEWER_HOST   默认 127.0.0.1
  WINIT_VIEWER_PORT   默认 8765
"""

from __future__ import annotations

import html
import json
import os
import sqlite3
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, abort, request, Response

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

from winit_inventory_db import connect, init_schema, sqlite_path  # noqa: E402

app = Flask(__name__)

PAGE_SIZE = 80


def _conn() -> sqlite3.Connection:
    conn = connect()
    init_schema(conn)
    return conn


@app.route("/")
def index() -> str:
    db = html.escape(str(sqlite_path()))
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

    rows_html = ""
    for snapshot_date, account_id, username, n in blocks:
        u = html.escape(username or "")
        rows_html += (
            f"<tr><td>{html.escape(snapshot_date)}</td>"
            f"<td>{account_id}</td><td>{u}</td><td>{n}</td>"
            f"<td><a href=\"/table?snapshot_date={html.escape(snapshot_date, quote=True)}"
            f"&account_id={account_id}\">浏览</a></td></tr>"
        )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Winit 库存快照</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 1rem; max-width: 1200px; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 14px; }}
    th, td {{ border: 1px solid #ccc; padding: 6px 8px; text-align: left; }}
    th {{ background: #f4f4f4; }}
    .muted {{ color: #666; font-size: 13px; }}
    a {{ color: #0b57d0; }}
  </style>
</head>
<body>
  <h1>Winit 库存快照</h1>
  <p class="muted">数据库文件：<code>{db}</code></p>
  <h2>按日期 · 账号汇总</h2>
  <table>
    <thead><tr><th>快照日期</th><th>账号 ID</th><th>登录名</th><th>行数</th><th></th></tr></thead>
    <tbody>{rows_html or (
        "<tr><td colspan=5><strong>暂无快照数据。</strong><br/>"
        "在本机项目目录执行：<code>python run_daily_winit_job.py</code>（有 zip 可先 "
        "<code>WINIT_SKIP_DOWNLOAD=1 python run_daily_winit_job.py</code>）。<br/>"
        "若曾把数据写入其它库，请在 <code>.env</code> 里设置 <code>WINIT_SQLITE_PATH</code> 与这里一致后重启本页服务。"
        "</td></tr>"
    )}</tbody>
  </table>
  <p><a href="/runs">同步运行记录 sync_runs →</a></p>
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
    with _conn() as conn:
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
            ORDER BY warehouse, sku
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
            f"<td>{qav}</td><td>{qoh}</td>"
            f"<td><details><summary>row_json</summary><pre>{j}</pre></details></td></tr>"
        )

    prev_q = f"snapshot_date={html.escape(snapshot_date, quote=True)}&account_id={account_id}&page={page - 1}"
    next_q = f"snapshot_date={html.escape(snapshot_date, quote=True)}&account_id={account_id}&page={page + 1}"
    nav = f'<p class="muted">共 {total} 行，第 {page} 页 '
    if page > 1:
        nav += f'<a href="/table?{prev_q}">上一页</a> '
    if offset + len(data) < total:
        nav += f'<a href="/table?{next_q}">下一页</a>'
    nav += "</p>"

    title = f"{snapshot_date} · 账号 {account_id}"
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>{html.escape(title)}</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 1rem; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
    th, td {{ border: 1px solid #ccc; padding: 5px 6px; text-align: left; vertical-align: top; }}
    th {{ background: #f4f4f4; }}
    pre {{ white-space: pre-wrap; word-break: break-all; font-size: 11px; max-height: 240px; overflow: auto; }}
    .muted {{ color: #666; }}
    a {{ color: #0b57d0; }}
  </style>
</head>
<body>
  <p><a href="/">← 返回汇总</a></p>
  <h1>{html.escape(title)}</h1>
  {nav}
  <table>
    <thead>
      <tr>
        <th>国家</th><th>仓库</th><th>SKU</th><th>中文名</th><th>可用</th><th>在库</th><th>全字段 JSON</th>
      </tr>
    </thead>
    <tbody>{rows_html}</tbody>
  </table>
  {nav}
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
            f"<tr><td>{rid}</td><td>{html.escape(sd)}</td><td>{aid}</td><td>{user_e}</td>"
            f"<td>{rc}</td><td>{st_e}</td><td class=\"muted\">{zp_e}</td>"
            f"<td><small>{d_e}</small></td><td><small>{html.escape(sta or '')}</small></td>"
            f"<td><small>{html.escape(fin or '')}</small></td>"
            f"<td><a href=\"/runs/{rid}\">详情</a></td></tr>"
        )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8"/>
  <title>sync_runs</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 1rem; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
    th, td {{ border: 1px solid #ccc; padding: 6px; text-align: left; }}
    th {{ background: #f4f4f4; }}
    .muted {{ color: #555; word-break: break-all; }}
    a {{ color: #0b57d0; }}
  </style>
</head>
<body>
  <p><a href="/">← 返回汇总</a></p>
  <h1>同步运行记录（最近 100 条）</h1>
  <table>
    <thead>
      <tr>
        <th>id</th><th>日期</th><th>账号</th><th>用户</th><th>行数</th><th>状态</th>
        <th>zip</th><th>摘要</th><th>开始</th><th>结束</th><th></th>
      </tr>
    </thead>
    <tbody>{body or "<tr><td colspan=11>暂无记录</td></tr>"}</tbody>
  </table>
</body>
</html>"""


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
