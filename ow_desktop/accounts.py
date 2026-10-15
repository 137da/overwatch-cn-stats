"""已保存账号的文件存取：把某个账号槽位（查询号 state/、主号 state/main/）的凭据整套备份、恢复、删除。

一套凭据 = 会话 cookie + bigdata 凭据 + 设备标识，三者必须一起搬：会话与设备标识是绑定的。
备份按槽位、角色 ID 存到 state/saved/<槽位>/<role_id>/（state/ 已被 .gitignore 挡住）。
这里只做文件操作，不发请求；换 token、切换流程在 service 里。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

ACCOUNT_FILES = ("session.json", "credentials.json", "device.txt")  # 一套凭据包含的文件


def role_id_of(slot_dir: Path) -> str:
    """读槽位里当前账号的角色 ID，没有凭据返回空串。"""
    try:
        cred = json.loads((slot_dir / "credentials.json").read_text(encoding="utf-8"))
        return str(int(cred["role_id"]))
    except (OSError, ValueError, KeyError, TypeError):
        return ""


def _copy_set(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    for name in ACCOUNT_FILES:
        if (src / name).exists():
            shutil.copy2(src / name, dst / name)


def backup(slot_dir: Path, saved_root: Path) -> str:
    """把槽位里的当前账号备份到 saved_root/<role_id>/，返回角色 ID；槽位为空则什么也不做。"""
    role_id = role_id_of(slot_dir)
    if role_id:
        _copy_set(slot_dir, saved_root / role_id)
    return role_id


def restore(saved_root: Path, role_id: str, slot_dir: Path) -> None:
    """把已保存的账号放回槽位（覆盖槽位里的三个文件）。"""
    src = saved_dir(saved_root, role_id)
    if not (src / "session.json").exists():
        raise FileNotFoundError(f"没有保存角色 {role_id} 的会话")
    _copy_set(src, slot_dir)


def saved_dir(saved_root: Path, role_id: str) -> Path:
    """角色 ID 只许是数字，防止 ../ 之类的路径跳出 saved 目录。"""
    if not str(role_id).isdigit():
        raise ValueError(f"角色 ID 不合法：{role_id!r}")
    return saved_root / str(role_id)


def list_saved(saved_root: Path) -> list[Path]:
    """所有已保存账号的目录，按目录修改时间倒序（最近备份的在前）。"""
    if not saved_root.is_dir():
        return []
    dirs = [d for d in saved_root.iterdir() if d.is_dir() and d.name.isdigit() and (d / "credentials.json").exists()]
    return sorted(dirs, key=lambda d: (d / "credentials.json").stat().st_mtime, reverse=True)


def delete(saved_root: Path, role_id: str) -> None:
    shutil.rmtree(saved_dir(saved_root, role_id), ignore_errors=False)
