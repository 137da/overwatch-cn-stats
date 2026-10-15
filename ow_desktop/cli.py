"""无界面命令行：给 agent / 脚本调用，python -m ow_desktop.cli <子命令>。

- stdout 只输出一段 UTF-8 JSON：{"ok": true, "data": ...} 或 {"ok": false, "code": ..., "message": ...}，
  与界面的 bridge 同一结构；提醒类信息走 stderr。
- 帮助与手册文字在 cli_help.py（写给项目外的 AI，要自成一体）；guide 子命令输出纯文本手册。
- 退出码见 EXIT_CODES，agent 可以不解析 JSON 直接按退出码分支。
- 默认输出精简版（cli_compact.py：去掉内部 ID、空占位，数字统一成数值），--full 输出原样完整数据。
- 输出里不带 customerToken：它等同于查此人的凭据，agent 不需要也不该拿到；
  要查单局时用 BattleTag 从本地库取（先用 player 查过一次就有）。
- 和桌面端共用 data/、state/ 与同一把跨进程请求锁（guard.py），CLI 的请求计入每日上限。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import cli_compact as compact
from . import cli_help
from . import database as db
from . import errors as E
from . import settings, updater
from .guard import RequestGuard, read_usage
from .service import (CURRENT_SEASON, FIND_MAX_DETAILS, MAIN_STATE_DIR, SEASONS, STATE_DIR, Service,
                      account_info)

# 错误码 -> 退出码；未列出的错误为 1，参数错误由 argparse 给 2
EXIT_CODES = {
    E.AUTH_EXPIRED: 3, E.NEED_LOGIN: 3,     # 需要人去扫码 / 续期
    E.BANNED: 4,                            # 查询号被封，必须停
    E.RATE_LIMITED: 5, E.DAILY_LIMIT: 5,    # 限流或到了每日上限，必须停
    E.NOT_FOUND: 6,                         # 查无此人 / 本地没有此人
}
MODES = ("all", "sport", "quick")  # 对局模式：全部 / 竞技 / 快速
FIND_DEFAULT_DETAILS = 20          # find 默认最多为比对发几次详情请求，超过就停下报告候选（上限 FIND_MAX_DETAILS）
LIBRARY_LIMIT = 20                 # library 默认返回人数（库里每页 100，agent 用不了那么多）


def _usage() -> dict:
    limit = settings.load()["cli_daily_limit"]
    usage = read_usage()
    return {"date": usage["date"], "used": usage["count"], "limit": limit,
            "remaining": None if limit == 0 else max(0, limit - usage["count"])}  # limit 为 0 表示不限


def _season(value: str) -> str:
    if value and value not in SEASONS:
        raise E.AppError(E.API, f"未知赛季：{value}，可用 seasons 子命令查看")
    return value


def _service() -> Service:
    return Service(RequestGuard(counted=True, daily_limit=settings.load()["cli_daily_limit"]))


# ---------- 子命令（每个返回要输出的 data） ----------

def cmd_status(_args) -> dict:
    """两个账号的凭据摘要 + 今日用量。不发请求。"""
    return {"query": account_info(STATE_DIR, "查询号"), "main": account_info(MAIN_STATE_DIR, "主号"),
            "usage": _usage(), "version": updater.info()["version"]}


def cmd_usage(_args) -> dict:
    return _usage()


def cmd_seasons(_args) -> list:
    return [{"id": k, "name": v, "current": k == CURRENT_SEASON}
            for k, v in sorted(SEASONS.items(), key=lambda kv: -int(kv[0]))]


def _limit_matches(data: dict, limit: int) -> None:
    """只留最近 limit 场（0 不限）。只是裁输出，不省请求：接口按页返回。"""
    if limit > 0:
        data["recentMatches"] = data["recentMatches"][:limit]


def cmd_library(args) -> dict:
    """本地玩家库检索。不发请求。--offset 为已取人数，翻页时传上一次的累计数。"""
    data = db.drawer_players(args.query, args.offset)
    if args.query:  # 常用玩家总是全量返回（界面在前端过滤），这里按关键字筛
        data["followed"] = [p for p in data["followed"] if args.query.lower() in p["battle_tag"].lower()]
    if args.limit > 0 and len(data["all"]) > args.limit:
        data["all"], data["hasMore"] = data["all"][:args.limit], True
    return data if args.full else compact.compact_library(data)


def cmd_player(args) -> dict:
    """资料卡 + 段位 + 竞技/快速统计 + 第一页对局。约 4~6 次请求。"""
    data = _service().get_player(args.battle_tag, mode=args.mode, page=1, limit=args.pages,
                                 season=_season(args.season))
    data.pop("customerToken", None)
    _limit_matches(data, args.limit)
    return data if args.full else compact.compact_player(data)


def cmd_matches(args) -> dict:
    """只翻对局列表；本地库有此人时不再拉资料卡与统计，每页 1 次请求（all 模式竞技、快速各算）。"""
    svc = _service()
    token = svc.cached_token(args.battle_tag)
    data = svc.get_player(args.battle_tag, mode=args.mode, page=args.page, limit=args.pages,
                          season=_season(args.season), customer_token=token, stats=not token)
    data = {k: data[k] for k in ("recentMatches", "pagination", "season")}
    _limit_matches(data, args.limit)
    return data if args.full else compact.compact_matches(data)


def cmd_match(args) -> dict:
    """单局记分板，1 次请求。matchId 来自 player / matches 的 recentMatches。"""
    svc = _service()
    token = svc.cached_token(args.battle_tag)
    if not token:
        raise E.AppError(E.NOT_FOUND, f"本地库没有 {args.battle_tag}，先用 player 查一次")
    data = svc.match_detail(args.match_id, token)
    return data if args.full else compact.compact_match(data)


def cmd_find(args) -> dict:
    """按日期 / 时段 / 本人英雄 / 地图筛候选；给了同局条件再逐场看详情比对（有缓存不发请求）。"""
    if args.date and (args.since or args.until):
        raise E.AppError(E.API, "--date 是 --since/--until 同一天的简写，不要同时写")
    if not 0 < args.max_details <= FIND_MAX_DETAILS:
        raise E.AppError(E.API, f"--max-details 须在 1~{FIND_MAX_DETAILS} 之间（每场 1 次请求，范围太大请先缩小条件）")
    svc = _service()
    d = svc.find_candidates(args.battle_tag, args.date or args.since, args.date or args.until, args.time_from,
                            args.time_to, mode=args.mode, hero=args.hero, map_name=args.map)
    d.pop("maxDetails")
    token = d.pop("customerToken")
    cands = d.pop("candidates")
    if not (args.with_names or args.with_heroes):
        d["matches"] = cands
    else:
        cost = sum(not c["cached"] for c in cands)  # 详情不在本地、要发请求的场数
        if cost > args.max_details:
            d.update(stopped=True, candidates=cands, matches=[],
                     message=f"候选 {len(cands)} 场，比对需 {cost} 次请求，超过 --max-details {args.max_details}；"
                             "请加时段 / 英雄 / 地图缩小范围，或确有需要再调大")
        else:
            hits = []
            for c in cands:
                r = svc.check_match(c["matchId"], token, args.with_names, args.with_heroes)
                if r["hit"]:
                    hits.append({**c, "matched": r["matched"]})
            d.update(checked=len(cands), matches=hits)
    return d if args.full else compact.compact_find(d)


def _prog() -> str:
    """帮助里显示的调用名：打包版是 exe 本身（战绩查询.exe，带参数即命令行）。"""
    return Path(sys.executable).name if getattr(sys, "frozen", False) else "python -m ow_desktop.cli"


def build_parser() -> argparse.ArgumentParser:
    prog = _prog()
    raw = argparse.RawDescriptionHelpFormatter  # 保留说明文字里的换行与缩进
    p = argparse.ArgumentParser(prog=prog, formatter_class=raw, add_help=False,
                                description=cli_help.overview(prog), epilog=cli_help.epilog(prog))
    # 通用开关在子命令前后都能写；子命令上的默认值用 SUPPRESS，免得把前面写的覆盖成 False
    common = argparse.ArgumentParser(add_help=False)
    # argparse 自带的 -h 说明与分组标题是英文，换成中文（看帮助的 AI 不一定读过源码）
    for parser in (p, common):
        parser.add_argument("-h", "--help", action="help", help="显示帮助")
    for parser, default in ((p, False), (common, argparse.SUPPRESS)):
        parser.add_argument("--pretty", action="store_true", default=default, help="缩进输出，方便人看")
        parser.add_argument("--full", action="store_true", default=default, help="输出完整原始结构（默认是精简版）")
    sub = p.add_subparsers(dest="command", required=True, title="命令", metavar="<命令>")
    _add = sub.add_parser
    # 每个子命令都带上通用开关，并把 cli_help 里的成本 / 字段说明挂在它自己的 --help 末尾
    sub.add_parser = lambda name, **kw: _add(
        name, parents=[common], formatter_class=raw, add_help=False, description=kw.get("help"),
        epilog=cli_help.command_epilog(name, prog), **kw)

    sub.add_parser("status", help="账号凭据与今日用量（不发请求）").set_defaults(fn=cmd_status)
    sub.add_parser("usage", help="CLI 今日请求数与上限（不发请求）").set_defaults(fn=cmd_usage)
    sub.add_parser("seasons", help="赛季列表（不发请求）").set_defaults(fn=cmd_seasons)
    sub.add_parser("guide", help="完整使用手册，第一次用先看这个（不发请求）").set_defaults(fn=None)

    s = sub.add_parser("library", help="检索本地玩家库（不发请求）")
    s.add_argument("query", nargs="?", default="", help="BattleTag 关键字，留空列最近的")
    s.add_argument("--offset", type=int, default=0, help="已取人数，翻页用")
    s.add_argument("--limit", type=int, default=LIBRARY_LIMIT, help=f"返回人数，最多 100（默认 {LIBRARY_LIMIT}）")
    s.set_defaults(fn=cmd_library)

    def add_query_args(s, with_page: bool):
        s.add_argument("battle_tag", help="名字#编号")
        s.add_argument("--mode", choices=MODES, default="all", help="对局模式：all 全部 / sport 竞技 / quick 快速（默认 all）")
        s.add_argument("--season", default="", help="赛季号（数字，如 24；见 seasons），默认当前赛季")
        if with_page:
            s.add_argument("--page", type=int, default=1, help="起始页，每页约 12 场")
        s.add_argument("--pages", type=int, choices=(1, 2), default=1, help="一次连读几页，每页约 12 场、1 次请求（默认 1）")
        s.add_argument("--limit", type=int, default=0, help="只返回最近几场（默认 0 不限；不省请求）")

    s = sub.add_parser("player", help="查一个玩家：资料、段位、统计、近期对局")
    add_query_args(s, with_page=False)
    s.set_defaults(fn=cmd_player)

    s = sub.add_parser("matches", help="翻对局列表")
    add_query_args(s, with_page=True)
    s.set_defaults(fn=cmd_matches)

    s = sub.add_parser("find", help="按日期、英雄、同局玩家找某一场对局")
    s.add_argument("battle_tag", help="已知的那位玩家，名字#编号")
    s.add_argument("--since", default="", metavar="日期", help="开始日期 YYYY-MM-DD（可空）")
    s.add_argument("--until", default="", metavar="日期", help="结束日期 YYYY-MM-DD（可空，默认今天）")
    s.add_argument("--date", default="", metavar="日期", help="只查这一天，等于 --since 与 --until 写同一天")
    s.add_argument("--from", dest="time_from", default="", metavar="HH:MM", help="每天时段开始（可空）")
    s.add_argument("--to", dest="time_to", default="", metavar="HH:MM",
                   help="每天时段结束，含这一分钟；比开始早表示跨零点，如 --from 23:00 --to 01:00（可空）")
    s.add_argument("--mode", choices=MODES, default="all", help="对局模式（默认 all）")
    s.add_argument("--hero", default="", metavar="英雄", help="他本人用的英雄，包含即可（可空，不花请求）")
    s.add_argument("--map", default="", metavar="地图", help="地图名，包含即可（可空，不花请求）")
    s.add_argument("--with", dest="with_names", default="", metavar="名字,…", help="同局玩家名字，逗号分隔、包含即可、不分大小写；每个都要出现")
    s.add_argument("--with-hero", dest="with_heroes", default="", metavar="英雄,…", help="同局出现的英雄，逗号分隔；每个都要出现")
    s.add_argument("--max-details", type=int, default=FIND_DEFAULT_DETAILS, metavar="N",
                   help=f"比对最多发几次详情请求，超过就只报告候选（默认 {FIND_DEFAULT_DETAILS}，最大 {FIND_MAX_DETAILS}）")
    s.set_defaults(fn=cmd_find)

    s = sub.add_parser("match", help="单局记分板")
    s.add_argument("battle_tag", help="对局 id 来源的那位玩家，名字#编号（需先查过）")
    s.add_argument("match_id", help="对局 id，来自 player / matches 输出的 matches[].id")
    s.set_defaults(fn=cmd_match)
    for parser in (p, *sub.choices.values()):
        parser._positionals.title, parser._optionals.title = "参数", "选项"
    return p


def main(argv: list[str] | None = None) -> int:
    # Windows 控制台默认 GBK，中文会乱码；agent 读管道也按 UTF-8
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    argv = sys.argv[1:] if argv is None else argv
    parser = build_parser()
    if not argv:  # 不带参数：给完整帮助而不是一行「缺少子命令」
        parser.print_help()
        return 0
    args = parser.parse_args(argv)
    if args.command == "guide":
        print(cli_help.guide(_prog()))
        return 0
    try:
        out, code = {"ok": True, "data": args.fn(args)}, 0
    except E.AppError as e:
        out, code = {"ok": False, "code": e.code, "message": e.message}, EXIT_CODES.get(e.code, 1)
    except Exception as e:  # noqa: BLE001  兜底：原因要带出来，不能静默
        out, code = {"ok": False, "code": E.API, "message": f"{type(e).__name__}: {e}"}, 1
    print(json.dumps(out, ensure_ascii=False, indent=2 if args.pretty else None))
    return code


if __name__ == "__main__":
    sys.exit(main())
