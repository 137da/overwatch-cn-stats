"""业务编排：读凭据、查玩家、整形给界面用的数据、token 续期。

只用查询号（state/ 根目录）。HTTP 细节在 stats_api 里，这里不碰。
"""

from __future__ import annotations

import base64
import json
import threading
import time
from datetime import date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path

from qr_login.client import DashenSession
from qr_login.flow import QrLoginError, QrLoginFlow

from . import accounts, paths
from . import database as db
from . import errors as E
from .guard import RequestGuard
from .stats_api import StatsClient

STATE_DIR = paths.STATE_DIR          # 查询号状态目录
MAIN_STATE_DIR = STATE_DIR / "main"   # 主号状态目录（qr_login --profile main）
SAVED_DIR = STATE_DIR / "saved"       # 已保存账号，按槽位分开：saved/query/、saved/main/
ACCOUNT_DIRS = {"query": STATE_DIR, "main": MAIN_STATE_DIR}  # 界面上的账号槽位 -> 目录
MAP_DIR = Path(__file__).resolve().parent / "mappings"        # 映射文件目录

RANK_NAMES = {
    "Bronze": "青铜", "Silver": "白银", "Gold": "黄金", "Platinum": "铂金", "Diamond": "钻石",
    "Master": "大师", "Grandmaster": "宗师", "Champion": "英杰", "Emerald": "翡翠",
}
ROLE_NAMES = {"tank": "坦克", "dps": "输出", "healer": "辅助", "open": "开放职责"}
MODE_NAMES = {"SportPreset": "排位", "SportOpen": "开放排位", "LeisurePreset": "快速", "Leisure6v6": "6v6快速",
              "QuickPlay": "快速", "SportFight": "竞技"}  # 接口 gameMode -> 界面名称，未收录的原样显示
MODE_QUERY = {"all": ["sport", "leisure"], "sport": ["sport"], "quick": ["leisure"]}  # 界面模式 -> 接口 gameMode
MATCH_PAGE_SIZE = 12      # 对局接口每页场数，不满一页说明到底了
MAX_PAGES_PER_CALL = 2    # 一次最多连读几页，控制单次查询的请求数


def _load_map(name: str) -> dict:
    return json.loads((MAP_DIR / name).read_text(encoding="utf-8"))


HEROES = _load_map("hero_mapping.json")        # heroGuid -> {name, role}
HERO_ATTRS = _load_map("hero_attr_mapping.json")  # 英雄属性 guid -> 中文名
MAPS = _load_map("map_mapping.json")              # 地图 guid -> 中文名
SEASONS = _load_map("season_mapping.json")        # 赛季号 -> 名称
SEASON_STARTS = _load_map("season_start_mapping.json")  # 赛季号 -> 开始日期（只记了赛年制以来的，更早的网易已清理）
CURRENT_SEASON = max(SEASONS, key=int)            # 映射里最新的赛季当作当前赛季
PAST_SEASONS = sorted((k for k in SEASONS if int(k) < int(CURRENT_SEASON)), key=int, reverse=True)  # 往前的赛季，新的在前
RANK_FALLBACK_SEASONS = 3  # 当前赛季没段位时最多往前补查几个赛季（连当前共 4 个）
ROLE_ORDER = {"tank": 0, "dps": 1, "healer": 2, "open": 3}  # 段位卡片的显示顺序
FIND_MAX_PAGES = 30        # 找对局时每个赛季、每个模式最多往前翻几页（每页 1 次请求）
FIND_MAX_DETAILS = 100     # 一次找对局最多比对几场详情（每场 1 次请求）；硬上限，界面确认、CLI 参数都越不过


def hero_name(guid) -> str:
    return (HEROES.get(str(guid)) or {}).get("name") or ""


def _rank(info: dict) -> dict:
    raw = (info or {}).get("rankName", "")
    if raw == "None":  # 定级赛没打完时接口给字符串 "None"，按未定级处理
        raw = ""
    return {
        "rankName": RANK_NAMES.get(raw, raw),
        "rankTier": (info or {}).get("rankSubTier", 0),
        "rankScore": (info or {}).get("rankScore", 0),
    }


CRED_STALE_HOURS = 12  # 凭据换取超过这么久就提示「较旧」


def _mask(name: str) -> str:
    """账号打码：保留前 2 位与 @ 后缀，例如 ab***@163.com。"""
    local, at, domain = (name or "").partition("@")
    return (local[:2] + "***" + at + domain) if local else ""


