"""举报记录客户端。

用主号的大神会话换取举报系统 token，再读举报记录。
token 有效期 2 小时，缓存到快过期再换，避免每次都走三步。
"""

from __future__ import annotations

import base64
import json
import threading
import time
from urllib.parse import parse_qs, urlparse

import httpx

from qr_login import constants as C
from qr_login.client import DashenSession, Throttle
from qr_login.flow import QrLoginFlow

from . import errors as E

REPORT_REF = "https://support.battlenet.com.cn/ld5/report"  # 举报页地址
SUPPORT_API = "https://support-api.battlenet.com.cn"        # 举报系统接口域
SUPPORT_SITE = "https://support.battlenet.com.cn"           # 举报页站点（Origin / Referer）
TOKEN_REFRESH_MARGIN_SEC = 120                               # 距过期不足 2 分钟就提前换新


def _jwt_exp(token: str) -> float:
    """读出 JWT 的过期时间戳，解析失败按「立即过期」处理。"""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload))["exp"])
    except Exception:
        return 0.0


class ReportClient:
    """读主号的举报记录。

    Args:
        session: 主号的大神会话（已从 state/main/ 载入 cookie）
        bnet_id: 主号的 bnetId（即 role_id）
    """

    def __init__(self, session: DashenSession, bnet_id: int):
        self.session = session
        self.flow = QrLoginFlow(session)
        self.bnet_id = bnet_id
        self._token = ""      # 举报系统 token（JWT）
        self._token_exp = 0.0  # token 过期时间戳
        self._lock = threading.Lock()
        self._throttle = Throttle(C.MIN_INTERVAL_MS, C.JITTER_MS)
        self._http = httpx.Client(headers=dict(C.COMMON_HEADERS), timeout=15)

    def close(self) -> None:
        self._http.close()

    def _refresh_token(self) -> None:
        """①② 用大神会话换举报系统 token。"""
        if not self.flow.is_session_alive():
            raise E.AppError(E.NEED_LOGIN, "主号登录已失效，请重新扫码")
        data = self.session.ds_post(
            f"{C.DS}/sop/specifiedGeneratePassport",
            {"bnetId": self.bnet_id, "mssLoginRefUrl": REPORT_REF},
            use_act=True,
        )
        # result 直接是拼好的 auth/login 完整地址，不是单独的 passport 字段
        login_url = data.get("result")
        if not (isinstance(login_url, str) and login_url.startswith("https://")):
            raise E.AppError(E.API, f"生成举报系统登录地址失败：{data.get('errmsg') or data}")

        self._throttle.wait()
        try:
            resp = self._http.get(login_url, headers={"Referer": C.REFERER_ACT}, follow_redirects=False)
        except httpx.HTTPError as e:
            raise E.AppError(E.NETWORK, f"网络请求失败：{e}") from e
        location = resp.headers.get("location", "")
        token = (parse_qs(urlparse(location).query).get("token") or [""])[0]
        if not token:
            raise E.AppError(E.API, f"举报系统登录失败（{resp.status_code}），没拿到 token")
        self._token = token
        self._token_exp = _jwt_exp(token)

    def fetch_records(self) -> list[dict]:
        """③ 读全部举报记录，按提交时间倒序。"""
        with self._lock:
            if time.time() > self._token_exp - TOKEN_REFRESH_MARGIN_SEC:
                self._refresh_token()
            self._throttle.wait()
            try:
                resp = self._http.post(f"{SUPPORT_API}/sprite/api/ld5/reportRecord/get", headers={
                    "token": self._token, "token-type": "gmsdk", "Accept": C.ACCEPT_JSON,
                    "Origin": SUPPORT_SITE, "Referer": SUPPORT_SITE + "/",
                })
            except httpx.HTTPError as e:
                raise E.AppError(E.NETWORK, f"网络请求失败：{e}") from e

        if resp.status_code == 401:
            self._token_exp = 0.0  # 下次强制换新
            raise E.AppError(E.AUTH_EXPIRED, "举报系统 token 失效，请重试")
        if resp.status_code == 429:
            raise E.AppError(E.RATE_LIMITED, "举报记录查询太频繁，请稍后再试")
        try:
            body = resp.json()
        except ValueError:
            body = {}
        if resp.status_code != 200 or body.get("code") != 200:
            raise E.AppError(E.API, f"读举报记录失败（{resp.status_code}）：{body.get('message') or str(body)[:200]}")
        records = (body.get("data") or {}).get("result") or []
        return sorted(records, key=lambda r: r.get("createTime", ""), reverse=True)


def reported_players(records: list[dict]) -> list[dict]:
    """把举报记录整理成「被举报玩家」列表。

    一次举报勾选多个类别会生成多条记录，按 reported_bnetId 去重，保留最新一次。
    """
    seen: dict[str, dict] = {}
    for rec in records:
        extra = rec.get("extraInfo") or {}
        bnet_id = str(extra.get("reported_bnetId") or "")
        if not bnet_id:
            continue
        kind = "作弊" if rec.get("type", "").endswith("cheating") else "消极比赛"
        if bnet_id in seen:
            if kind not in seen[bnet_id]["kinds"]:
                seen[bnet_id]["kinds"].append(kind)
            continue
        seen[bnet_id] = {
            "bnetId": bnet_id,
            "battleTag": extra.get("reported_role_name") or "",
            "reportTime": rec.get("createTime") or "",
            "kinds": [kind],
            "result": rec.get("result") or "处理中",
        }
    return list(seen.values())
