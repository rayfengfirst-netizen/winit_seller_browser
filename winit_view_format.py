"""
页面表格中数字展示为整数（四舍五入）。
"""

from __future__ import annotations

from typing import Any


def cell_int_str(v: Any) -> str:
    if v is None or v == "":
        return ""
    if isinstance(v, bool):
        return "1" if v else "0"
    try:
        return str(int(round(float(v))))
    except (TypeError, ValueError):
        return str(v)
