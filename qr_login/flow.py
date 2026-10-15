"""扫码登录业务流程。

完整链路：
    出码 → 轮询确认 → 取登录态 cookie → SSO 到 ds 域
         → 取自己绑定的守望先锋角色 → 换 bigdata token
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from . import constants as C
from .client import DashenSession, parse_urs_json


class ScanState(Enum):
    """二维码扫描状态。"""

    WAITING = "waiting"      # 等待扫码 / 已扫待确认
    CONFIRMED = "confirmed"  # 已确认
    EXPIRED = "expired"      # 二维码失效，需重新出码


@dataclass
class OwRole:
    """账号绑定的守望先锋角色。"""

    role_id: int      # 大神侧角色标识
    server: str       # 服务器编号
    nick: str = ""    # 角色昵称


@dataclass
class Credential:
    """最终产物：查询战绩所需的凭据。"""

    role_id: int      # 角色标识
    token: str        # bigdata token（即 GL-Bigdata-Auth-Token）
    server: str = C.SERVER
    nick: str = ""    # 角色昵称
    user_name: str = ""  # 扫码者的网易账号昵称


class QrLoginError(RuntimeError):
    """扫码登录过程中的可预期失败。"""


class QrLoginFlow:
    """驱动一次扫码登录。

    Args:
        session: 承载 cookie 与签名的会话对象
    """

    def __init__(self, session: DashenSession):
        self.session = session
        self._qr_uuid: str = ""          # 当前二维码的 uuid（轮询用）
        self._set_cookie_url: str = ""   # 扫码确认后用于取登录态 cookie 的地址
        self.user_name: str = ""         # 扫码者昵称（轮询确认时返回）

    # ---------- ① 出码 ----------

    def create_qr(self) -> bytes:
        """申请一个登录二维码，返回 PNG 字节。

        上游直接返回图片，无需本地生成二维码。
        """
        resp = self.session.urs_get(
            f"{C.URS_QR}/getqrcodeid", params={"product": C.PRODUCT}
        )
        resp.raise_for_status()
        data = parse_urs_json(resp.text)
        # 响应形如 {"s":"urs","a":"login","l":{"p":"cc_team","i":"<uuid>"}}
        uuid_val = (data.get("l") or {}).get("i") or ""
        if not uuid_val:
            raise QrLoginError(f"未能取得二维码 uuid: {data}")
        self._qr_uuid = uuid_val
        self._set_cookie_url = ""

        png = self.session.urs_get(
            f"{C.URS_QR}/getGeneralUrlQrcode",
            params={
                "uuid": self._qr_uuid,
                "size": "240",
                "format": "png",
                "product": C.PRODUCT,
                "rtid": "",
                "url": C.CALLBACK,
                "url2": C.CALLBACK,
            },
        )
        png.raise_for_status()
        if not png.content:
            raise QrLoginError("二维码图片为空")
        return png.content

    # ---------- ② 轮询 ----------

    def poll_once(self) -> ScanState:
        """轮询一次扫码状态。

        确认时会记下取 cookie 的地址与扫码者昵称，随后调 complete_login()。
        """
        if not self._qr_uuid:
            return ScanState.EXPIRED

        resp = self.session.urs_get(
            f"{C.URS_QR}/qrcodeauth",
            params={
                "product": C.PRODUCT,
                "client": "pc",
                "newQrCode": "1",
                "uuid": self._qr_uuid,
            },
        )
        resp.raise_for_status()
        data = parse_urs_json(resp.text)
        ret_code = str(data.get("retCode", ""))

        if ret_code == C.RET_CONFIRMED:
            # 上游可能给出专用的跨域设置 cookie 地址，缺省时回退到默认端点
            self._set_cookie_url = data.get("crossSetCookieUrl") or f"{C.URS_QR}/qrcodeSetCookie"
            self.user_name = data.get("userName") or ""
            return ScanState.CONFIRMED
        if ret_code in C.RET_EXPIRED:
            return ScanState.EXPIRED
        return ScanState.WAITING  # 含 408 等待中

    # ---------- ③④ 换登录态 cookie 并 SSO ----------

    def complete_login(self) -> None:
        """扫码确认后：取通行证 cookie，再 SSO 到大神域。"""
        if not self._set_cookie_url or not self._qr_uuid:
            raise QrLoginError("尚未完成扫码确认")
        # 上游有时给的是 http://，统一升到 https
        url = self._set_cookie_url.replace("http://", "https://")
        resp = self.session.urs_get(
            url,
            params={
                "product": C.PRODUCT,
                "uuid": self._qr_uuid,
                "url": C.CALLBACK,
                "url2": C.CALLBACK,
            },
        )
        resp.raise_for_status()
        self.sso()

    def sso(self) -> None:
        """登录大神域，换取 GL-XSRF-TOKEN / GOD_UUID / JSESSIONID。"""
        data = self.session.ds_post(f"{C.DS}/base/login", {})
        code = str(data.get("code", ""))
        if code and code != "200":
            raise QrLoginError(f"大神域登录失败: {data}")

    def is_session_alive(self) -> bool:
        """探测当前会话是否仍然有效（用登录接口试一次）。"""
        try:
            self.sso()
            return True
        except Exception:
            return False

    # ---------- ⑤ 取绑定的守望先锋角色 ----------

    def get_own_role(self) -> OwRole | None:
        """取扫码账号自己绑定的守望先锋角色，没有则返回 None。

        这是硬性前提：该网易账号必须已把战网账号绑定到大神，
        否则拿不到 role_id，后续换 token 无从进行。
        """
        data = self.session.ds_post(f"{C.DS}/role-list-query/getBindList", {})
        for role in data.get("result") or []:
            if role.get("appKey") != C.APP_KEY_OW:
                continue
            raw_id = str(role.get("roleId") or "").strip()
            if not raw_id.isdigit():
                continue
            return OwRole(
                role_id=int(raw_id),
                server=str(role.get("server") or C.SERVER),
                nick=str(role.get("nick") or ""),
            )
        return None

    def list_bound_games(self) -> list[str]:
        """列出该账号绑定的所有游戏名，用于在找不到守望先锋时给出有用提示。"""
        try:
            data = self.session.ds_post(f"{C.DS}/role-list-query/getBindList", {})
        except Exception:
            return []
        names = []
        for role in data.get("result") or []:
            name = str(role.get("game") or role.get("appKey") or "").strip()
            if name and name not in names:
                names.append(name)
        return names

    # ---------- ⑥ 换 bigdata token ----------

    def get_report_token(self, role: OwRole) -> str:
        """用角色标识换取查询战绩所需的 bigdata token。"""
        data = self.session.ds_post(
            f"{C.DS}/game/report/getReportToken",
            {
                "appKey": C.APP_KEY_OW,
                "roleId": role.role_id,
                "server": role.server,
                "source": 1,
                "type": "yearly",
            },
        )
        result = data.get("result") or {}
        token = result.get("token")
        if not token:
            raise QrLoginError(f"换取 token 失败: {data}")
        return str(token)

    # ---------- 组合：会话已就绪后取凭据 ----------

    def fetch_credential(self) -> Credential:
        """在会话有效的前提下，取出完整凭据。"""
        role = self.get_own_role()
        if role is None:
            bound = self.list_bound_games()
            hint = ("该账号已绑定：" + "、".join(bound)) if bound else "该账号没有绑定任何游戏角色"
            raise QrLoginError(
                "这个网易账号没有绑定守望先锋（战网）角色，无法取得 role_id。\n"
                f"  {hint}\n"
                "  需要换一个「绑定过战网账号」的网易大神账号来扫码。"
            )
        token = self.get_report_token(role)
        return Credential(
            role_id=role.role_id,
            token=token,
            server=role.server,
            nick=role.nick,
            user_name=self.user_name,
        )
