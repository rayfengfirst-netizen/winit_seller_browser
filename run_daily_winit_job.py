"""
定时任务入口：按账号顺序执行「浏览器导出 zip → 解压 → 写入当日库存快照」。

环境变量（常用）：
  WINIT_SKIP_DOWNLOAD=1   跳过 step02，只取该账号下载目录里最新 zip 再入库（调试）
  WINIT_SNAPSHOT_DATE=2026-03-23  快照业务日期（默认 UTC 日期；若要用本地日可再改）
  WINIT_SQLITE_PATH       SQLite 文件路径，默认 artifacts/winit_inventory.db

手动跑：
  cd winit_seller_browser && source .venv/bin/activate
  python run_daily_winit_job.py

服务器上与 systemd timer 配合：见 deploy/winit-daily-sync.service.example
"""

from __future__ import annotations

import os
import sys
import tempfile
import traceback
from datetime import date, datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

from step02_australia_export import run_step02_export_for_account  # noqa: E402
from step03_unpack_winit_export import extract_zip  # noqa: E402
from winit_accounts import (  # noqa: E402
    WinitAccount,
    downloads_dir_base,
    list_winit_accounts,
    resolve_download_dir_for_account,
)
from winit_inventory_db import connect, init_schema, log_sync_run, sqlite_path  # noqa: E402
from winit_inventory_ingest import ingest_inventory_xlsx  # noqa: E402


def _snapshot_date_str() -> str:
    raw = os.environ.get("WINIT_SNAPSHOT_DATE", "").strip()
    if raw:
        return raw
    # 默认按「本机日历日」；服务器在国内一般与业务日一致。若要严格 UTC 可改为 date.today() 在 UTC 下算。
    return date.today().isoformat()


def _skip_download() -> bool:
    return os.environ.get("WINIT_SKIP_DOWNLOAD", "").lower() in ("1", "true", "yes")


def find_latest_zip_for_account(account: WinitAccount) -> Path:
    d = resolve_download_dir_for_account(account)
    zips = list(d.glob("inventorySellerPortalExport*.zip"))
    if not zips:
        zips = list(d.glob("*.zip"))
    if not zips and account.id == 1:
        # 多账号分目录前，账号 1 的 zip 可能仍在 downloads/ 根目录
        root = downloads_dir_base()
        zips = list(root.glob("inventorySellerPortalExport*.zip"))
        if not zips:
            zips = [p for p in root.glob("*.zip") if p.parent == root]
    if not zips:
        raise FileNotFoundError(f"账号 {account.id} 目录下无 zip：{d}")
    return max(zips, key=lambda p: p.stat().st_mtime)


def run_one_account(
    conn,
    account: WinitAccount,
    snapshot_date: str,
    *,
    skip_download: bool,
) -> int:
    started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    zip_path_str = ""
    try:
        if not skip_download:
            code = run_step02_export_for_account(account)
            if code != 0:
                finished = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                log_sync_run(
                    conn,
                    snapshot_date=snapshot_date,
                    account_id=account.id,
                    account_username=account.username,
                    zip_path="",
                    row_count=0,
                    status="export_failed",
                    detail=f"step02 退出码 {code}",
                    started_at=started,
                    finished_at=finished,
                )
                return code

        zip_path = find_latest_zip_for_account(account)
        zip_path_str = str(zip_path)

        with tempfile.TemporaryDirectory() as td:
            tdir = Path(td)
            extract_zip(zip_path, tdir)
            xlsx_files = sorted(tdir.rglob("*.xlsx"))
            if not xlsx_files:
                raise RuntimeError(f"zip 内无 xlsx：{zip_path}")

            n = ingest_inventory_xlsx(
                conn,
                xlsx_files[0],
                snapshot_date=snapshot_date,
                account_id=account.id,
                account_username=account.username,
            )

        finished = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        log_sync_run(
            conn,
            snapshot_date=snapshot_date,
            account_id=account.id,
            account_username=account.username,
            zip_path=zip_path_str,
            row_count=n,
            status="ok",
            detail="",
            started_at=started,
            finished_at=finished,
        )
        print(
            f"[daily] 账号 {account.id} {account.username} 入库 {n} 行，"
            f"日期 {snapshot_date}，zip {zip_path_str}",
            flush=True,
        )
        return 0
    except Exception as e:
        finished = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        log_sync_run(
            conn,
            snapshot_date=snapshot_date,
            account_id=account.id,
            account_username=account.username,
            zip_path=zip_path_str,
            row_count=0,
            status="error",
            detail=f"{type(e).__name__}: {e}\n{traceback.format_exc()}",
            started_at=started,
            finished_at=finished,
        )
        print(f"[daily] 账号 {account.id} 失败：", e, file=sys.stderr)
        traceback.print_exc()
        return 1


def main() -> int:
    accs = list_winit_accounts()
    if not accs:
        print("未配置任何 Winit 账号（.env）", file=sys.stderr)
        return 1

    snapshot_date = _snapshot_date_str()
    skip_dl = _skip_download()
    print(
        f"[daily] 快照日期 {snapshot_date}，账号数 {len(accs)}，"
        f"跳过下载={'是' if skip_dl else '否'}，库 {sqlite_path()}",
        flush=True,
    )

    conn = connect()
    init_schema(conn)
    exit_code = 0
    try:
        for account in accs:
            code = run_one_account(conn, account, snapshot_date, skip_download=skip_dl)
            if code != 0:
                exit_code = code
    finally:
        conn.close()

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
