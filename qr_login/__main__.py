"""扫码取凭据的命令行入口。

用法：
    python -m qr_login              # 扫码登录并取出 role_id / token
    python -m qr_login --reuse      # 复用上次的会话，免扫码直接换新 token
    python -m qr_login --qr-only    # 只出码（验证接口连通性，不需要账号）
    python -m qr_login --logout     # 清除本地会话

多账号：以上命令均可加 --profile <名字>，该账号的数据单独存到 state/<名字>/。
    python -m qr_login --profile main          # 主号扫码
    python -m qr_login --profile main --reuse  # 主号免扫码换新 token
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

from . import constants as C
from .client import DashenSession
from .flow import Credential, QrLoginError, QrLoginFlow, ScanState

# 状态目录：二维码、设备标识、会话 cookie、凭据都放这里（已被 .gitignore 挡住）
STATE_DIR = Path(__file__).resolve().parent.parent / "state"

# profile 名只许小写字母 / 数字 / 下划线，防止 ../ 之类的路径跳出 state/
PROFILE_PATTERN = re.compile(r"^[a-z0-9_]{1,32}$")


def _profile_dir(profile: str | None) -> Path:
    """按 profile 算出状态目录：不指定时用 state/ 根目录（兼容旧数据），否则用 state/<profile>/。"""
    if profile is None:
        return STATE_DIR
    return STATE_DIR / profile


def _profile_arg(value: str) -> str:
    """argparse 的 profile 校验器。"""
    if not PROFILE_PATTERN.match(value):
        raise argparse.ArgumentTypeError(
            f"profile 名只能用小写字母、数字、下划线（1~32 位），收到：{value!r}"
        )
    return value


def _open_image(path: Path) -> None:
    """尽量用系统默认程序打开图片，失败则静默跳过（用户仍可手动打开）。"""
    try:
        if sys.platform == "win32":
            os.startfile(str(path))  # noqa: S606  仅打开本地生成的图片
        elif sys.platform == "darwin":
            os.system(f'open "{path}"')
        else:
            os.system(f'xdg-open "{path}" >/dev/null 2>&1')
    except Exception:
        pass


def _save_credential(cred: Credential, state_dir: Path) -> Path:
    """把凭据写入本地文件。"""
    path = state_dir / "credentials.json"
    path.write_text(
        json.dumps(
            {
                "role_id": cred.role_id,
                "token": cred.token,
                "server": cred.server,
                "nick": cred.nick,
                "user_name": cred.user_name,
                "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def _print_credential(cred: Credential, saved_to: Path) -> None:
    """打印结果，并给出可直接粘贴的配置片段。"""
    who = cred.user_name or "（未知）"
    nick = cred.nick or "（无昵称）"
    print()
    print("=" * 60)
    print(f"  扫码账号：{who}")
    print(f"  守望先锋角色：{nick}")
    print(f"  role_id：{cred.role_id}")
    print(f"  token  ：{cred.token}")
    print("=" * 60)
    print(f"\n已保存到：{saved_to}")
    print("\n可直接粘贴的配置片段：\n")
    print("DASHEN_ACCOUNTS = [")
    print("    {")
    print('        "name": "primary",')
    print(f'        "role_id": {cred.role_id},')
    print(f'        "token": "{cred.token}",')
    print("    },")
    print("]")
    print()
    print("提醒：这是私人凭据，不要提交到仓库、不要外传。")
    print("     正常个人查询不会有问题，但不要拿它跑批量采集——那是上一个号被封的原因。")


def cmd_qr_only(flow: QrLoginFlow, state_dir: Path) -> int:
    """只出码，用于验证接口连通性。"""
    png = flow.create_qr()
    path = state_dir / "qrcode.png"
    path.write_bytes(png)
    print(f"二维码已生成：{path}（{len(png)} 字节）")
    print("接口连通正常。--qr-only 不做登录，到此结束。")
    return 0


def cmd_reuse(flow: QrLoginFlow, session: DashenSession, state_dir: Path) -> int:
    """复用已有会话，免扫码直接换新 token。"""
    if not session.load_session():
        print("本地没有已保存的会话，请先不带 --reuse 跑一次扫码。", file=sys.stderr)
        return 2
    print("已载入本地会话，正在校验是否仍然有效…")
    if not flow.is_session_alive():
        print("会话已失效（过期或被登出），需要重新扫码。", file=sys.stderr)
        return 3
    print("会话有效，正在换取 token…")
    cred = flow.fetch_credential()
    session.save_session()
    _print_credential(cred, _save_credential(cred, state_dir))
    return 0


def cmd_login(flow: QrLoginFlow, session: DashenSession, state_dir: Path) -> int:
    """完整扫码登录流程。"""
    print("正在申请登录二维码…")
    png = flow.create_qr()
    qr_path = state_dir / "qrcode.png"
    qr_path.write_bytes(png)
    print(f"二维码已保存：{qr_path}")
    print("请用【网易大神 App】扫码并确认登录。")
    print("（要让朋友帮忙的话，把这个 PNG 发给对方扫即可）")
    _open_image(qr_path)

    deadline = time.monotonic() + C.POLL_TIMEOUT_SEC
    waited = 0
    while time.monotonic() < deadline:
        state = flow.poll_once()
        if state is ScanState.CONFIRMED:
            who = flow.user_name or "（未知账号）"
            print(f"\n已确认：{who}")
            break
        if state is ScanState.EXPIRED:
            print("\n二维码已失效，请重新运行。", file=sys.stderr)
            return 4
        waited += 1
        print(f"\r等待扫码… {waited}s", end="", flush=True)
        time.sleep(C.POLL_INTERVAL_SEC)
    else:
        print("\n等待超时（5 分钟），请重新运行。", file=sys.stderr)
        return 5

    print("正在完成登录…")
    flow.complete_login()
    saved = session.save_session()
    print(f"会话已保存：{saved}（下次可用 --reuse 免扫码）")

    print("正在读取绑定的守望先锋角色并换取 token…")
    cred = flow.fetch_credential()
    _print_credential(cred, _save_credential(cred, state_dir))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m qr_login",
        description="网易大神扫码登录，取得查询守望先锋战绩所需的 role_id 与 token。",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--reuse", action="store_true", help="复用已保存的会话，免扫码换新 token")
    group.add_argument("--qr-only", action="store_true", help="只出码，验证接口连通性")
    group.add_argument("--logout", action="store_true", help="清除本地会话")
    parser.add_argument(
        "--profile", type=_profile_arg, default=None, metavar="名字",
        help="多账号时指定账号，数据存到 state/<名字>/；不指定则用 state/ 根目录",
    )
    args = parser.parse_args(argv)

    state_dir = _profile_dir(args.profile)  # 本次使用的状态目录
    state_dir.mkdir(parents=True, exist_ok=True)
    print(f"[账号] {args.profile or '默认'}（{state_dir}）")

    with DashenSession(state_dir) as session:
        flow = QrLoginFlow(session)
        try:
            if args.logout:
                session.clear_session()
                print("本地会话已清除。")
                return 0
            if args.qr_only:
                return cmd_qr_only(flow, state_dir)
            if args.reuse:
                return cmd_reuse(flow, session, state_dir)
            return cmd_login(flow, session, state_dir)
        except QrLoginError as e:
            print(f"\n失败：{e}", file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            print("\n已取消。", file=sys.stderr)
            return 130


if __name__ == "__main__":
    sys.exit(main())
