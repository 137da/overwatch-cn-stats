"""跨进程请求守卫：桌面端与 CLI 同时开着时，所有战绩请求排同一个队。

StatsClient 自带的锁和节流只管本进程；agent 调 CLI 是多进程，
不加这一层就可能和界面同时发请求，等于把节流间隔缩短了一半。

- 文件锁（data/request.lock）保证同一时刻只有一个进程在发请求；
- 上次请求时刻记在 data/cli_usage.json，跨进程保证最小间隔 + 抖动（复用 constants 的参数）；
- CLI 的请求按天计数，超过 settings 里的 cli_daily_limit 就拒绝（0 表示不限），界面的请求不计数。
"""

from __future__ import annotations

import json
import random
import sys
import time
from contextlib import contextmanager
from datetime import date

from qr_login import constants as C

from . import errors as E
from .paths import DATA_DIR

LOCK_PATH = DATA_DIR / "request.lock"      # 跨进程文件锁
USAGE_PATH = DATA_DIR / "cli_usage.json"   # 上次请求时刻 + CLI 当日请求数
LOCK_WAIT_SEC = 60                         # 等别的进程释放锁的上限
LOCK_POLL_SEC = 0.05                       # 抢锁失败后的重试间隔
WARN_RATIO = 0.8                           # 当日用量到上限的这个比例时在 stderr 提醒

try:
    import msvcrt

    def _try_lock(f) -> bool:
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _unlock(f) -> None:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
except ImportError:  # 非 Windows
    import fcntl

    def _try_lock(f) -> bool:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def _unlock(f) -> None:
        fcntl.flock(f, fcntl.LOCK_UN)


def read_usage() -> dict:
    """读用量文件；跨天时 count 归零。"""
    try:
        raw = json.loads(USAGE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    today = date.today().isoformat()
    return {
        "date": today,
        "count": int(raw.get("count", 0)) if raw.get("date") == today else 0,  # CLI 当日请求数
        "last_at": float(raw.get("last_at", 0)),                                 # 上次请求的时间戳（秒）
    }


class RequestGuard:
    """每次 HTTP 请求都包在 slot() 里。

    Args:
        counted: 是否计入 CLI 当日用量（CLI 为 True，桌面端为 False）
        daily_limit: 当日请求上限，0 表示不限；counted=False 时不起作用
    """

    def __init__(self, counted: bool = False, daily_limit: int = 0):
        self.counted = counted          # 是否计数
        self.daily_limit = daily_limit  # 每日上限
        self._warned = False            # 本进程已提醒过接近上限

    @contextmanager
    def slot(self):
        """拿到请求资格：抢锁 → 查上限 → 等够间隔 → 发请求 → 记账 → 放锁。"""
        LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOCK_PATH, "a+b") as f:
            f.seek(0)
            deadline = time.monotonic() + LOCK_WAIT_SEC
            while not _try_lock(f):
                if time.monotonic() > deadline:
                    raise E.AppError(E.API, "等待其他进程的请求超时，请稍后再试")
                time.sleep(LOCK_POLL_SEC)
            try:
                usage = read_usage()
                limited = self.counted and self.daily_limit > 0
                if limited and usage["count"] >= self.daily_limit:
                    raise E.AppError(E.DAILY_LIMIT, f"CLI 今日请求已达上限 {self.daily_limit} 次"
                                     "（data/settings.json 的 cli_daily_limit，0 为不限）")
                # 墙上时钟：跨进程不能用 monotonic
                target = usage["last_at"] + (C.MIN_INTERVAL_MS + random.uniform(0, C.JITTER_MS)) / 1000
                delay = target - time.time()
                if delay > 0:
                    time.sleep(delay)
                try:
                    yield
                finally:
                    # 请求发出去就算一次，失败也计数（失败的请求同样打到了服务器）
                    usage["last_at"] = time.time()
                    if self.counted:
                        usage["count"] += 1
                    USAGE_PATH.write_text(json.dumps(usage), encoding="utf-8")
                    if limited and not self._warned and usage["count"] >= self.daily_limit * WARN_RATIO:
                        self._warned = True
                        print(f"[提醒] CLI 今日已用 {usage['count']}/{self.daily_limit} 次请求", file=sys.stderr)
            finally:
                _unlock(f)
