"""桌面端统一的错误类型。

前身项目把 403 吞成空列表、再包成 400，封号后前端只显示「查无此人」。
这里每种失败都有独立的错误码，一路透传到界面，绝不静默吞掉。
"""

from __future__ import annotations


class AppError(Exception):
    """可预期的业务失败，带一个给界面判断用的错误码。

    Args:
        code: 错误码（见下方常量）
        message: 给人看的说明
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code        # 错误码
        self.message = message  # 说明文字


# ---------- 错误码 ----------
AUTH_EXPIRED = "auth_expired"  # 401：token 过期（可自动续期）
BANNED = "banned"              # 403：账号被风控封禁，换 token 无效
RATE_LIMITED = "rate_limited"  # 429：被限流，必须停下，不自动重试
DAILY_LIMIT = "daily_limit"    # CLI 当日请求数到了 settings 里的上限
NOT_FOUND = "not_found"        # 查无此人 / 未公开战绩
NEED_LOGIN = "need_login"      # 本地没有可用会话，需要扫码
NETWORK = "network"            # 网络层失败（超时、断网）
API = "api"                    # 其他接口层面的失败
