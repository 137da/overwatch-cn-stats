"""本地 sqlite 缓存：玩家、常用玩家、段位记录、对局记录、对局详情。

库文件放 data/ow_stats.db（已被 .gitignore 忽略），启动时自动建表。
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime

from .paths import DATA_DIR

DB_PATH = DATA_DIR / "ow_stats.db"  # 库文件
MAX_RANK_RECORDS_PER_ROLE = 10  # 每个玩家每个位置最多保留的段位记录数
DRAWER_PAGE_SIZE = 100           # 玩家库每次加载的人数，往下滚再加载下一页


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS players (
            bnet_id TEXT PRIMARY KEY,       -- 玩家 bnetId（唯一标识，名字可能变）
            battle_tag TEXT NOT NULL,       -- 名字#编号
            customer_token TEXT,            -- 查此人用的 customerToken
            first_seen TEXT NOT NULL,       -- 首次查询时间
            last_seen TEXT NOT NULL         -- 最近查询时间
        )""")
    # 导入过旧库的几十万玩家：按时间倒序列出、按名字找人都要走索引，否则一次要十几秒。
    # 导入的人大量 last_seen 相同，翻页要再按 bnet_id 排，否则同一时间的人在页与页之间会重复或漏掉
    conn.execute("DROP INDEX IF EXISTS idx_players_last_seen")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_players_recent ON players(last_seen, bnet_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_players_battle_tag ON players(battle_tag)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS followed_players (
            battle_tag TEXT PRIMARY KEY,    -- 常用玩家的 名字#编号（抽屉按 BattleTag 操作）
            added_time TEXT NOT NULL
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS report_handled (
            bnet_id TEXT NOT NULL,          -- 被举报人 bnetId
            report_time TEXT NOT NULL,      -- 工单提交时间（同一人再次被举报算新的一条）
            handled_at TEXT NOT NULL,       -- 被举报提醒处理的时间
            queried INTEGER NOT NULL DEFAULT 0,  -- 是否查过战绩（自动或手动）
            PRIMARY KEY (bnet_id, report_time)
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rank_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bnet_id TEXT NOT NULL,
            role_type TEXT NOT NULL,        -- tank / dps / healer
            rank_name TEXT, rank_tier INTEGER, rank_score INTEGER,
            match_sum INTEGER, win_rate REAL, kda REAL,
            query_time TEXT NOT NULL
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS match_records (
            bnet_id TEXT NOT NULL,
            match_id TEXT NOT NULL,
            result INTEGER, role_type TEXT, hero_guid TEXT, game_mode TEXT,
            team_score INTEGER, opponent_score INTEGER,
            kill INTEGER, assist INTEGER, death INTEGER,
            hero_damage INTEGER, cure INTEGER,
            begin_ts INTEGER,               -- 对局开始时间（毫秒时间戳）
            PRIMARY KEY (bnet_id, match_id) -- 联合主键天然去重
        )""")
    # 对局结束后详情不再变化，存原始响应，找对局时反复比对、再次打开记分板都不用再发请求。
    # 详情里的队友 / 敌人、胜负是相对查看者而言的，所以按「对局 + 查看者」存
    conn.execute("""
        CREATE TABLE IF NOT EXISTS match_details (
            match_id TEXT NOT NULL,
            viewer_bnet_id TEXT NOT NULL,   -- 以谁的视角查的（customerToken 里的 bnetId）
            data TEXT NOT NULL,             -- queryMatchInfo 原始响应 JSON
            fetched_at TEXT NOT NULL,
            PRIMARY KEY (match_id, viewer_bnet_id)
        )""")
    return conn


@contextmanager
def _tx():
    """一次事务：成功则提交，最终一定关闭连接（sqlite3 自带的 with 不会关连接）。"""
    conn = _conn()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def init_db() -> None:
    """确保库与表存在。"""
    _conn().close()


def save_player(bnet_id: str, battle_tag: str, customer_token: str) -> None:
    """写入或更新玩家，保留首次查询时间。"""
    now = _now()
    with _tx() as conn:
        conn.execute("""
            INSERT INTO players (bnet_id, battle_tag, customer_token, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(bnet_id) DO UPDATE SET
                battle_tag = excluded.battle_tag,
                customer_token = excluded.customer_token,
                last_seen = excluded.last_seen""",
            (bnet_id, battle_tag, customer_token, now, now))


def find_player(battle_tag: str) -> dict | None:
    """按 BattleTag 查已缓存的玩家，没有返回 None。"""
    with _tx() as conn:
        row = conn.execute(
            "SELECT bnet_id, battle_tag, customer_token FROM players WHERE battle_tag = ?",
            (battle_tag,)).fetchone()
    return {"bnetId": row[0], "battleTag": row[1], "customerToken": row[2]} if row else None


def cached_match_detail(match_id: str, viewer_bnet_id: str) -> dict | None:
    """本地缓存的对局详情原始响应，没有返回 None。"""
    with _tx() as conn:
        row = conn.execute("SELECT data FROM match_details WHERE match_id = ? AND viewer_bnet_id = ?",
                           (match_id, viewer_bnet_id)).fetchone()
    return json.loads(row[0]) if row else None


def cached_detail_ids(match_ids: list[str], viewer_bnet_id: str) -> set[str]:
    """这批对局里哪些已有本地详情（不用再发请求）。"""
    if not match_ids:
        return set()
    marks = ",".join("?" * len(match_ids))
    with _tx() as conn:
        rows = conn.execute(f"SELECT match_id FROM match_details WHERE viewer_bnet_id = ? AND match_id IN ({marks})",
                            (viewer_bnet_id, *match_ids)).fetchall()
    return {r[0] for r in rows}


def save_match_detail(match_id: str, viewer_bnet_id: str, data: dict) -> None:
    with _tx() as conn:
        conn.execute("INSERT OR REPLACE INTO match_details (match_id, viewer_bnet_id, data, fetched_at) "
                     "VALUES (?, ?, ?, ?)", (match_id, viewer_bnet_id, json.dumps(data, ensure_ascii=False), _now()))


def save_match_players(players: list[dict]) -> int:
    """记分板里带 customerToken 的玩家顺手入库，之后从抽屉点人可以免搜索直连。

    只缓存本次响应里已有的数据，不额外发请求。返回写入人数。
    """
    rows = [(str(p["bnetId"]), p["name"], p["customerToken"])
            for p in players if p.get("bnetId") and p.get("name") and p.get("customerToken")]
    if not rows:
        return 0
    now = _now()
    with _tx() as conn:
        conn.executemany("""
            INSERT INTO players (bnet_id, battle_tag, customer_token, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(bnet_id) DO UPDATE SET
                battle_tag = excluded.battle_tag,
                customer_token = excluded.customer_token,
                last_seen = excluded.last_seen""",
            [(b, t, c, now, now) for b, t, c in rows])
    return len(rows)


def drawer_players(query: str = "", offset: int = 0) -> dict:
    """玩家库抽屉：常用玩家 + 收录玩家（分页）。

    query 为空时按最近活跃列出；非空时对全库按 BattleTag 模糊检索。
    每次 DRAWER_PAGE_SIZE 人，offset 为已加载人数，hasMore 表示还有下一页。
    字段名沿用旧前端（battle_tag / has_token / is_followed / last_seen）。
    """
    q = (query or "").strip()
    offset = max(0, int(offset))
    page = (DRAWER_PAGE_SIZE + 1, offset)  # 多取一条，用来判断还有没有下一页
    like = f"%{q}%"
    with _tx() as conn:
        # 常用玩家人数很少，总是全量返回：顶部收藏按钮要全部显示，检索过滤交给前端
        followed_rows = conn.execute(
            "SELECT f.battle_tag, p.customer_token, p.last_seen FROM followed_players f "
            "LEFT JOIN players p ON p.battle_tag = f.battle_tag ORDER BY f.added_time DESC").fetchall()
        if q:
            all_rows = conn.execute(
                # 模糊搜索强制全表扫（几十万行约 0.15 秒）：走 last_seen 索引时，命中少的词要扫完整个索引，反而要近 2 秒
                "SELECT battle_tag, customer_token, last_seen FROM players NOT INDEXED WHERE battle_tag LIKE ? "
                "ORDER BY last_seen DESC, bnet_id DESC LIMIT ? OFFSET ?",
                (like, *page)).fetchall()
        else:
            all_rows = conn.execute(
                "SELECT battle_tag, customer_token, last_seen FROM players "
                "ORDER BY last_seen DESC, bnet_id DESC LIMIT ? OFFSET ?", page).fetchall()
    has_more = len(all_rows) > DRAWER_PAGE_SIZE
    all_rows = all_rows[:DRAWER_PAGE_SIZE]
    followed_tags = {r[0] for r in followed_rows}
    return {
        "followed": [{"battle_tag": r[0], "has_token": bool(r[1]), "last_seen": r[2] or ""}
                     for r in followed_rows],
        "all": [{"battle_tag": r[0], "has_token": bool(r[1]), "is_followed": r[0] in followed_tags,
                 "last_seen": r[2] or ""} for r in all_rows],
        "hasMore": has_more,
    }


def handled_reports() -> dict[tuple[str, str], dict]:
    """已处理的举报：{(bnet_id, report_time): {"queried": bool, "handledAt": str}}。"""
    with _tx() as conn:
        rows = conn.execute("SELECT bnet_id, report_time, queried, handled_at FROM report_handled").fetchall()
    return {(r[0], r[1]): {"queried": bool(r[2]), "handledAt": r[3]} for r in rows}


def mark_reports_handled(items: list[tuple[str, str, bool]]) -> None:
    """批量标记举报已处理：[(bnet_id, report_time, queried)]，已存在的不覆盖。"""
    now = _now()
    with _tx() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO report_handled (bnet_id, report_time, handled_at, queried) VALUES (?, ?, ?, ?)",
            [(b, t, now, int(q)) for b, t, q in items])


def mark_report_queried(bnet_id: str, report_time: str) -> None:
    """手动查过某个被举报人后标记，没处理过的也一并记为已处理。"""
    with _tx() as conn:
        conn.execute(
            "INSERT INTO report_handled (bnet_id, report_time, handled_at, queried) VALUES (?, ?, ?, 1) "
            "ON CONFLICT(bnet_id, report_time) DO UPDATE SET queried = 1",
            (bnet_id, report_time, _now()))


def toggle_followed(battle_tag: str) -> bool:
    """切换常用状态，返回切换后是否为常用。"""
    with _tx() as conn:
        if conn.execute("SELECT 1 FROM followed_players WHERE battle_tag = ?", (battle_tag,)).fetchone():
            conn.execute("DELETE FROM followed_players WHERE battle_tag = ?", (battle_tag,))
            return False
        conn.execute("INSERT INTO followed_players (battle_tag, added_time) VALUES (?, ?)",
                     (battle_tag, _now()))
        return True


def save_rank(bnet_id: str, role_type: str, rank_name: str, rank_tier: int,
              rank_score: int, match_sum: int, win_rate: float, kda: float) -> None:
    """写入段位记录；与最新一条完全相同则跳过，并按位置限额淘汰旧记录。"""
    with _tx() as conn:
        last = conn.execute(
            "SELECT rank_name, rank_tier, rank_score, match_sum FROM rank_records "
            "WHERE bnet_id = ? AND role_type = ? ORDER BY id DESC LIMIT 1",
            (bnet_id, role_type)).fetchone()
        if last == (rank_name, rank_tier, rank_score, match_sum):
            return
        conn.execute(
            "INSERT INTO rank_records (bnet_id, role_type, rank_name, rank_tier, rank_score, "
            "match_sum, win_rate, kda, query_time) VALUES (?,?,?,?,?,?,?,?,?)",
            (bnet_id, role_type, rank_name, rank_tier, rank_score, match_sum, win_rate, kda, _now()))
        conn.execute(
            "DELETE FROM rank_records WHERE bnet_id = ? AND role_type = ? AND id NOT IN ("
            "SELECT id FROM rank_records WHERE bnet_id = ? AND role_type = ? "
            "ORDER BY id DESC LIMIT ?)",
            (bnet_id, role_type, bnet_id, role_type, MAX_RANK_RECORDS_PER_ROLE))


def save_matches(bnet_id: str, matches: list[dict]) -> None:
    """批量写入对局，已存在的自动忽略。"""
    with _tx() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO match_records (bnet_id, match_id, result, role_type, hero_guid, "
            "game_mode, team_score, opponent_score, kill, assist, death, hero_damage, cure, begin_ts) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(bnet_id, str(m.get("matchId", "")), m.get("matchRet"), m.get("roleType"),
              str(m.get("heroGuid", "")), m.get("gameMode"), m.get("teamScore"), m.get("opponentScore"),
              m.get("kill"), m.get("assist"), m.get("death"), m.get("heroDamage"), m.get("cure"),
              m.get("beginTs")) for m in matches if m.get("matchId")])
