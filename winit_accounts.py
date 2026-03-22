"""
多账号配置：从环境变量解析 Winit 登录账号列表。

约定（向后兼容只配一个账号时仍用 WINIT_USERNAME / WINIT_PASSWORD）：
  - 账号 1：WINIT_USERNAME + WINIT_PASSWORD，或 WINIT_ACCOUNT_1_USERNAME + WINIT_ACCOUNT_1_PASSWORD
  - 账号 2+：必须写 WINIT_ACCOUNT_2_USERNAME、WINIT_ACCOUNT_2_PASSWORD（3、4… 同理，编号连续）

选用哪个账号跑单次任务：WINIT_ACCOUNT_ID（默认 1）。
顺序跑全部已配置账号：WINIT_RUN_ALL_ACCOUNTS=1（当前仅 step02 / login_winit 主流程接入）。

下载目录：配置了两个及以上账号时，默认按子目录 downloads/account_1、account_2 分开
（可用 WINIT_DOWNLOAD_PER_ACCOUNT=0 强制仍用同一目录，有覆盖风险）。
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class WinitAccount:
    id: int
    username: str
    password: str
    label: str = ""

    def display_name(self) -> str:
        if self.label.strip():
            return f"{self.id}（{self.label.strip()}）"
        return str(self.id)


def _acc_username(n: int) -> str:
    return os.environ.get(f"WINIT_ACCOUNT_{n}_USERNAME", "").strip()


def _acc_password(n: int) -> str:
    return os.environ.get(f"WINIT_ACCOUNT_{n}_PASSWORD", "")


def _acc_label(n: int) -> str:
    return os.environ.get(f"WINIT_ACCOUNT_{n}_LABEL", "").strip()


def list_winit_accounts() -> List[WinitAccount]:
    """已配置的账号列表，id 从 1 起连续；至少 0 个（未配置时）。"""
    out: List[WinitAccount] = []

    u1 = _acc_username(1)
    p1 = _acc_password(1)
    if not u1:
        u1 = os.environ.get("WINIT_USERNAME", "").strip()
    if not p1 and u1:
        p1 = os.environ.get("WINIT_PASSWORD", "")

    if u1 and p1:
        out.append(WinitAccount(id=1, username=u1, password=p1, label=_acc_label(1)))

    n = 2
    while n <= 32:
        u = _acc_username(n)
        if not u:
            break
        p = _acc_password(n)
        if not p:
            print(
                f"已设置 WINIT_ACCOUNT_{n}_USERNAME 但缺少 WINIT_ACCOUNT_{n}_PASSWORD，"
                f"该账号将被跳过。",
                file=sys.stderr,
            )
            break
        out.append(WinitAccount(id=n, username=u, password=p, label=_acc_label(n)))
        n += 1

    return out


def pick_active_account(accs: Optional[List[WinitAccount]] = None) -> Optional[WinitAccount]:
    """根据 WINIT_ACCOUNT_ID 选取一个账号；无效 id 时回退到第一个并打日志。"""
    accs = accs if accs is not None else list_winit_accounts()
    if not accs:
        return None
    raw = os.environ.get("WINIT_ACCOUNT_ID", "1").strip()
    try:
        want = int(raw)
    except ValueError:
        want = 1
    for a in accs:
        if a.id == want:
            return a
    print(
        f"[winit_accounts] 未找到 WINIT_ACCOUNT_ID={raw!r}，改用账号 {accs[0].id}（{accs[0].username}）",
        file=sys.stderr,
    )
    return accs[0]


def run_all_winit_accounts_requested() -> bool:
    return os.environ.get("WINIT_RUN_ALL_ACCOUNTS", "").lower() in ("1", "true", "yes")


def downloads_dir_base() -> Path:
    """WINIT_DOWNLOAD_DIR 根路径（未展开账号子目录）。"""
    return Path(os.environ.get("WINIT_DOWNLOAD_DIR", str(ROOT / "downloads")))


def resolve_download_dir_for_account(account: WinitAccount) -> Path:
    """
    在 WINIT_DOWNLOAD_DIR 下是否使用按账号子目录。
    WINIT_DOWNLOAD_PER_ACCOUNT 未设置时：若配置了 2 个及以上账号则自动分目录。
    """
    base = downloads_dir_base()
    raw = os.environ.get("WINIT_DOWNLOAD_PER_ACCOUNT", "").strip().lower()
    accs = list_winit_accounts()
    if raw in ("0", "false", "no", "off"):
        use_sub = False
    elif raw in ("1", "true", "yes", "on"):
        use_sub = True
    else:
        use_sub = len(accs) >= 2

    if use_sub:
        label = account.label.strip() if account.label.strip() else f"account_{account.id}"
        safe = re.sub(r"[^a-zA-Z0-9._\u4e00-\u9fff-]+", "_", label).strip("._") or f"account_{account.id}"
        if len(safe) > 80:
            safe = safe[:80]
        base = base / safe

    base.mkdir(parents=True, exist_ok=True)
    return base
