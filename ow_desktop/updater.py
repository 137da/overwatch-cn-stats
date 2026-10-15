"""应用内更新：安装更新包（make_update.py 打的 zip）、回滚到上一版、重启。

只在打包版里可用：程序代码在 exe 旁边的 app/（见 app_loader.py），更新就是换掉这个文件夹。
步骤：校验包结构与版本 → 解压到临时目录 → 用本程序 exe 自检（把新代码全部导入一遍）
→ 现有 app/ 改名为 app.old/，新的换上。任何一步失败，现有版本都不受影响。
data/、state/ 不在 app/ 里，更新与回滚都不碰。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import zipfile
from pathlib import Path, PurePosixPath

import app_loader

from . import errors as E
from .paths import APP_ROOT

APP_DIR = APP_ROOT / app_loader.APP_DIR_NAME              # 正在用的程序代码
OLD_DIR = APP_ROOT / f"{app_loader.APP_DIR_NAME}.old"     # 上一版，回滚用
WORK_DIR = APP_ROOT / f"{app_loader.APP_DIR_NAME}.update"  # 解压与自检用的临时目录
MANIFEST = "VERSION.json"                                 # app/ 里的版本信息
SELFTEST_TIMEOUT_SEC = 90                                 # 自检最长等待
RESTART_DELAY_SEC = 0.5                                   # 新进程拉起后，旧进程等这么久再退出
NO_WINDOW = 0x08000000                                    # CREATE_NO_WINDOW：自检不弹黑窗
DETACHED = 0x00000008 | 0x00000200                        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP


def _frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _python_tag() -> str:
    return f"{sys.version_info.major}.{sys.version_info.minor}"


def _manifest(d: Path) -> dict | None:
    try:
        return json.loads((d / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def info() -> dict:
    """当前版本、启动壳版本、能否回滚。"""
    if not _frozen():
        return {"frozen": False, "version": "源码运行", "shell": app_loader.SHELL_VERSION, "python": _python_tag()}
    cur, old = _manifest(APP_DIR) or {}, _manifest(OLD_DIR)
    return {"frozen": True, "version": cur.get("version", "未知"), "builtAt": cur.get("builtAt", ""),
            "shell": app_loader.SHELL_VERSION, "python": _python_tag(),
            "rollback": old.get("version", "未知") if old else None}


def _require_frozen() -> None:
    if not _frozen():
        raise E.AppError(E.API, "源码运行时代码就在项目里，改完直接重开即可，不需要更新包")


def _check_manifest(m: dict) -> None:
    if m.get("python") != _python_tag():
        raise E.AppError(E.API, f"更新包是给 Python {m.get('python')} 编译的，本程序是 {_python_tag()}，"
                                "需要重新下载完整版")
    if int(m.get("shell", 0)) > app_loader.SHELL_VERSION:
        raise E.AppError(E.API, f"更新包需要启动壳 {m.get('shell')}，本程序是 {app_loader.SHELL_VERSION}"
                                "（加了新的依赖），需要重新下载完整版")


def _extract(zip_path: Path) -> tuple[Path, dict]:
    """校验并解压到 WORK_DIR，返回 (新 app 目录, 版本信息)。"""
    try:
        zf = zipfile.ZipFile(zip_path)
    except (OSError, zipfile.BadZipFile) as e:
        raise E.AppError(E.API, f"不是有效的更新包：{e}") from e
    prefix = app_loader.APP_DIR_NAME
    with zf:
        for name in zf.namelist():
            parts = PurePosixPath(name).parts
            if not parts or parts[0] != prefix or ".." in parts or name.startswith("/") or ":" in name:
                raise E.AppError(E.API, f"更新包结构不对（{name}），不是 make_update.py 打的包")
        try:
            manifest = json.loads(zf.read(f"{prefix}/{MANIFEST}"))
        except (KeyError, ValueError) as e:
            raise E.AppError(E.API, "更新包里没有版本信息，不是 make_update.py 打的包") from e
        _check_manifest(manifest)
        shutil.rmtree(WORK_DIR, ignore_errors=True)
        zf.extractall(WORK_DIR)
    new = WORK_DIR / prefix
    if not (new / "ow_desktop").is_dir():
        raise E.AppError(E.API, "更新包里缺少程序代码")
    return new, manifest


def _selftest(new: Path) -> None:
    """再起一个本程序 exe，从新目录把全部模块导入一遍；失败就不装。"""
    report = WORK_DIR / "selftest.txt"
    env = {**os.environ, app_loader.ENV_APP_DIR: str(new), app_loader.ENV_SELFTEST: str(report)}
    try:
        r = subprocess.run([sys.executable], env=env, cwd=APP_ROOT, capture_output=True,
                           timeout=SELFTEST_TIMEOUT_SEC, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise E.AppError(E.API, f"自检没能运行（{Path(sys.executable).name}）：{e}") from e
    text = report.read_text(encoding="utf-8") if report.exists() else r.stderr.decode("utf-8", "replace")
    if r.returncode != 0:
        raise E.AppError(E.API, f"更新包自检失败，没有安装：\n{text.strip()[-800:]}")


def install(zip_path: str) -> dict:
    """安装更新包；成功后需重启生效。"""
    _require_frozen()
    try:
        new, manifest = _extract(Path(zip_path))
        _selftest(new)
        shutil.rmtree(OLD_DIR, ignore_errors=True)
        APP_DIR.rename(OLD_DIR)
        try:
            new.rename(APP_DIR)
        except OSError:
            OLD_DIR.rename(APP_DIR)  # 换不上就把原来的放回去
            raise
    except OSError as e:
        raise E.AppError(E.API, f"替换程序文件失败（{e}），当前版本未改动") from e
    finally:
        shutil.rmtree(WORK_DIR, ignore_errors=True)
    return {"version": manifest["version"], **info()}


def rollback() -> dict:
    """和上一版互换：回滚后还能再「回滚」回来。"""
    _require_frozen()
    if not _manifest(OLD_DIR):
        raise E.AppError(E.API, "没有可回滚的上一版")
    swap = APP_ROOT / f"{app_loader.APP_DIR_NAME}.swap"
    try:
        APP_DIR.rename(swap)
        OLD_DIR.rename(APP_DIR)
        swap.rename(OLD_DIR)
    except OSError as e:
        raise E.AppError(E.API, f"回滚失败（{e}），请检查程序文件夹里的 app、app.old、app.swap") from e
    return info()


def restart() -> None:
    """拉起一个新的自己，稍后退出当前进程。"""
    _require_frozen()
    subprocess.Popen([sys.executable], cwd=APP_ROOT, creationflags=DETACHED, close_fds=True)
    threading.Timer(RESTART_DELAY_SEC, os._exit, args=(0,)).start()
