"""用户设置：存 data/settings.json（已被 .gitignore 忽略）。

放界面可调的参数；每个参数都有默认值与上下限，读到非法值时回落到默认。
"""

from __future__ import annotations

import json

from .paths import DATA_DIR

SETTINGS_PATH = DATA_DIR / "settings.json"  # 设置文件

# 参数名 -> (默认值, 下限, 上限)
# 心跳下限 30 秒：项目约定不得调小请求间隔，读的又只是本人工单，没必要更频繁
SPECS: dict[str, tuple[int, int, int]] = {
    "report_heartbeat_sec": (60, 30, 3600),  # 举报提醒心跳间隔（秒）
    "report_window_min": (30, 1, 1440),      # 只提醒多少分钟内的举报
    "report_batch_min": (10, 1, 120),        # 举报时间相差不超过这么多分钟算同一批，一批只提醒一次
    "cli_daily_limit": (3000, 0, 5000),      # CLI 每日请求上限，0 为不限；设置面板可调，后续启动的命令读取新值
}


def _clamp(key: str, value) -> int:
    default, low, high = SPECS[key]
    try:
        return min(max(int(value), low), high)
    except (TypeError, ValueError):
        return default


def load() -> dict:
    """读全部设置，缺项或非法值用默认值补齐。"""
    try:
        raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    return {k: _clamp(k, raw.get(k, spec[0])) for k, spec in SPECS.items()}


def save(changes: dict) -> dict:
    """合并保存，未知参数忽略，超出范围的值会被夹到上下限。返回保存后的完整设置。"""
    current = load()
    for k, v in changes.items():
        if k in SPECS:
            current[k] = _clamp(k, v)
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    return current


def limits() -> dict:
    """给界面显示上下限用。"""
    return {k: {"min": low, "max": high} for k, (_, low, high) in SPECS.items()}
