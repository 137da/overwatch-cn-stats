"""举报提醒：心跳检查主号举报工单，找出最近举报的玩家，方便在游戏里避开。

心跳由前端定时器驱动（间隔见 settings.report_heartbeat_sec），这里每次只读一次记录，不自己起线程。
只读本人已提交的举报工单，不会触发任何新的举报。

分批：提交时间相差不超过 report_batch_min 分钟的举报算同一批（一局里举报几个人通常就是一批），
一批只提醒一次、只自动查其中最新的一人；处理结果存进 report_handled 表，重启后不重复提醒。
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path

from qr_login.client import DashenSession

from . import database as db
from . import errors as E
from . import settings
from .report_api import ReportClient, reported_players
from .service import MAIN_STATE_DIR

TIME_FORMAT = "%Y-%m-%d %H:%M:%S"    # 工单 createTime 格式（北京时间，与本机时区一致）


def _parse_time(text: str) -> datetime | None:
    try:
        return datetime.strptime(text, TIME_FORMAT)
    except (TypeError, ValueError):
        return None


def plan_batches(fresh: list[dict], last_handled: datetime | None, gap_min: int) -> dict | None:
    """给未处理的举报分批，返回需要提醒的那一批里最新的一人（没有新批次返回 None）。

    Args:
        fresh: 未处理的被举报玩家（含 reportTime）
        last_handled: 已处理举报里最晚的提交时间；与它相差不超过 gap_min 的并入上一批，不再提醒
        gap_min: 批次间隔（分钟）
    """
    alert, batch, prev = None, [], last_handled
    for p in sorted(fresh, key=lambda x: x["reportTime"]):
        t = _parse_time(p["reportTime"])
        if prev is None or (t - prev).total_seconds() > gap_min * 60:
            batch = [p]  # 与前一条隔得够久：新的一批
            alert = batch
        elif alert is batch:
            batch.append(p)  # 并入当前这个新批次
        # 否则并入的是已处理过的上一批：静默
        prev = t
    if not alert:
        return None
    # 一次心跳里出现多个新批次（比如刚打开提醒、积压了好几局）时，只提醒最新的那一批
    return {"player": alert[-1], "size": len(alert)}


class ReportWatcher:
    def __init__(self, state_dir: Path = MAIN_STATE_DIR):
        self._state_dir = state_dir
        self._lock = threading.Lock()
        self._session: DashenSession | None = None
        self._client: ReportClient | None = None  # 首次检查时创建

    def _client_or_raise(self) -> ReportClient:
        if self._client is None:
            try:
                cred = json.loads((self._state_dir / "credentials.json").read_text(encoding="utf-8"))
                bnet_id = int(cred["role_id"])
            except (OSError, ValueError, KeyError) as e:
                raise E.AppError(E.NEED_LOGIN, "主号还没登录：先运行 python -m qr_login --profile main") from e
            session = DashenSession(self._state_dir)
            if not session.load_session():
                session.close()
                raise E.AppError(E.NEED_LOGIN, "主号会话不存在：先运行 python -m qr_login --profile main")
            self._session, self._client = session, ReportClient(session, bnet_id)
        return self._client

    def check(self) -> dict:
        """一次心跳：返回时间窗口内的被举报玩家及其处理状态；有新批次时在 alert 里给出要自动查的人。"""
        with self._lock:
            records = self._client_or_raise().fetch_records()
            cfg = settings.load()
            now = datetime.now()

            players = []
            for p in reported_players(records):
                t = _parse_time(p["reportTime"])
                if t is None:
                    continue
                minutes_ago = int((now - t).total_seconds() // 60)
                if 0 <= minutes_ago <= cfg["report_window_min"]:
                    players.append({**p, "minutesAgo": minutes_ago})

            handled = db.handled_reports()
            key = lambda p: (p["bnetId"], p["reportTime"])  # noqa: E731
            fresh = [p for p in players if key(p) not in handled]
            times = [_parse_time(t) for _, t in handled]
            last_handled = max((t for t in times if t), default=None)

            plan = plan_batches(fresh, last_handled, cfg["report_batch_min"])
            chosen = key(plan["player"]) if plan else None
            db.mark_reports_handled([(*key(p), key(p) == chosen) for p in fresh])
            handled = db.handled_reports()
            for p in players:
                p["queried"] = handled[key(p)]["queried"]

            return {
                "checkedAt": now.strftime("%H:%M:%S"),
                "windowMin": cfg["report_window_min"],
                "players": players,
                "alert": {"battleTag": plan["player"]["battleTag"], "bnetId": plan["player"]["bnetId"],
                          "batchSize": plan["size"]} if plan else None,
            }

    def close(self) -> None:
        with self._lock:
            if self._client:
                self._client.close()
            if self._session:
                self._session.close()
            self._client = self._session = None