def account_info(state_dir: Path, label: str) -> dict:
    """读某个账号的凭据摘要给界面展示。不返回 token。"""
    info = {"label": label, "loggedIn": False}
    try:
        cred = json.loads((state_dir / "credentials.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return info
    fetched = cred.get("fetched_at", "")
    try:
        age_h = (datetime.now() - datetime.strptime(fetched, "%Y-%m-%d %H:%M:%S")).total_seconds() / 3600
    except ValueError:
        age_h = None
    return {
        **info, "loggedIn": True,
        "nick": cred.get("nick") or "", "userName": _mask(cred.get("user_name") or ""),
        "roleId": str(cred.get("role_id") or ""), "fetchedAt": fetched,
        "stale": age_h is None or age_h > CRED_STALE_HOURS,
    }


def _rank_entry(role: dict, season_id: str, past: bool) -> dict:
    """guideCountData 的一项整理成段位卡片数据。

    lastRankInfo 是该赛季的最后段位；season 标明是哪个赛季，past 表示是往前补查到的。
    """
    role_type = role.get("roleType", "")
    return {
        "roleType": role_type, "roleName": ROLE_NAMES.get(role_type, role_type),
        **_rank(role.get("lastRankInfo")),
        "matchSum": role.get("matchSum", 0),
        "winRate": role.get("winRate", "0"), "kda": role.get("kda", "0"),
        "maxRank": _rank(role.get("maxRankInfo")),       # 该赛季最高段位
        "maxWinStreak": role.get("maxWinStreak", 0),     # 最长连胜
        # 当前赛季标「本赛季」：赛季映射要手动更新，过时了也不会标错
        "season": "本赛季" if season_id == "" else SEASONS.get(season_id, f"赛季{season_id}"),
        "past": past,
    }


TOP_HEROES = 5  # 资料卡显示几个常用英雄


def _overview(counts: dict) -> dict:
    """queryCountInfo 里段位之外的概览：赛季战绩、近期主玩职责、常用英雄。随段位一起返回，不多发请求。

    summary 为空表示该赛季没打竞技；recent 来自官方「近 12 场最常用职责」统计。
    """
    s = counts.get("presetsSummaryData") or {}
    r = counts.get("recentMatchCount") or {}
    return {
        "summary": {
            "matchSum": s.get("matchSum", 0), "winSum": s.get("matchWinSum", 0),
            "lossSum": s.get("matchLossSum", 0), "winRate": s.get("winRate", "0"),
        } if s.get("matchSum") else None,
        "recent": {
            "roleName": ROLE_NAMES.get(r.get("roleType", ""), r.get("roleType", "")),
            "winRate": r.get("winRate", "0"), "aveKill": r.get("aveKill", 0),
            "aveDamage": r.get("aveDamage", 0), "aveCure": r.get("aveCure", 0),
        } if r.get("roleType") else None,
        "heroes": [n for n in (hero_name(g) for g in counts.get("frequentHeroIds") or []) if n][:TOP_HEROES],
    }


# 记分板每人的数据项（接口原名）
PLAYER_STATS = ("kill", "assist", "death", "heroDamage", "cure", "resistDamage",
                "damageTaken", "healingTaken", "finalHit", "targetCompetingTime")
BEST_FLAGS = {"kill": "killMax", "heroDamage": "heroDamageMax", "cure": "cureMax",
              "resistDamage": "resistDamageMax"}  # 数据项 -> 接口里「全场最高」标记字段


def _best_flags(m: dict) -> list[str]:
    """该玩家哪几项是全场最高，例如 ["kill", "heroDamage"]。"""
    return [k for k, flag in BEST_FLAGS.items() if m.get(flag)]


def _assign_parties(players: list[dict]) -> list[dict]:
    """按 friendBnetIds 把同队开黑的玩家分组：partyIndex 1、2…，单排为 0。保持原顺序。"""
    index = {p["bnetId"]: i for i, p in enumerate(players) if p.get("bnetId")}
    parent = list(range(len(players)))  # 并查集

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, p in enumerate(players):
        for fid in p.get("friendBnetIds") or []:
            if fid in index:
                parent[find(i)] = find(index[fid])

    sizes: dict[int, int] = {}
    for i in range(len(players)):
        sizes[find(i)] = sizes.get(find(i), 0) + 1
    party_of: dict[int, int] = {}
    for i, p in enumerate(players):
        root = find(i)
        if sizes[root] >= 2 and root not in party_of:
            party_of[root] = len(party_of) + 1
        p["partyIndex"] = party_of.get(root, 0)
    return players


def _token_parts(token: str) -> dict:
    """customerToken = base64("sign=..&bnetId=..&timestamp=毫秒")，解析不了返回空字典。"""
    try:
        return dict(kv.split("=", 1) for kv in base64.b64decode(token).decode().split("&"))
    except (ValueError, UnicodeDecodeError):
        return {}


def _fresh_token(token: str) -> str:
    """把缓存的 customerToken 换上当前时间戳。

    实测时间戳旧了（约一个月前）接口会报查不到人，
    sign 与 bnetId 不变、只换时间戳即可继续用，省掉一次搜索。解析不了就原样返回。
    """
    parts = _token_parts(token)
    if "sign" not in parts or "bnetId" not in parts:
        return token
    sign, bnet_id = parts["sign"], parts["bnetId"]
    raw = f"sign={sign}&bnetId={bnet_id}&timestamp={int(time.time() * 1000)}"
    return base64.b64encode(raw.encode()).decode()


MS_THRESHOLD = 10 ** 12  # 时间戳大于它按毫秒算，否则按秒算


def _fmt_time(ts, fmt: str = "%m-%d %H:%M") -> str:
    """接口时间戳单位不统一：战绩列表 beginTs 是毫秒，对局详情 startTime 是秒，按数量级自动区分。"""
    try:
        ts = int(ts)
        return datetime.fromtimestamp(ts / 1000 if ts > MS_THRESHOLD else ts).strftime(fmt)
    except (TypeError, ValueError, OSError):
        return ""


def _match_row(m: dict) -> dict:
    """对局列表接口的一场 -> 界面用的一行。"""
    return {
        "matchId": m.get("matchId", ""),
        "result": "win" if m.get("matchRet") == 1 else "loss",
        "gameMode": m.get("gameMode", ""),
        "gameModeName": MODE_NAMES.get(m.get("gameMode", ""), m.get("gameMode", "")),
        "roleName": ROLE_NAMES.get(m.get("roleType", ""), m.get("roleType", "")),
        "heroGuid": str(m.get("heroGuid", "")), "heroName": hero_name(m.get("heroGuid", "")),
        "teamScore": m.get("teamScore", 0), "opponentScore": m.get("opponentScore", 0),
        "kill": m.get("kill", 0), "assist": m.get("assist", 0), "death": m.get("death", 0),
        "heroDamage": m.get("heroDamage", 0), "cure": m.get("cure", 0),
        "resistDamage": m.get("resistDamage", 0),
        "mapName": MAPS.get(str(m.get("mapGuid", "")), ""),
        "best": _best_flags(m),
        "matchTime": _fmt_time(m.get("beginTs")),
    }


def _season_starts() -> list[tuple[date, str]]:
    return sorted((date.fromisoformat(v), k) for k, v in SEASON_STARTS.items())


def _purged_error(day: date) -> E.AppError:
    first_day, first = _season_starts()[0]
    return E.AppError(E.NOT_FOUND, f"{day} 早于 {SEASONS.get(first, first)}（{first_day} 开始），"
                                   "网易只保留最近几个赛季，那时的数据已被清理")


def season_start_of(day: date) -> date:
    """日期所在赛季的第一天；早于有记录的第一个赛季时报错。"""
    starts = [d for d, _ in _season_starts() if d <= day]
    if not starts:
        raise _purged_error(day)
    return starts[-1]


def seasons_for_range(d0: date, d1: date) -> list[str]:
    """和 [d0, d1] 有交集的赛季号，新的在前。

    赛季开始当天、开服前打的局还算上个赛季，所以每个赛季的范围算到下个赛季开始那天（含）。
    """
    starts = _season_starts()
    out = []
    for i, (begin, sid) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else date.max
        if begin <= d1 and end >= d0:
            out.append(sid)
    if not out:
        raise _purged_error(d1)
    return out[::-1]


def _hm(text: str) -> dtime | None:
    return dtime.fromisoformat(text.strip()) if text.strip() else None


def _day_minutes(ts_ms: int) -> int:
    t = datetime.fromtimestamp(ts_ms / 1000)
    return t.hour * 60 + t.minute


class FindRange:
    """找对局的时间条件：起止日期（都可空）+ 每天的时段（可空，可跨零点）。

    - 都不填：当前赛季第一天到今天；只填开始：到今天；只填结束：从结束日期所在赛季的第一天起。
    - 时段 23:00~01:00 表示每天 23 点到次日 1 点，归属前一天：所以起点从开始日期的 23 点算，
      终点延到结束日期次日的 1 点。
    """

    def __init__(self, date_from: str = "", date_to: str = "", time_from: str = "", time_to: str = ""):
        try:
            d1 = date.fromisoformat(date_to.strip()) if date_to.strip() else date.today()
            d0 = date.fromisoformat(date_from.strip()) if date_from.strip() else season_start_of(d1)
            t0, t1 = _hm(time_from), _hm(time_to)
        except ValueError as e:
            raise E.AppError(E.API, "日期格式应为 YYYY-MM-DD，时间为 HH:MM") from e
        if d0 > d1:
            raise E.AppError(E.API, "开始日期晚于结束日期")
        first_day = _season_starts()[0][0]
        if d1 < first_day:
            raise _purged_error(d1)
        self.clipped = d0 < first_day  # 开始日期早于 Y2026S1，那一段已被清理、跳过
        self.d0, self.d1 = max(d0, first_day), d1
        self.seasons = seasons_for_range(self.d0, self.d1)
        if not date_from.strip():  # 开始日期取的是赛季第一天：意思就是「只查这一季」，不连带上一季
            self.seasons = [sid for sid in self.seasons if date.fromisoformat(SEASON_STARTS[sid]) >= self.d0]
        self.lo = t0.hour * 60 + t0.minute if t0 else 0           # 每天时段起点（分钟）
        self.hi = t1.hour * 60 + t1.minute if t1 else 24 * 60 - 1  # 每天时段终点（分钟，含）
        self.wraps = self.lo > self.hi                               # 跨零点
        start = datetime.combine(self.d0, dtime(self.lo // 60, self.lo % 60))
        end_day = self.d1 + timedelta(days=1) if self.wraps else self.d1
        end = datetime.combine(end_day, dtime(self.hi // 60, self.hi % 60))
        self.start_ms = int(start.timestamp() * 1000)
        self.end_ms = int(end.timestamp() * 1000) + 59_999  # 结束那一分钟整分钟都算

    def contains(self, ts_ms: int) -> bool:
        if not self.start_ms <= ts_ms <= self.end_ms:
            return False
        m = _day_minutes(ts_ms)
        return (m >= self.lo or m <= self.hi) if self.wraps else self.lo <= m <= self.hi


def _keywords(text) -> list[str]:
    """「奶龙, 小明，ABC」-> ["奶龙", "小明", "abc"]：中英文逗号、空格都能分隔，比较时不分大小写。"""
    if isinstance(text, (list, tuple)):
        text = ",".join(map(str, text))
    return [w.strip().lower() for w in str(text or "").replace("，", ",").replace(" ", ",").split(",") if w.strip()]


def match_hits(detail: dict, names: list[str], heroes: list[str]) -> list[str] | None:
    """对局详情是否满足「同局有这些人 / 这些英雄」：每个关键字都要在双方全部玩家里找到（包含即可）。

    满足返回命中的玩家描述（如 "奶龙#123(源氏)"），不满足返回 None。
    英雄按记分板上每人显示的那一个比对（中途换的英雄接口不给）。
    """
    players = detail.get("teammates", []) + detail.get("enemies", [])
    hits: list[str] = []
    for words, key in ((names, "name"), (heroes, "heroName")):
        for w in words:
            found = [p for p in players if w in (p.get(key) or "").lower()]
            if not found:
                return None
            hits += [f'{p["name"]}({p["heroName"] or "未知英雄"})' for p in found]
    return list(dict.fromkeys(hits))  # 去重保序


class Service:
    """Args:
        guard: 跨进程请求守卫，桌面端与 CLI 各传自己的（CLI 计数、界面不计数）
    """

    def __init__(self, guard: RequestGuard | None = None):
        self._lock = threading.RLock()
        self._guard = guard  # 跨进程请求守卫
        self._client: StatsClient | None = None  # 查询号客户端，首次使用时创建
        db.init_db()

    # ---------- 凭据 ----------

    def _credential_path(self) -> Path:
        return STATE_DIR / "credentials.json"

    def _client_or_raise(self) -> StatsClient:
        """取查询号客户端，没有凭据则报 NEED_LOGIN。"""
        with self._lock:
            if self._client is None:
                try:
                    cred = json.loads(self._credential_path().read_text(encoding="utf-8"))
                    self._client = StatsClient(int(cred["role_id"]), str(cred["token"]), self._guard)
                except (OSError, ValueError, KeyError) as e:
                    raise E.AppError(E.NEED_LOGIN, "还没有登录，请先扫码") from e
            return self._client

    def has_credential(self) -> bool:
        try:
            self._client_or_raise()
            return True
        except E.AppError:
            return False

    def refresh_token(self, state_dir: Path = STATE_DIR) -> None:
        """用已存会话免扫码换新 token，并回写该账号目录下的 credentials.json。失败说明要重新扫码。

        Args:
            state_dir: 账号状态目录，默认查询号（state/），主号为 state/main/
        """
        with self._lock:
            with DashenSession(state_dir) as session:
                flow = QrLoginFlow(session)
                if not session.load_session() or not flow.is_session_alive():
                    raise E.AppError(E.NEED_LOGIN, "登录已过期，请重新扫码")
                try:
                    cred = flow.fetch_credential()
                except QrLoginError as e:
                    raise E.AppError(E.NEED_LOGIN, str(e)) from e
                session.save_session()
            (state_dir / "credentials.json").write_text(json.dumps({
                "role_id": cred.role_id, "token": cred.token, "server": cred.server,
                "nick": cred.nick, "user_name": cred.user_name,
                "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            if state_dir != STATE_DIR:
                return  # 换的是别的账号，查询号客户端不受影响
            if self._client:
                self._client.close()
            self._client = None  # 下次使用时按新凭据重建

    def _call(self, fn):
        """执行一次战绩查询；token 过期则自动续期并重试一次，其余错误原样抛出。"""
        try:
            return fn(self._client_or_raise())
        except E.AppError as e:
            if e.code != E.AUTH_EXPIRED:
                raise
            self.refresh_token()
            return fn(self._client_or_raise())

    # ---------- 已保存账号（切号免扫码） ----------

    def remember_account(self, kind: str) -> None:
        """把槽位里的当前账号备份到该槽位自己的已保存列表，扫码登录成功后调用。"""
        accounts.backup(ACCOUNT_DIRS[kind], SAVED_DIR / kind)

    def saved_accounts(self) -> dict:
        """各槽位已保存的账号（查询号、主号分开），并标出哪个是槽位当前在用的。"""
        result = {}
        for kind, slot in ACCOUNT_DIRS.items():
            active = accounts.role_id_of(slot)
            result[kind] = [{**account_info(d, ""), "active": d.name == active}
                            for d in accounts.list_saved(SAVED_DIR / kind)]
        return result

    def switch_account(self, kind: str, role_id: str) -> None:
        """把已保存的账号换进槽位，用其会话免扫码换新 token。

        会话已过期则回滚到原账号并报 NEED_LOGIN，槽位不会停在一个不能用的账号上。
        """
        slot, saved_root = ACCOUNT_DIRS[kind], SAVED_DIR / kind
        with self._lock:
            previous = accounts.backup(slot, saved_root)  # 先把当前账号存好，换走也不丢
            if previous == str(role_id):
                return
            try:
                accounts.restore(saved_root, str(role_id), slot)
            except (FileNotFoundError, ValueError) as e:
                raise E.AppError(E.API, str(e)) from e
            try:
                self.refresh_token(slot)
            except E.AppError as e:
                if previous:
                    accounts.restore(saved_root, previous, slot)
                else:
                    for name in accounts.ACCOUNT_FILES:
                        (slot / name).unlink(missing_ok=True)
                if e.code == E.NEED_LOGIN:
                    raise E.AppError(E.NEED_LOGIN, "这个账号的登录已过期，需要重新扫码") from e
                raise
            accounts.backup(slot, saved_root)  # 新 token 同步回备份

    def delete_saved(self, kind: str, role_id: str) -> None:
        """删除某槽位下的一个已保存账号；该槽位正在使用的不能删，先切走。"""
        role_id = str(role_id)
        if role_id == accounts.role_id_of(ACCOUNT_DIRS[kind]):
            raise E.AppError(E.API, "这个账号正在使用中，先切换到别的账号再删除")
        try:
            accounts.delete(SAVED_DIR / kind, role_id)
        except (OSError, ValueError) as e:
            raise E.AppError(E.API, f"删除失败：{e}") from e

    # ---------- 查询（返回结构与旧网站接口一致，前端原样复用） ----------

    def _resolve(self, battle_tag: str, token_hint: str) -> tuple[str, str, dict]:
        """拿到玩家的 customerToken、bnetId 与资料卡。

        优先用传入或本地缓存的 token 直连，省掉一次搜索；缓存失效（查不到人）时再走搜索。
        封禁、限流、过期这类错误原样抛出，不拿搜索去重试。
        """
        cached = db.find_player(battle_tag) or {}
        token = _fresh_token(token_hint or cached.get("customerToken") or "")
        if token:
            try:
                return token, cached.get("bnetId", ""), self._call(lambda c: c.query_card(token))
            except E.AppError as e:
                if e.code not in (E.NOT_FOUND, E.API):
                    raise
        found = self._call(lambda c: c.search(battle_tag))
        token = found["customerToken"]
        return token, str(found.get("bnetId", "")), self._call(lambda c: c.query_card(token))

    def get_player(self, battle_tag: str, mode: str = "all", page: int = 1, limit: int = 2,
                   season: str = "", customer_token: str = "", stats: bool = True) -> dict:
        """资料卡 + 各位置段位 + 对局列表（按页）。

        Args:
            mode: all 全部 / sport 竞技 / quick 快速
            page: 起始页（接口每页约 12 场）
            limit: 本次连读几页，最多 MAX_PAGES_PER_CALL
            season: 赛季号，空为当前赛季
            stats: 是否要资料卡与统计（竞技 + 快速）。切模式、加载更多时前端只换对局列表，
                传 False 并带上 customer_token，省掉 queryCard 与两次 queryCountInfo
        """
        battle_tag = battle_tag.strip()
        if "#" not in battle_tag:
            raise E.AppError(E.NOT_FOUND, "请输入完整的 名字#编号，例如 玩家#12345")
        modes = MODE_QUERY.get(mode, MODE_QUERY["all"])
        page = max(1, int(page))
        limit = min(max(1, int(limit)), MAX_PAGES_PER_CALL)
        season = str(season or "")
        if season and not season.isdigit():
            raise E.AppError(E.API, f"赛季参数不合法：{season}")

        if not stats and customer_token:
            token, bnet_id = customer_token, (db.find_player(battle_tag) or {}).get("bnetId", "")
        else:
            stats = True
            token, bnet_id, card = self._resolve(battle_tag, customer_token)
            counts = self._call(lambda c: c.query_count_info(token, season))
            quick = self._call(lambda c: c.query_count_info(token, season, "leisure"))

        raw_matches, has_more = [], False
        for game_mode in modes:
            for pg in range(page, page + limit):
                batch = self._call(lambda c: c.query_match_list(token, game_mode, pg, season))
                raw_matches += batch
                if len(batch) < MATCH_PAGE_SIZE:
                    break
            else:
                has_more = True  # 最后一页是满的，后面可能还有
        raw_matches.sort(key=lambda m: m.get("beginTs", 0), reverse=True)

        if bnet_id:
            db.save_player(bnet_id, battle_tag, token)
            db.save_matches(bnet_id, raw_matches)

        matches = [_match_row(m) for m in raw_matches]
        result = {
            "customerToken": token,
            "recentMatches": matches,
            "pagination": {"currentPage": page, "requestedPages": limit,
                           "totalReturned": len(matches), "hasMore": has_more},
            "season": {"current": season or CURRENT_SEASON,
                       "name": SEASONS.get(season or CURRENT_SEASON, f"赛季{season}")},
        }
        if not stats:
            return result

        ranks: dict[str, dict] = {}
        shown_season = season  # 本次展示的赛季号，空为当前赛季
        for role in counts.get("guideCountData", []):
            entry = _rank_entry(role, shown_season, past=False)
            ranks[entry["roleType"]] = entry
            # 历史赛季的段位不写库，避免被当成当前段位
            if bnet_id and not season:
                db.save_rank(bnet_id, entry["roleType"], entry["rankName"], entry["rankTier"],
                             entry["rankScore"], entry["matchSum"], float(entry["winRate"] or 0),
                             float(entry["kda"] or 0))
        # 只在看「当前赛季」时往前补；手动选了某个赛季就只显示那个赛季，不混数据
        if not season:
            self._fill_past_ranks(token, ranks)
        # 快速没有段位，只把各位置胜率挂到段位卡上；只打快速的位置也补一张卡
        for role in quick.get("guideCountData", []):
            rt = role.get("roleType", "")
            entry = ranks.setdefault(rt, _rank_entry({"roleType": rt}, shown_season, past=False))
            entry["quick"] = {"matchSum": role.get("matchSum", 0), "winRate": role.get("winRate", "0"),
                              "kda": role.get("kda", "0")}
        ranks = sorted(ranks.values(), key=lambda r: ROLE_ORDER.get(r["roleType"], 9))

        return {
            **result,
            "profile": {"name": card.get("name") or battle_tag, "level": card.get("level", 0),
                        "gameTime": card.get("gameTime", "0"), "icon": card.get("icon", ""),
                        "title": card.get("title") or ""},
            "overview": {"sport": _overview(counts), "quick": _overview(quick)},
            "ranks": ranks,
        }

    def _fill_past_ranks(self, token: str, ranks: dict[str, dict]) -> None:
        """当前赛季一个段位都没有时，往前最多 RANK_FALLBACK_SEASONS 个赛季，取第一个有段位的赛季整体展示。

        当前赛季只要有任一位置有段位就不补查（不按位置逐个补：多数人不会四个位置都打，
        逐个补几乎每次都要多查 3 个赛季）。补查失败（查不到 / 接口异常）只放弃补查，
        封禁、限流、过期照常抛出。
        """
        if any(r["rankName"] for r in ranks.values()):
            return
        for sid in PAST_SEASONS[:RANK_FALLBACK_SEASONS]:
            try:
                data = self._call(lambda c: c.query_count_info(token, sid))
            except E.AppError as e:
                if e.code in (E.NOT_FOUND, E.API):
                    return
                raise
            found = [e for e in (_rank_entry(r, sid, past=True) for r in data.get("guideCountData", []))
                     if e["rankName"]]
            if found:
                ranks.clear()
                ranks.update({e["roleType"]: e for e in found})
                return

    def match_detail(self, match_id: str, customer_token: str) -> dict:
        """单局详情：地图、时长、双方阵容（含组队分组）、英雄数据。

        先读本地缓存（按对局 + 查看者），没有才发请求；只缓存有阵容的完整响应。
        """
        viewer = _token_parts(customer_token).get("bnetId", "")  # 详情的视角是 token 对应的玩家
        data = db.cached_match_detail(match_id, viewer) if viewer else None
        cached = data is not None
        if not cached:
            data = self._call(lambda c: c.query_match_info(customer_token, match_id))
            if viewer and (data.get("teammateList") or data.get("enemyList")):
                db.save_match_detail(match_id, viewer, data)

        heroes = []
        for hero in data.get("heroList") or []:
            guid = str(hero.get("heroGuid") or hero.get("heroId") or "")  # heroList 里常常只有 heroId
            stats = hero.get("statMap") if isinstance(hero.get("statMap"), dict) else {}
            heroes.append({
                "heroId": hero.get("heroId", ""), "heroGuid": guid, "heroName": hero_name(guid),
                "userTimeSec": hero.get("userTimeSec", 0), "useTimeRate": hero.get("useTimeRate", ""),
                "statMap": {HERO_ATTRS.get(str(k), str(k)): v for k, v in stats.items()},
            })

        def player(p: dict) -> dict:
            guid = p.get("heroGuid", "")
            return {
                "name": p.get("name") or p.get("userName") or "", "bnetId": p.get("bnetId", ""),
                "heroGuid": guid, "heroName": hero_name(guid), **_rank(p.get("rankInfo")),
                "friendBnetIds": p.get("friendBnetIds") or [],
                **{k: p.get(k, 0) for k in PLAYER_STATS},
                "best": _best_flags(p),
            }

        team_raw, enemy_raw = data.get("teammateList") or [], data.get("enemyList") or []
        if not cached:  # 缓存命中时不再刷玩家库的「最近出现」时间
            db.save_match_players(team_raw + enemy_raw)

        map_guid = str(data.get("mapGuid", ""))
        start = data.get("startTime")
        return {
            "matchId": match_id, "mapGuid": map_guid, "mapName": MAPS.get(map_guid, ""),
            "startTime": _fmt_time(start, "%Y-%m-%d %H:%M") if start else "",
            "gameTimeSec": data.get("gameTimeSec", 0),
            "result": "win" if data.get("matchRet") == 1 else "loss" if data.get("matchRet") == -1 else "",
            "teamScore": data.get("teamScore", 0), "opponentScore": data.get("opponentScore", 0),
            "teamBans": [hero_name(g) or "未知英雄" for g in data.get("teamBanHeroGuids") or []],
            "enemyBans": [hero_name(g) or "未知英雄" for g in data.get("enemyBanHeroGuids") or []],
            "heroes": heroes,
            "teammates": _assign_parties([player(p) for p in team_raw]),
            "enemies": _assign_parties([player(p) for p in enemy_raw]),
        }

    # ---------- 找对局：按日期、英雄、同局玩家找某一场 ----------

    def find_candidates(self, battle_tag: str, date_from: str = "", date_to: str = "", time_from: str = "",
                        time_to: str = "", mode: str = "all", hero: str = "", map_name: str = "",
                        customer_token: str = "") -> dict:
        """第一步：只翻对局列表，按日期范围、每天时段、本人英雄、地图、模式筛出候选。

        范围规则见 FindRange；涉及的赛季从新到旧逐个翻，每季翻到早于范围起点就停。
        赛季由日期决定，与界面上选的赛季无关。返回的候选带 cached（详情已在本地，比对时不发请求）。
        """
        battle_tag = battle_tag.strip()
        if "#" not in battle_tag:
            raise E.AppError(E.NOT_FOUND, "请输入完整的 名字#编号，例如 玩家#12345")
        rng = FindRange(date_from, date_to, time_from, time_to)
        token = _fresh_token(customer_token) or self.cached_token(battle_tag)
        if not token:
            token, bnet_id, _ = self._resolve(battle_tag, "")
            if bnet_id:
                db.save_player(bnet_id, battle_tag, token)
        bnet_id = _token_parts(token).get("bnetId", "")

        raw, pages, truncated, empty = [], 0, [], []
        for season in rng.seasons:
            query_season = "" if season == CURRENT_SEASON else season  # 当前赛季按界面的习惯传空
            got_any = False
            for game_mode in MODE_QUERY.get(mode, MODE_QUERY["all"]):
                for pg in range(1, FIND_MAX_PAGES + 1):
                    batch = self._call(lambda c: c.query_match_list(token, game_mode, pg, query_season))
                    pages += 1
                    got_any = got_any or bool(batch)
                    raw += [m for m in batch if rng.contains(int(m.get("beginTs") or 0))]
                    if len(batch) < MATCH_PAGE_SIZE or min(int(m.get("beginTs") or 0) for m in batch) < rng.start_ms:
                        break  # 到底了，或已翻过范围起点
                else:
                    truncated.append(SEASONS.get(season, season))  # 翻满上限还没到范围起点
            if not got_any:
                empty.append(SEASONS.get(season, season))
        if bnet_id:
            db.save_matches(bnet_id, raw)

        hero_kw, map_kw = hero.strip().lower(), map_name.strip().lower()
        newest_first = sorted(raw, key=lambda m: m.get("beginTs", 0), reverse=True)
        rows = {m.get("matchId"): _match_row(m) for m in newest_first}  # 按 id 去重
        rows = [r for r in rows.values() if hero_kw in r["heroName"].lower() and map_kw in r["mapName"].lower()]
        cached = db.cached_detail_ids([r["matchId"] for r in rows], bnet_id) if bnet_id else set()
        for r in rows:
            r["cached"] = r["matchId"] in cached
        return {"customerToken": token, "range": {"from": rng.d0.isoformat(), "to": rng.d1.isoformat()},
                "clipped": rng.clipped, "seasons": [SEASONS.get(s, s) for s in rng.seasons],
                "emptySeasons": empty, "pagesRead": pages, "truncated": truncated,
                "maxDetails": FIND_MAX_DETAILS, "candidates": rows}

    def check_match(self, match_id: str, customer_token: str, names, heroes) -> dict:
        """第二步：打开一场候选的详情（有缓存就不发请求），看同局有没有这些人 / 英雄。"""
        detail = self.match_detail(match_id, customer_token)
        hits = match_hits(detail, _keywords(names), _keywords(heroes))
        return {"matchId": match_id, "hit": hits is not None, "matched": hits or []}

    def cached_token(self, battle_tag: str) -> str:
        """本地库里此人的 customerToken（已换成当前时间戳），没收录过返回空串。不发请求。"""
        return _fresh_token((db.find_player(battle_tag.strip()) or {}).get("customerToken") or "")

    # ---------- 玩家库抽屉 ----------

    def drawer_players(self, query: str = "", offset: int = 0) -> dict:
        return db.drawer_players(query, offset)

    def toggle_followed(self, battle_tag: str) -> bool:
        battle_tag = battle_tag.strip()
        if not battle_tag:
            raise E.AppError(E.API, "BattleTag 不能为空")
        return db.toggle_followed(battle_tag)
