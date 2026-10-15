"""暴露给前端 JS 的接口：window.pywebview.api.<方法>()。

只做参数校验与转发，业务全在 service 里。
所有方法统一返回 {"ok": true, "data": ...} 或 {"ok": false, "code": ..., "message": ...}。
"""

from __future__ import annotations

import base64
import time

from qr_login.client import DashenSession
from qr_login.flow import QrLoginFlow, ScanState

from . import database as db
from . import errors as E
from . import settings, updater
from .guard import RequestGuard
from .report_watch import ReportWatcher
from .service import ACCOUNT_DIRS, HEROES, MAIN_STATE_DIR, MAPS, SEASONS, STATE_DIR, Service, account_info


def _wrap(fn):
    """把业务异常翻译成统一的错误结构，意外异常也不吞，原样带回消息。"""
    def inner(*args, **kwargs):
        try:
            return {"ok": True, "data": fn(*args, **kwargs)}
        except E.AppError as e:
            return {"ok": False, "code": e.code, "message": e.message}
        except Exception as e:  # noqa: BLE001  兜底：让界面能看到真实原因，而不是静默失败
            return {"ok": False, "code": E.API, "message": f"{type(e).__name__}: {e}"}
    return inner


class Bridge:
    def __init__(self):
        self._service = Service(RequestGuard(counted=False))  # 与 CLI 排同一个队，但不占 CLI 的每日额度
        self._watcher = ReportWatcher()  # 举报提醒（主号）
        self._session: DashenSession | None = None  # 登录中的会话
        self._flow: QrLoginFlow | None = None
        self._login_account = "query"  # 本次扫码的账号槽位

    @_wrap
    def status(self):
        """界面启动时调用：查询号有没有凭据。"""
        return {"loggedIn": self._service.has_credential()}

    @_wrap
    def accounts(self):
        """两个账号的凭据摘要：查询号（state/）与主号（state/main/）。"""
        return [account_info(STATE_DIR, "查询号"), account_info(MAIN_STATE_DIR, "主号")]

    # ---------- 战绩查询（对应旧网站 window.aczApi.overwatch 的四个方法） ----------

    @_wrap
    def get_player(self, params: dict):
        p = dict(params or {})
        return self._service.get_player(
            str(p.get("battleTag") or ""), mode=str(p.get("mode") or "all"),
            page=int(p.get("page") or 1), limit=int(p.get("limit") or 2),
            season=str(p.get("season") or ""), customer_token=str(p.get("customerToken") or ""),
            stats=p.get("stats", True) is not False)

    @_wrap
    def seasons(self):
        """赛季下拉框：[{id, name}]，新的在前；第一项是当前赛季。"""
        return [{"id": k, "name": v} for k, v in sorted(SEASONS.items(), key=lambda kv: -int(kv[0]))]

    @_wrap
    def get_match_detail(self, match_id: str, customer_token: str):
        return self._service.match_detail(str(match_id), str(customer_token))

    # ---------- 找对局 ----------

    @_wrap
    def find_options(self):
        """找对局弹窗的输入提示：英雄名、地图名（去重排序）。"""
        heroes = {h.get("name") for h in HEROES.values() if isinstance(h, dict) and h.get("name")}
        return {"heroes": sorted(heroes), "maps": sorted({m for m in MAPS.values() if m})}

    @_wrap
    def find_candidates(self, params: dict):
        """第一步：翻对局列表筛候选。params 见 service.find_candidates。"""
        p = dict(params or {})
        return self._service.find_candidates(
            str(p.get("battleTag") or ""), str(p.get("dateFrom") or ""), str(p.get("dateTo") or ""),
            str(p.get("timeFrom") or ""), str(p.get("timeTo") or ""), mode=str(p.get("mode") or "all"), hero=str(p.get("hero") or ""),
            map_name=str(p.get("map") or ""), customer_token=str(p.get("customerToken") or ""))

    @_wrap
    def check_match(self, match_id: str, customer_token: str, names: str, heroes: str):
        """第二步：比对一场候选的同局玩家 / 英雄（详情有缓存就不发请求）。"""
        return self._service.check_match(str(match_id), str(customer_token), str(names or ""), str(heroes or ""))

    @_wrap
    def get_cached_players(self, query: str = "", offset: int = 0):
        """玩家库：offset 为已加载人数，往下滚时传入以加载下一页。"""
        return self._service.drawer_players(str(query or ""), int(offset or 0))

    @_wrap
    def toggle_followed(self, battle_tag: str):
        return {"is_followed": self._service.toggle_followed(str(battle_tag or ""))}

    # ---------- 举报提醒 ----------

    @_wrap
    def report_check(self):
        """前端按设置的心跳间隔调用：返回最近举报的玩家；有新批次时 alert 给出要自动查的人。"""
        return self._watcher.check()

    @_wrap
    def mark_report_queried(self, bnet_id: str, report_time: str):
        """在举报列表里手动查了某人后调用，标记为已查。"""
        db.mark_report_queried(str(bnet_id), str(report_time))

    # ---------- 设置 ----------

    @_wrap
    def get_settings(self):
        return {"values": settings.load(), "limits": settings.limits()}

    @_wrap
    def save_settings(self, changes: dict):
        return settings.save(dict(changes or {}))

    # ---------- 版本与更新 ----------

    @_wrap
    def update_info(self):
        return updater.info()

    @_wrap
    def install_update(self):
        """弹出选文件对话框选更新包并安装；取消选择返回 None。"""
        import webview  # 只有界面进程用得到，CLI 不必加载

        picked = webview.windows[0].create_file_dialog(webview.FileDialog.OPEN, file_types=("更新包 (*.zip)",))
        if not picked:
            return None
        return updater.install(str(picked[0]))

    @_wrap
    def rollback_update(self):
        return updater.rollback()

    @_wrap
    def restart_app(self):
        updater.restart()

    # ---------- 扫码登录 ----------

    @_wrap
    def start_login(self, account: str = "query"):
        """申请二维码，返回 data URL 供 <img> 直接显示。

        Args:
            account: "query" 查询号 / "main" 主号，扫码成功后凭据写入对应目录
        """
        if account not in ACCOUNT_DIRS:
            raise E.AppError(E.API, f"未知账号：{account}")
        self._close_login()
        self._login_account = account
        ACCOUNT_DIRS[account].mkdir(parents=True, exist_ok=True)
        self._session = DashenSession(ACCOUNT_DIRS[account])
        self._flow = QrLoginFlow(self._session)
        png = self._flow.create_qr()
        return "data:image/png;base64," + base64.b64encode(png).decode("ascii")

    @_wrap
    def poll_login(self):
        """前端每秒调一次：waiting / expired / done。"""
        if not self._flow or not self._session:
            raise E.AppError(E.NEED_LOGIN, "请先申请二维码")
        state = self._flow.poll_once()
        if state is ScanState.EXPIRED:
            self._close_login()
            return "expired"
        if state is not ScanState.CONFIRMED:
            return "waiting"
        self._flow.complete_login()
        self._session.save_session()
        self._close_login()
        # 已有刚存的会话，直接换 token 并写该账号的 credentials.json
        self._service.refresh_token(ACCOUNT_DIRS[self._login_account])
        self._service.remember_account(self._login_account)  # 存一份，以后切回来免扫码
        self._after_switch(self._login_account)
        return "done"

    def _after_switch(self, account: str) -> None:
        if account == "main":
            # 主号换了：举报提醒下次心跳按新凭据重建
            self._watcher.close()

    # ---------- 已保存账号 ----------

    @_wrap
    def saved_accounts(self):
        return self._service.saved_accounts()

    @_wrap
    def switch_account(self, account: str, role_id: str):
        """免扫码切换：account 为 query / main，role_id 为已保存账号的角色 ID。"""
        if account not in ACCOUNT_DIRS:
            raise E.AppError(E.API, f"未知账号：{account}")
        self._service.switch_account(account, str(role_id))
        self._after_switch(account)

    @_wrap
    def delete_saved(self, account: str, role_id: str):
        if account not in ACCOUNT_DIRS:
            raise E.AppError(E.API, f"未知账号：{account}")
        self._service.delete_saved(account, str(role_id))

    @_wrap
    def cancel_login(self):
        """用户在登录页点「返回」：丢弃本次扫码会话，不写任何凭据。"""
        self._close_login()

    def _close_login(self):
        if self._session:
            self._session.close()
        self._session = self._flow = None
