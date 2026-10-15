"""应用根目录：可写数据（data/、state/）都放在它下面。

源码运行时是项目根目录；PyInstaller 打包后是 exe 所在目录（便携式，整个文件夹可拷走）。
只读资源（web/、mappings/）随包走，仍用各模块自己的 Path(__file__)，不经过这里。
"""

from __future__ import annotations

import sys
from pathlib import Path


def app_root() -> Path:  # 应用根目录
    # 打包后 __file__ 指向运行库目录（onefile 时还是临时解压目录），写进去的数据会丢，所以改取 exe 旁边
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


APP_ROOT = app_root()             # 应用根目录
DATA_DIR = APP_ROOT / "data"      # 本地库与设置
STATE_DIR = APP_ROOT / "state"    # 设备标识、会话 cookie 与凭据
