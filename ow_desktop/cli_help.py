"""CLI 的帮助与手册文字。

写给「项目外、第一次见到这个命令行的 AI」：不能假设它读过 README / 源码，
所以总帮助、子命令帮助、guide 手册都要自成一体。{prog} 在使用时替换为实际调用名。
"""

from __future__ import annotations

OVERVIEW = """\
守望先锋国服战绩查询（数据来自网易大神），输出 JSON，供 AI / 脚本调用。
第一次使用请先运行：{prog} guide    （完整手册：字段含义、注意事项，不发请求）

推荐流程：
  1. {prog} status                       确认已登录、看今日剩余额度          [0 次请求]
  2. {prog} library <关键字>              在本地库里找完整 BattleTag            [0 次请求]
  3. {prog} player 名字#编号              段位、赛季统计、最近对局              [约 4~7 次]
  4. {prog} matches 名字#编号 --page 2    翻更早的对局                          [每页 1~2 次]
  5. {prog} match 名字#编号 <对局id>      单局记分板（id 来自第 3/4 步）        [1 次]
  另：{prog} find 名字#编号 --with 同局玩家 [--date 日期]  按日期范围 / 英雄 / 同局玩家找某一场
                                                              [翻页数 + 候选场数，见 find --help]
"""

EPILOG = """\
输出：stdout 一行 JSON。成功 {{"ok": true, "data": ...}}；失败 {{"ok": false, "code": ..., "message": ...}}
      加 --pretty 缩进；加 --full 输出完整原始字段（默认是去掉内部 ID 的精简版）。

退出码（看到 3/4/5 必须停下，不要自动重试）：
  0 成功
  1 其他错误，看 message
  2 参数写错（看 stderr 的用法提示）
  3 未登录或凭据过期 → 请用户双击打开「战绩查询.exe」（不带参数即界面）扫码登录
  4 查询号被封禁     → 立即停止一切调用并告知用户
  5 被限流或已到今日上限 → 停止，过一段时间或明天再试
  6 查无此人 / 本地库没有此人（match 前需先 player 查过一次）

调用方式（打包版）：命令行与界面是同一个 exe，不带参数是打开界面，带参数才是命令行。
  它是窗口程序：PowerShell / cmd 里直接敲不会等它结束、拿不到退出码。请接管道调用，
  例如 PowerShell 用 `{prog} status | Out-String` 后看 $LASTEXITCODE；
  Git Bash、Python subprocess 等接走输出的调用方式都正常等待并返回退出码。

注意：
  - BattleTag 必须是「名字#编号」完整形式，例如 玩家#12345；不确定时先用 library 搜。
  - 赛季号：25 = 2026 第 5 赛季（Y2026S5）、21 = Y2026S1，20 及以前为 S20…；seasons 列出全部。
    网易只保留最近几个赛季的数据，旧赛季查不到是正常现象，不要反复重试。
  - 每次请求都消耗查询号额度（每日上限见 status），只查用户要的人，禁止批量遍历玩家。

示例：
  {prog} player 玩家#12345 --mode sport --limit 5
  {prog} matches 玩家#12345 --mode quick --season 24
  {prog} match 玩家#12345 2e91d26b-13f4-764d-8c1c-304af6bcbeff --pretty
  {prog} find 玩家#12345 --date 2026-10-12 --from 20:00 --to 23:00 --with 奶龙
"""

