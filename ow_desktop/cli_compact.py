"""CLI 的精简输出：把 service 返回的界面用结构裁成 agent 好读的样子。

只在 CLI 这一层做，service 的返回结构不动（app.js 依赖它）；--full 时跳过这里原样输出。
规则按命令逐个写明，不做「见 0 就删」的通用清理——death: 0 也是信息。
"""

from __future__ import annotations

from .service import SEASONS

# 英雄数据里与别处重复或无意义的项：累计游戏时间与 time 重复，英雄获胜是 0.99999 这类浮点
HERO_STAT_SKIP = {"累计游戏时间", "英雄获胜"}


def num(value) -> float | int:
    """接口里的 "66.67" / 3280.84 统一成数字：整数给 int，其余保留两位小数。"""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0
    return int(f) if f.is_integer() else round(f, 2)


def _clock(sec) -> str:
    """秒数 -> m:ss。"""
    sec = int(num(sec))
    return f"{sec // 60}:{sec % 60:02d}"


def _rank_text(name: str, tier) -> str:
    """钻石 + 4 -> 钻石4；没有小段时只给名字。"""
    return f"{name}{tier}" if name and tier else name


def _season_name(label: str, current: str) -> str:
    """段位卡里的「本赛季」换成具体赛季名，agent 不用再去对照。"""
    return SEASONS.get(current, current) if label == "本赛季" else label


# ---------- 各命令 ----------

def compact_match_row(m: dict) -> dict:
    """对局列表的一行。"""
    return {
        "id": m["matchId"], "time": m["matchTime"], "mode": m["gameModeName"], "map": m["mapName"],
        "role": m["roleName"], "hero": m["heroName"], "result": m["result"],
        "score": f'{m["teamScore"]}:{m["opponentScore"]}',
        "kill": m["kill"], "assist": m["assist"], "death": m["death"],
        "damage": num(m["heroDamage"]), "heal": num(m["cure"]), "mitigated": num(m["resistDamage"]),
    } | ({"best": m["best"]} if m["best"] else {})  # 全场最高项，没有就省略


def _stats_block(overview: dict, ranks: list[dict], mode: str) -> dict | None:
    """竞技 / 快速的赛季汇总；没打过就整段省略（返回 None）。"""
    s, r = overview.get("summary"), overview.get("recent")
    if not s:
        return None
    block = {"games": s["matchSum"], "wins": s["winSum"], "winRate": num(s["winRate"])}
    if mode == "quick":
        # 快速没有段位，各位置战绩挂在段位卡的 quick 上，搬到这里
        block["byRole"] = [{"role": x["roleName"], "games": x["quick"]["matchSum"],
                            "winRate": num(x["quick"]["winRate"]), "kda": num(x["quick"]["kda"])}
                           for x in ranks if x.get("quick", {}).get("matchSum")]
    if r:
        block["recent"] = {"role": r["roleName"], "winRate": num(r["winRate"]), "avgKill": num(r["aveKill"]),
                           "avgDamage": num(r["aveDamage"]), "avgHeal": num(r["aveCure"])}
    if overview.get("heroes"):
        block["topHeroes"] = overview["heroes"]
    return block


def compact_player(d: dict) -> dict:
    current = d["season"]["current"]
    ranks = []
    for x in d["ranks"]:
        if not x["rankName"]:
            continue  # 没段位的位置只是占位，快速战绩已搬到 quick.byRole
        entry = {"role": x["roleName"], "rank": _rank_text(x["rankName"], x["rankTier"]),
                 "score": x["rankScore"], "games": x["matchSum"], "winRate": num(x["winRate"]),
                 "kda": num(x["kda"]), "season": _season_name(x["season"], current)}
        best = x.get("maxRank") or {}
        if (best.get("rankName"), best.get("rankTier")) != (x["rankName"], x["rankTier"]) and best.get("rankName"):
            entry["maxRank"] = _rank_text(best["rankName"], best["rankTier"])
        if x.get("maxWinStreak"):
            entry["maxWinStreak"] = x["maxWinStreak"]
        ranks.append(entry)

    out = {"player": d["profile"]["name"], "level": d["profile"]["level"],
           "hours": num(d["profile"]["gameTime"]), "season": d["season"]["name"]}
    if d["profile"].get("title"):
        out["title"] = d["profile"]["title"]
    out["ranks"] = ranks
    for mode in ("sport", "quick"):
        block = _stats_block(d["overview"][mode], d["ranks"], mode)
        if block:
            out[mode] = block
    out["matches"] = [compact_match_row(m) for m in d["recentMatches"]]
    out["hasMore"] = d["pagination"]["hasMore"]
    return out


