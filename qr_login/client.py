"""大神接口的 HTTP 会话层。

职责：
  - 管理 cookie（扫码得到的通行证会话 + SSO 后的 ds 域会话），支持落盘复用
  - 对 ds 域的 POST 计算 gl-checksum 签名
  - 请求节流，避免给上游造成突发压力
"""

from __future__ import annotations

import hashlib
import json
import random
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from . import constants as C


def compact_json(obj: Any) -> str:
    """序列化为紧凑 JSON（无多余空格）。

    签名与请求体必须使用同一个字符串，否则 checksum 与 body 不自洽，
    所以统一从这里出口，调用方不要自己拼 JSON。
    """
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def parse_urs_json(text: str) -> dict:
    """解析通行证接口的响应。

    这些接口的响应首行是内部状态码，不是合法 JSON，
    必须从第一个 '{' 开始截取后再解析。
    """
    i = text.find("{")
    if i < 0:
        raise ValueError(f"响应中找不到 JSON 体: {text[:120]!r}")
    return json.loads(text[i:])


class Throttle:
    """请求节流器：保证相邻请求的最小间隔，并叠加随机抖动。"""

    def __init__(self, min_interval_ms: int, jitter_ms: int):
        self._min_interval = min_interval_ms / 1000.0  # 最小间隔（秒）
        self._jitter = jitter_ms / 1000.0              # 抖动上限（秒）
        self._lock = threading.Lock()
        self._last_at = 0.0                            # 上次请求时刻

    def wait(self) -> None:
        with self._lock:
            target = self._last_at + self._min_interval + random.uniform(0, self._jitter)
            delay = target - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self._last_at = time.monotonic()


class DashenSession:
    """一次大神会话：持有 cookie、设备标识与 HTTP 客户端。

    Args:
        state_dir: 状态目录，存放设备标识与会话 cookie
    """

    def __init__(self, state_dir: Path):
        self.state_dir = Path(state_dir)                         # 状态目录
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._device_file = self.state_dir / "device.txt"         # 设备标识
        self._session_file = self.state_dir / "session.json"      # 会话 cookie
        self.device_id = self._load_or_create_device_id()        # 本机设备标识
        self._throttle = Throttle(C.MIN_INTERVAL_MS, C.JITTER_MS)
        self._http = httpx.Client(
            headers=dict(C.COMMON_HEADERS),
            timeout=C.HTTP_TIMEOUT_SEC,
            follow_redirects=True,
        )

    # ---------- 生命周期 ----------

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "DashenSession":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---------- 设备标识 ----------

    def _load_or_create_device_id(self) -> str:
        """读取本机设备标识，不存在则生成并落盘。

        这个值保持稳定：同一台机器始终用同一个标识，不每次随机。
        """
        try:
            if self._device_file.exists():
                saved = self._device_file.read_text(encoding="utf-8").strip()
                if saved:
                    return saved
            new_id = str(uuid.uuid4())
            self._device_file.write_text(new_id, encoding="utf-8")
            return new_id
        except OSError:
            return str(uuid.uuid4())  # 落盘失败不致命，退化为本次进程内固定

    # ---------- cookie 存取 ----------

    def cookie(self, name: str) -> str:
        """按名取 cookie 值，取不到返回空串。"""
        for c in self._http.cookies.jar:
            if c.name.lower() == name.lower():
                return c.value or ""
        return ""

    def save_session(self) -> Path:
        """把当前 cookie 落盘，供下次免扫码复用。"""
        items = [
            {"name": c.name, "value": c.value, "domain": c.domain, "path": c.path or "/"}
            for c in self._http.cookies.jar
        ]
        self._session_file.write_text(
            json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return self._session_file

    def load_session(self) -> bool:
        """从磁盘恢复 cookie。无缓存返回 False（不校验是否过期）。"""
        if not self._session_file.exists():
            return False
        try:
            items = json.loads(self._session_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        for it in items:
            try:
                self._http.cookies.set(
                    it["name"], it["value"],
                    domain=it.get("domain", ""), path=it.get("path", "/"),
                )
            except Exception:
                continue  # 个别 cookie 格式异常则跳过
        return bool(items)

    def clear_session(self) -> None:
        """清除会话（登出）。"""
        self._http.cookies.clear()
        self._session_file.unlink(missing_ok=True)

    # ---------- 请求 ----------

    def _gl_headers(self, use_act: bool) -> dict[str, str]:
        """组装 ds 域接口要求的会话头。

        Args:
            use_act: True 用 act 页来源（customer/* 等），False 用 owCard 页来源（登录等）
        """
        return {
            "Accept": C.ACCEPT_JSON,
            "gl-clienttype": C.GL_CLIENT_TYPE,
            "gl-deviceid": self.device_id,
            "gl-uid": self.cookie("GOD_UUID"),
            "gl-x-xsrf-token": self.cookie("GL-XSRF-TOKEN"),
            "Origin": C.ORIGIN_ACT if use_act else C.ORIGIN_CC,
            "Referer": C.REFERER_ACT if use_act else C.REFERER_CC,
        }

    def _checksum(self, body: str) -> str:
        """计算 gl-checksum = sha1(请求体 + GL-XSRF-TOKEN)。"""
        raw = body + self.cookie("GL-XSRF-TOKEN")
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def urs_get(self, url: str, params: dict | None = None) -> httpx.Response:
        """通行证域的 GET（扫码阶段用，此时尚无 ds 域会话，不带 gl-* 头）。"""
        self._throttle.wait()
        return self._http.get(url, params=params)

    def ds_post(self, url: str, payload: Any = None, use_act: bool = False) -> dict:
        """ds 域的带签名 POST，返回解析后的 JSON。"""
        body = compact_json(payload if payload is not None else {})
        headers = self._gl_headers(use_act)
        headers["gl-checksum"] = self._checksum(body)
        headers["Content-Type"] = "application/json"
        self._throttle.wait()
        resp = self._http.post(url, content=body.encode("utf-8"), headers=headers)
        resp.raise_for_status()
        return resp.json()