# 子命令帮助：参数列表之后显示，说明成本、输出字段与示例
COMMAND_EPILOGS = {
    "status": """\
不发请求。输出：
  query / main   查询号与主号：loggedIn 是否已登录、nick 角色名、fetchedAt 凭据获取时间、
                 stale 凭据较旧（会自动续期，一般不用管）。查战绩只用 query（查询号）。
  usage          今日用量：used 已用、limit 上限（0 为不限）、remaining 剩余（不限时为 null）
query.loggedIn 为 false 时，其他查询命令都会以退出码 3 失败。""",

    "usage": """\
不发请求。输出 date、used 今日已用请求数、limit 上限（0 不限）、remaining 剩余。
只统计命令行发出的请求；用到 80% 时 stderr 会有提醒。""",

    "seasons": """\
不发请求。输出 [{{"id": "25", "name": "Y2026S5", "current": true}}, ...]，新的在前。
id 用于 --season 参数；name 是游戏内叫法（2026 起按年编号：Y2026S1 = 2026 第 1 赛季）。""",

    "library": """\
不发请求，只查本机已收录的玩家（查过的人、对局里出现过的人）。用来把模糊的名字补全成 名字#编号。
输出：
  followed   用户收藏的常用玩家（按关键字过滤）
  players    匹配的玩家，最近出现的在前；每项 tag 为完整 BattleTag、lastSeen 最近出现时间、
             noToken 为 true 表示此人没有缓存凭据，player 查他会多一次搜索请求
  hasMore    还有更多，传 --offset <已取人数> 取下一批
示例：{prog} library 奶龙 --limit 5""",

    "player": """\
约 4~7 次请求（资料卡、竞技 + 快速统计、对局列表；当前赛季没段位时会往前补查最多 3 个赛季）。
输出：
  player / level / hours   BattleTag、等级、游戏时长（小时）；season 本次查询的赛季名
  ranks[]    有段位的位置：role 坦克/输出/辅助/开放职责、rank 段位（如 钻石4，数字为小段，1 最高）、
             score 段位分、games 场数、winRate 胜率(%)、kda、season 该段位所属赛季
             （和顶层 season 不同 = 本赛季没段位，取的是往前最近一个有段位的赛季）、
             maxRank 赛季最高段位（与当前相同时省略）、maxWinStreak 最长连胜
  sport      竞技赛季汇总；本赛季没打竞技时整段省略
  quick      快速赛季汇总，byRole 为各位置的场数 / 胜率 / kda
             两者共有：games 场数、wins 胜场、winRate、recent 官方「近期最常用职责」统计、
             topHeroes 常用英雄
  matches[]  最近对局，字段同 matches 命令；hasMore 还有更早的对局
示例：{prog} player 玩家#12345 --mode sport --limit 5""",

    "matches": """\
本地库已有此人时每页 1 次请求（--mode all 时竞技、快速各 1 次）；没有时等同 player 的成本。
输出 season 赛季名、hasMore 是否还有下一页，以及 matches[]，每场：
  id 对局 id（给 match 用）、time 开始时间（月-日 时:分）、mode 模式（排位/快速/…）、map 地图、
  role 位置、hero 英雄、result win/loss、score 比分（我方:对方）、
  kill 击杀、assist 助攻、death 阵亡、damage 英雄伤害、heal 治疗、mitigated 阻挡伤害、
  best 本场全场最高的项（kill/heroDamage/cure/resistDamage），没有则省略
--limit 只是少输出几场，不减少请求。
示例：{prog} matches 玩家#12345 --mode sport --page 2 --limit 5""",

    "match": """\
1 次请求。battle_tag 写对局 id 来源的那位玩家（需先 player 或 matches 查过他）。
输出：
  id / time / duration(分:秒) / map / result（从该玩家视角）/ score / bans 双方禁用英雄
  teammates[] / enemies[]   每人：name、hero、rank、party（数字相同 = 同队开黑，无此键 = 单排）、
             kill / assist / death / finalHit 最后一击、damage 英雄伤害、heal 治疗、mitigated 阻挡、
             taken 承受伤害、healed 受到治疗、best 全场最高项
  heroes[]   该玩家本局用过的英雄：hero、time 使用时长、stats 详细数据（「…率」为百分数）
示例：{prog} match 玩家#12345 2e91d26b-13f4-764d-8c1c-304af6bcbeff""",

    "find": """\
用途：已知一个人，按日期范围、每天时段、他用的英雄、地图、同局其他玩家 / 英雄，找出某一场（例如视频里那局）。
所有条件都可空，但条件越少，要翻的页、要比对的场越多。
时间范围：
  --since / --until 都不填 = 当前赛季第一天到今天；只填 --since = 到今天；只填 --until = 从它所在赛季第一天起；
  --date D 等于 --since D --until D。范围可以跨赛季，涉及的赛季从新到旧逐个翻（赛季开始那天两季都查），
  与 --season 无关；早于 2026-02-10（Y2026S1）的部分网易已清理，自动跳过（输出 clipped）。
  --from / --to 是每天的时段；--to 比 --from 早表示跨零点（23:00~01:00 = 每晚 23 点到次日 1 点）。
成本，分两步：
  1. 翻对局列表：每页约 12 场、1 次请求（--mode all 时竞技、快速分开翻），每季翻到早于范围起点就停，
     每个赛季、每个模式最多 30 页。日期 / 时段 / --hero / --map / --mode 都在这一步筛，不额外花请求。
  2. 只有给了 --with / --with-hero 才逐场打开候选的详情比对（从新到旧），每场 1 次请求；查过的详情存在本地，
     再查不花请求。要发请求的候选超过 --max-details（默认 20，最大 100）时不比对，只返回 candidates 和
     message，请缩小日期 / 时段，或加 --hero / --map / --mode 后再查。
输出：
  range        实际查的日期范围 {{from, to}}；clipped 为 true 表示开始日期早于 Y2026S1、那段已跳过
  seasons      实际查的赛季；emptySeasons 这个模式下一场都没有的赛季（没打过，或已被网易清理）
  pagesRead    翻了几页（= 第 1 步的请求数）；truncated 列出翻到 30 页上限还没到范围起点的赛季
  matches[]    结果对局（新的在前），字段同 matches 命令；给了同局条件时每场多一个 matched（命中的玩家，名字(英雄)）
  checked      比对了几场；stopped / message / candidates 见上面第 2 步
示例：
  {prog} find 玩家#12345 --date 2026-10-12 --from 20:00 --to 23:00 --hero 源氏 --with 奶龙,小明
  {prog} find 玩家#12345 --since 2026-09-01 --mode sport --hero 源氏 --with 奶龙""",

    "guide": "不发请求，输出完整手册（纯文本，不是 JSON）。",
}

