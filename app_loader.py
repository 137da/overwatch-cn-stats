"""启动壳：打包版从 exe 旁边的 app/ 文件夹加载程序代码。

打包时 ow_desktop、qr_login 两个包不进 exe，而是编译成 .pyc 放在 app/ 里（见 make_update.py），
所以更新程序只需替换 app/（应用内「安装更新包」），不用重新跑 PyInstaller。
exe 里只剩这个文件、Python 运行库和第三方依赖；它们变了（加了新库、换了 Python 版本、改了这个文件）
才需要完整重新打包，并把 SHELL_VERSION 加 1，让旧壳拒绝装需要新壳的更新包。

源码运行（python -m ow_desktop）时 prepare() 什么都不做，照常从项目根目录导入。
"""

from __future__ import annotations

import importlib
import os
import sys
import traceback
from pathlib import Path

SHELL_VERSION = 2                 # 启动壳版本：依赖或本文件、入口变了就 +1，并完整重新打包（2：界面与命令行合成一个 exe）
APP_DIR_NAME = "app"              # 程序代码文件夹名（在 exe 旁边）
PACKAGES = ("qr_login", "ow_desktop")  # app/ 里的包
ENV_APP_DIR = "OW_APP_DIR"        # 环境变量：改从这个文件夹加载（更新前自检新代码用）
ENV_SELFTEST = "OW_APP_SELFTEST"  # 环境变量：只把全部模块导入一遍，结果写到它指定的文件后退出


def app_dir() -> Path | None:
    """打包版的程序代码文件夹；源码运行返回 None。"""
    if not getattr(sys, "frozen", False):
        return None
    override = os.environ.get(ENV_APP_DIR)
    return Path(override) if override else Path(sys.executable).resolve().parent / APP_DIR_NAME


def prepare() -> None:
    """把 app/ 放到导入路径最前面；自检模式下导入完直接退出。"""
    d = app_dir()
    if d is None:
        return
    if not (d / "ow_desktop").is_dir():
        raise RuntimeError(f"找不到程序文件夹 {d}\n请重新下载完整版，或把 app.old 改回 app 恢复上一版。")
    sys.path.insert(0, str(d))
    if os.environ.get(ENV_SELFTEST):
        sys.exit(_selftest(d, Path(os.environ[ENV_SELFTEST])))


def _selftest(d: Path, report: Path) -> int:
    """逐个导入 app/ 里的全部模块：缺依赖、Python 版本不符（.pyc 魔数不对）、语法问题都会在这里暴露。"""
    failed = []
    for pkg in PACKAGES:
        for f in sorted((d / pkg).glob("*.pyc")):
            name = f"{pkg}.{f.stem}"
            try:
                importlib.import_module(name)
            except BaseException:  # noqa: BLE001  模块里的 sys.exit 也要算失败
                failed.append(f"{name}: {traceback.format_exc(limit=2)}")
    report.write_text("\n".join(failed) or "ok", encoding="utf-8")
    return 1 if failed else 0
