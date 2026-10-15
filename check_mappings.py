"""比对映射：python check_mappings.py [--apply]

从网易大神官方配置 https://s.166.net/config/ds_ow/ 取英雄、地图、英雄数据项，
和 ow_desktop/mappings/ 里的本地映射逐条比对，列出：
- 新增：官方有、本地没有（新英雄、新地图）；
- 不一致：同一 guid 名字或职责不同（英雄重做会改职责，比如黑影 dps → healer）；
- 仅本地有：官方已下架的老条目。

默认只读。加 --apply 才写盘：以官方为准，追加新增、覆盖不一致；仅本地有的条目保留不删，
老对局还要靠它们显示名字。赛季映射官方配置里没有开始日期，只给提醒，需手动补。

这些是公开静态配置，不需要鉴权，不走战绩接口，不计入请求配额。
退出码：0 无差异 / 1 有差异（--apply 后仍返回 1，表示刚改过）/ 2 网络或格式错误。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent              # 项目根目录
MAP_DIR = ROOT / "ow_desktop" / "mappings"          # 本地映射目录
CONFIG_URL = "https://s.166.net/config/ds_ow/{}"    # 官方配置地址
TIMEOUT = 15                                        # 单次请求超时（秒）
SEASON_LENGTH = timedelta(weeks=9)                  # 赛季长度约 8~10 周，超过就提醒可能有新赛季


def fetch(name: str):
    """取一份官方配置；国内站点，不走系统代理。"""
    resp = httpx.get(CONFIG_URL.format(name), timeout=TIMEOUT, trust_env=False)
    resp.raise_for_status()
    return resp.json()


def load_local(name: str) -> dict:
    return json.loads((MAP_DIR / name).read_text(encoding="utf-8"))


def save_local(name: str, data: dict) -> None:
    """按原文件的换行风格与结尾写回，避免无关的整文件改动。"""
    path = MAP_DIR / name
    old = path.read_bytes().decode("utf-8")
    newline = "\r\n" if "\r\n" in old else "\n"
    text = json.dumps(data, ensure_ascii=False, indent=2)
    if old.endswith(("\n", "\r\n")):
        text += "\n"
    path.write_bytes(text.replace("\n", newline).encode("utf-8"))


def official_heroes() -> dict:
    return {h["heroGuid"]: {"name": h["name"], "role": h["roleType"]} for h in fetch("ow_hero_config.json")}


def official_maps() -> dict:
    return {m["guid"]: m["name"] for m in fetch("ow_map_config.json")}


def official_attrs() -> dict[str, list[str]]:
    """数据项 guid -> 所有写法。同一 guid 在不同英雄下偶有两种写法（累计 / 累积），按出现顺序收集。"""
    texts: dict[str, list[str]] = {}
    for a in fetch("ow_hero_attr.json"):
        bucket = texts.setdefault(a["valueGuid"], [])
        if a["valueText"] not in bucket:
            bucket.append(a["valueText"])
    return texts


def diff(local: dict, official: dict, same) -> tuple[dict, dict, list]:
    """返回 (新增, 不一致, 仅本地有)。same(本地值, 官方值) 判断是否算一致。"""
    added = {k: v for k, v in official.items() if k not in local}
    changed = {k: v for k, v in official.items() if k in local and not same(local[k], v)}
    local_only = [k for k in local if k not in official]
    return added, changed, local_only


def report(title: str, local: dict, added: dict, changed: dict, local_only: list, show) -> int:
    """打印一份映射的差异，返回差异条数（仅本地有不算差异，只提示）。"""
    print(f"\n[{title}] 本地 {len(local)} 条")
    for k, v in added.items():
        print(f"  + 新增   {k}  {show(v)}")
    for k, v in changed.items():
        print(f"  ~ 不一致 {k}  {show(local[k])}  →  {show(v)}")
    for k in local_only:
        print(f"  · 仅本地 {k}  {show(local[k])}（保留）")
    if not (added or changed):
        print("  与官方一致")
    return len(added) + len(changed)


def check_seasons() -> None:
    """赛季映射只能手动维护，这里只看日期给提醒。"""
    seasons = load_local("season_mapping.json")
    starts = load_local("season_start_mapping.json")
    latest = max(seasons, key=int)
    print(f"\n[赛季] 本地最新 {latest} = {seasons[latest]}", end="")
    if latest not in starts:
        print(f"\n  ! season_start_mapping.json 缺少 {latest} 的开始日期")
        return
    start = date.fromisoformat(starts[latest])
    print(f"，{start} 开始")
    if date.today() - start > SEASON_LENGTH:
        print(f"  ! 已超过 {SEASON_LENGTH.days} 天，可能有新赛季：按接口号 +1 手动补 season_mapping.json"
              " 与 season_start_mapping.json")


def main() -> int:
    parser = argparse.ArgumentParser(description="比对本地映射与网易大神官方配置")
    parser.add_argument("--apply", action="store_true", help="以官方为准写回：追加新增、覆盖不一致")
    args = parser.parse_args()

    try:
        heroes, maps, attrs = official_heroes(), official_maps(), official_attrs()
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
        print(f"取官方配置失败：{e!r}", file=sys.stderr)
        return 2

    # (文件名, 标题, 官方数据, 判断一致, 显示, 写入值)
    specs = [
        ("hero_mapping.json", "英雄", heroes, lambda a, b: a == b,
         lambda v: f"{v['name']}（{v['role']}）", lambda v: v),
        ("map_mapping.json", "地图", maps, lambda a, b: a == b, str, lambda v: v),
        # 本地写法只要是官方写法之一就算一致；写入时取第一种
        ("hero_attr_mapping.json", "英雄数据项", attrs,
         lambda a, b: (a in b) if isinstance(b, list) else a == b,
         lambda v: " / ".join(v) if isinstance(v, list) else v, lambda v: v[0]),
    ]
    total = 0
    for name, title, official, same, show, to_local in specs:
        local = load_local(name)
        added, changed, local_only = diff(local, official, same)
        total += report(title, local, added, changed, local_only, show)
        if args.apply and (added or changed):
            # dict.update 保持已有键的位置，新键追加在末尾
            local.update({k: to_local(v) for k, v in {**changed, **added}.items()})
            save_local(name, local)
            print(f"  已写回 {name}")

    check_seasons()
    if total and not args.apply:
        print(f"\n共 {total} 处差异；确认无误后运行 python check_mappings.py --apply 写回")
    return 1 if total else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # 管道 / Git Bash 下也输出 UTF-8
    sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
