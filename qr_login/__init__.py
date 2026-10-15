"""网易大神扫码登录：取得查询战绩所需的 role_id 与 token。

用法见 README.md。
"""

from .client import DashenSession
from .flow import QrLoginFlow, ScanState

__all__ = ["DashenSession", "QrLoginFlow", "ScanState"]
