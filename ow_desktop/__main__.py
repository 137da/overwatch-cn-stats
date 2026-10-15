"""统一入口：python -m ow_desktop [子命令…]；打包版就是 战绩查询.exe。

不带参数打开界面；带参数当命令行用（与 python -m ow_desktop.cli 相同，见 cli.py）。
"""

from __future__ import annotations

import sys
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent / "web"  # 前端静态文件目录
ICON_PATH = WEB_DIR / "app_icon.ico"  # 源码与打包版共用的窗口图标
ATTACH_PARENT_PROCESS = -1  # AttachConsole 参数：附加到启动本进程的那个终端


def main() -> None:
    """打开界面。webview 只在这里导入，命令行模式不必加载它。"""
    import webview

    from .bridge import Bridge

    webview.create_window(
        "守望先锋战绩查询", str(WEB_DIR / "index.html"),
        js_api=Bridge(), width=1100, height=780, min_size=(820, 600),
    )
    webview.start(icon=str(ICON_PATH))


def _attach_parent_console() -> None:
    """打包版是窗口程序（双击不弹黑框），自己没有控制台。

    被 agent / 脚本用管道或文件接输出时，stdout、stderr 是继承来的，照常可用；
    人在终端里直接敲时它们是 None，这时借用父终端的控制台输出，借不到就丢弃，免得 print 报错。
    """
    if sys.stdout is not None and sys.stderr is not None:
        return
    import ctypes

    attached = ctypes.windll.kernel32.AttachConsole(ATTACH_PARENT_PROCESS)
    target = "CONOUT$" if attached else "nul"  # CONOUT$ 走控制台宽字符输出，中文不乱码
    if sys.stdout is None:
        sys.stdout = open(target, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(target, "w", encoding="utf-8")
    if attached:
        print(file=sys.stdout)  # 终端不等窗口程序结束，提示符会先出来；空一行，让输出另起一行


def run() -> None:
    if len(sys.argv) > 1:
        _attach_parent_console()
        from .cli import main as cli_main

        sys.exit(cli_main())
    main()


if __name__ == "__main__":
    run()
