"""
万邑通库存日快照：SQLite 表定义与按「日期 + 账号」整批替换写入。

库文件路径：环境变量 WINIT_SQLITE_PATH，默认 <项目>/artifacts/winit_inventory.db
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Sequence, Tuple

ROOT = Path(__file__).resolve().parent

DEFAULT_DB_PATH = ROOT / "artifacts" / "winit_inventory.db"


def sqlite_path() -> Path:
    raw = os.environ.get("WINIT_SQLITE_PATH", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    p = DEFAULT_DB_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(sqlite_path()))
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS inventory_daily (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_date TEXT NOT NULL,
            account_id INTEGER NOT NULL,
            account_username TEXT,
            country TEXT,
            warehouse TEXT,
            sku TEXT NOT NULL,
            name_zh TEXT,
            name_en TEXT,
            qty_available REAL,
            qty_on_hand REAL,
            row_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_inv_daily_date_acct
            ON inventory_daily (snapshot_date, account_id);
        CREATE INDEX IF NOT EXISTS idx_inv_daily_sku
            ON inventory_daily (sku, warehouse, snapshot_date);
        CREATE TABLE IF NOT EXISTS sync_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_date TEXT NOT NULL,
            account_id INTEGER NOT NULL,
            account_username TEXT,
            zip_path TEXT,
            row_count INTEGER NOT NULL,
            status TEXT NOT NULL,
            detail TEXT,
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL
        );
        """
    )
    conn.commit()


def replace_snapshot_rows(
    conn: sqlite3.Connection,
    *,
    snapshot_date: str,
    account_id: int,
    account_username: str,
    rows: Sequence[Tuple[Any, ...]],
) -> int:
    """
    先删除该日该账号已有行，再批量插入。
    每行元组：
      (country, warehouse, sku, name_zh, name_en, qty_available, qty_on_hand, row_json)
    """
    conn.execute(
        "DELETE FROM inventory_daily WHERE snapshot_date = ? AND account_id = ?",
        (snapshot_date, account_id),
    )
    conn.executemany(
        """
        INSERT INTO inventory_daily (
            snapshot_date, account_id, account_username,
            country, warehouse, sku, name_zh, name_en,
            qty_available, qty_on_hand, row_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                snapshot_date,
                account_id,
                account_username,
                r[0],
                r[1],
                r[2],
                r[3],
                r[4],
                r[5],
                r[6],
                r[7],
            )
            for r in rows
        ],
    )
    conn.commit()
    return len(rows)


def log_sync_run(
    conn: sqlite3.Connection,
    *,
    snapshot_date: str,
    account_id: int,
    account_username: str,
    zip_path: str,
    row_count: int,
    status: str,
    detail: str,
    started_at: str,
    finished_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO sync_runs (
            snapshot_date, account_id, account_username, zip_path,
            row_count, status, detail, started_at, finished_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot_date,
            account_id,
            account_username,
            zip_path,
            row_count,
            status,
            detail,
            started_at,
            finished_at,
        ),
    )
    conn.commit()
