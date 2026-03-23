"""
InventoryInoutSeller 入库：独立 SQLite（不与库存快照库混用）。

特性：
- 按账号覆盖写入（仅保留当前数据，不按天保留历史）
- 明细行以 row_json 形式存储，兼容动态列
- 保存每账号最新文件元数据（latest_meta）
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parent
DEFAULT_INOUT_DB_PATH = ROOT / "artifacts" / "winit_inout.db"


def inout_sqlite_path() -> Path:
    raw = os.environ.get("WINIT_INOUT_SQLITE_PATH", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    p = DEFAULT_INOUT_DB_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def connect_inout() -> sqlite3.Connection:
    conn = sqlite3.connect(str(inout_sqlite_path()))
    conn.row_factory = sqlite3.Row
    return conn


def init_inout_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS inventory_inout_current (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL,
            account_username TEXT,
            file_name TEXT,
            sheet_name TEXT,
            row_no INTEGER NOT NULL,
            row_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_inout_current_acct
            ON inventory_inout_current (account_id);

        CREATE TABLE IF NOT EXISTS inventory_inout_latest_meta (
            account_id INTEGER PRIMARY KEY,
            account_username TEXT,
            file_path TEXT,
            file_name TEXT,
            file_size_bytes INTEGER NOT NULL DEFAULT 0,
            row_count INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        );
        """
    )
    conn.commit()


def replace_inout_current_rows(
    conn: sqlite3.Connection,
    *,
    account_id: int,
    account_username: str,
    file_name: str,
    sheet_name: str,
    rows_json: Iterable[str],
) -> int:
    conn.execute("DELETE FROM inventory_inout_current WHERE account_id = ?", (account_id,))
    payload = [
        (account_id, account_username, file_name, sheet_name, i + 1, rj)
        for i, rj in enumerate(rows_json)
    ]
    conn.executemany(
        """
        INSERT INTO inventory_inout_current (
            account_id, account_username, file_name, sheet_name, row_no, row_json
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        payload,
    )
    conn.commit()
    return len(payload)


def upsert_inout_latest_meta(
    conn: sqlite3.Connection,
    *,
    account_id: int,
    account_username: str,
    file_path: str,
    file_name: str,
    file_size_bytes: int,
    row_count: int,
    updated_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO inventory_inout_latest_meta (
            account_id, account_username, file_path, file_name,
            file_size_bytes, row_count, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(account_id) DO UPDATE SET
            account_username=excluded.account_username,
            file_path=excluded.file_path,
            file_name=excluded.file_name,
            file_size_bytes=excluded.file_size_bytes,
            row_count=excluded.row_count,
            updated_at=excluded.updated_at
        """,
        (
            account_id,
            account_username,
            file_path,
            file_name,
            int(file_size_bytes),
            int(row_count),
            updated_at,
        ),
    )
    conn.commit()