GUIDE_HEADER = """\
==================== 战绩查询 命令行使用手册 ====================
这是什么：本机的守望先锋国服战绩查询工具。用户在本程序的界面里扫码登录了一个
网易大神「查询号」，本工具借这个账号的凭据查任意公开战绩的玩家。数据存在本机，不经第三方。

使用原则（重要）：
  1. 每次查询都会向网易发真实请求，消耗查询号的额度。查询号若被判定为批量采集会被封禁（退出码 4），
     且无法自行恢复。只查用户明确要的人，不要遍历、不要循环刷新、不要并发调用。
  2. 能不发请求就不发：先 status 看状态，用 library 补全名字，翻页用 matches 而不是重复 player。
  3. 退出码 3/4/5 都需要人介入或等待，立即停下并把 message 转告用户，不要自动重试。
"""


def overview(prog: str) -> str:
    return OVERVIEW.format(prog=prog)


def epilog(prog: str) -> str:
    return EPILOG.format(prog=prog)


def command_epilog(name: str, prog: str) -> str:
    return COMMAND_EPILOGS[name].format(prog=prog)


def guide(prog: str) -> str:
    """完整手册：总说明 + 每个子命令的字段说明。"""
    parts = [GUIDE_HEADER, overview(prog), epilog(prog)]
    for name in ("status", "usage", "seasons", "library", "player", "matches", "match", "find"):
        parts.append(f"-------------------- {prog} {name} --------------------\n{command_epilog(name, prog)}\n")
    parts.append("每个命令的参数：{prog} <命令> --help".format(prog=prog))
    return "\n".join(parts)
