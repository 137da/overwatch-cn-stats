"""大神战绩接口客户端（datamsapi 域）。

只管 HTTP 与错误分类，不含业务编排。所有请求串行 + 节流，
节流参数复用 qr_login/constants.py，不另起一套。
"""

from __future__ import annotations

import threading
from contextlib import nullcontext
from typing import Any

import httpx

from qr_login import constants as C
from qr_login.client import Throttle

from . import errors as E
from .guard import RequestGuard


class StatsClient:
    """持有查询号凭据，发起战绩查询。

    Args:
        role_id: 查询号的 role_id
        token: 查询号的 bigdata token（GL-Bigdata-Auth-Token）
        guard: 跨进程请求守卫（见 guard.py），为空时只做本进程内的串行与节流
    """

    def __init__(self, role_id: int, token: str, guard: RequestGuard | None = None):
        self.role_id = role_id  # 查询号 role_id
        self.token = token      # 查询号 bigdata token
        self._guard = guard     # 跨进程守卫
        self._throttle = Throttle(C.MIN_INTERVAL_MS, C.JITTER_MS)
        self._lock = threading.Lock()  # 保证全部请求串行，绝不并发
        self._http = httpx.Client(headers=dict(C.COMMON_HEADERS), timeout=15)

    def close(self) -> None:
        self._http.close()

    def _headers(self) -> dict[str, str]:
        return {
            "GL-Bigdata-Auth-Token": self.token,
            "GL-Bigdata-Dts": C.DTS,
            "GL-Bigdata-Role-Id": str(self.role_id),
            "GL-Bigdata-Server": C.SERVER,
            "GL-ClientType": C.GL_CLIENT_TYPE,
            "Origin": C.ORIGIN_ACT,
            "Referer": C.REFERER_ACT,
            "Accept": C.ACCEPT_JSON,
        }

    def _request(self, method: str, path: str, **kwargs) -> Any:
        """发请求并把各种失败翻译成 AppError，成功时返回 data 节点。"""
        with self._lock:
            self._throttle.wait()
            with self._guard.slot() if self._guard else nullcontext():
                try:
                    resp = self._http.request(method, f"{C.DATAMS}/{path}", headers=self._headers(), **kwargs)
                except httpx.HTTPError as e:
                    raise E.AppError(E.NETWORK, f"网络请求失败：{e}") from e

        try:
            body = resp.json()
        except ValueError:
            body = {}
        # 状态码可能出现在 HTTP 层，也可能只出现在响应体的 code 里，两处都要看
        code = resp.status_code if resp.status_code != 200 else body.get("code", 0)
        message = body.get("errMsg") or body.get("message") or ""
        if code == 401:
            raise E.AppError(E.AUTH_EXPIRED, f"查询号 token 已过期：{message}")
        if code == 403:
            raise E.AppError(E.BANNED, f"查询号被限制访问（封禁）：{message}")
        if code == 429:
            raise E.AppError(E.RATE_LIMITED, "请求太频繁被限流，请过几分钟再查")
        if resp.status_code != 200 or code not in (0, 200):
            raise E.AppError(E.API, f"接口返回异常（{code}）：{message or str(body)[:200]}")
        return body.get("data")

    # ---------- 接口 ----------

    def search(self, battle_tag: str) -> dict:
        """按 BattleTag 搜人，返回含 bnetId / customerToken 的资料。"""
        data = self._request("POST", "searchBnetAccount", json={
            "token": self.token, "roleId": self.role_id,
            "dts": C.DTS, "server": C.SERVER, "name": battle_tag,
        })
        if not isinstance(data, dict) or not data.get("customerToken"):
            raise E.AppError(E.NOT_FOUND, f"没找到玩家，或对方未在大神公开战绩：{battle_tag}")
        return data

    def query_card(self, customer_token: str) -> dict:
        """概览卡：名字、等级、时长、头像。"""
        data = self._request("GET", "customer/queryCard", params={"season": "", "token": customer_token})
        if not isinstance(data, dict):
            raise E.AppError(E.NOT_FOUND, "查不到该玩家的资料卡")
        return data

    def query_count_info(self, customer_token: str, season: str = "", game_mode: str = "sport") -> dict:
        """统计：各位置胜率、KDA（sport 竞技另含段位；leisure 快速结构相同但无段位）。"""
        data = self._request("GET", "customer/queryCountInfo",
                             params={"gameMode": game_mode, "season": season, "token": customer_token})
        return data if isinstance(data, dict) else {}

    def query_match_list(self, customer_token: str, game_mode: str = "sport", page: int = 1,
                         season: str = "") -> list:
        """近期对局列表（每页约 12 场）。season 为空表示当前赛季。"""
        data = self._request("GET", "customer/queryMatchList", params={
            "gameMode": game_mode, "page": str(page), "season": season, "token": customer_token})
        return data if isinstance(data, list) else []

    def query_match_info(self, customer_token: str, match_id: str) -> dict:
        """单局记分板。"""
        data = self._request("GET", "customer/queryMatchInfo",
                             params={"matchId": match_id, "token": customer_token})
        return data if isinstance(data, dict) else {}