def compact_matches(d: dict) -> dict:
    return {"season": d["season"]["name"], "matches": [compact_match_row(m) for m in d["recentMatches"]],
            "hasMore": d["pagination"]["hasMore"]}


def compact_find(d: dict) -> dict:
    """find 的结果：赛季与翻页情况 + 命中对局（每场带 matched 命中的同局玩家）。"""
    def row(m: dict) -> dict:
        r = compact_match_row(m)
        if m.get("matched"):
            r["matched"] = m["matched"]
        return r
    out = {"range": d["range"], "seasons": d["seasons"], "pagesRead": d["pagesRead"]}
    for key in ("clipped", "truncated", "emptySeasons"):  # 只在有情况时输出
        if d[key]:
            out[key] = d[key]
    for key in ("checked", "stopped", "message"):
        if key in d:
            out[key] = d[key]
    if "candidates" in d:
        out["candidates"] = [row(m) for m in d["candidates"]]
    out["matches"] = [row(m) for m in d["matches"]]
    return out


def _scoreboard_player(p: dict) -> dict:
    row = {"name": p["name"], "hero": p["heroName"]}
    if p.get("rankName"):
        row["rank"] = _rank_text(p["rankName"], p["rankTier"])
    if p.get("partyIndex"):
        row["party"] = p["partyIndex"]  # 数字相同 = 同队开黑；没有此键 = 单排
    row.update({
        "kill": p["kill"], "assist": p["assist"], "death": p["death"], "finalHit": num(p["finalHit"]),
        "damage": num(p["heroDamage"]), "heal": num(p["cure"]), "mitigated": num(p["resistDamage"]),
        "taken": num(p["damageTaken"]), "healed": num(p["healingTaken"]),
    })
    if p["best"]:
        row["best"] = p["best"]
    return row


def _hero_stat(key: str, value):
    """命中率这类 0~1 的比例换成百分数（一位小数），其余按大小取整。"""
    if "率" in key:
        return round(float(value) * 100, 1)  # 先乘再舍，num 会先舍到两位小数丢精度
    v = num(value)
    return round(v) if abs(v) >= 10 else v


def compact_match(d: dict) -> dict:
    return {
        "id": d["matchId"], "time": d["startTime"], "duration": _clock(d["gameTimeSec"]),
        "map": d["mapName"], "result": d["result"], "score": f'{d["teamScore"]}:{d["opponentScore"]}',
        "bans": {"team": d["teamBans"], "enemy": d["enemyBans"]},
        "teammates": [_scoreboard_player(p) for p in d["teammates"]],
        "enemies": [_scoreboard_player(p) for p in d["enemies"]],
        "heroes": [{"hero": h["heroName"], "time": _clock(h["userTimeSec"]),
                    "stats": {k: _hero_stat(k, v) for k, v in h["statMap"].items() if k not in HERO_STAT_SKIP}}
                   for h in d["heroes"]],
    }


def compact_library(d: dict) -> dict:
    def row(p: dict) -> dict:
        r = {"tag": p["battle_tag"], "lastSeen": p["last_seen"][:16]}
        if not p["has_token"]:
            r["noToken"] = True  # 没有 token：得先用 player 查一次（会走搜索）
        return r
    return {"followed": [row(p) for p in d["followed"]], "players": [row(p) for p in d["all"]],
            "hasMore": d["hasMore"]}
