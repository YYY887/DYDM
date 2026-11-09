# -*- coding: utf-8 -*-
"""
main.py —— 抖音直播 全量消息采集器 (弹幕 + 礼物 + 进场 + 点赞 + 互动 + 人气)

用法:
    python main.py <web_rid 或 直播间URL> [选项]

常用:
    python main.py 343074527583                          # 弹幕+礼物+进场 上屏, 全量落盘到 data/
    python main.py 343074527583 --types all               # 终端显示全部消息
    python main.py 343074527583 --seconds 600 --jsonl data/r.jsonl --csv data/r.csv
    python main.py 343074527583 --only gift,chat          # 只显示礼物和弹幕
    python main.py 343074527583 --gift-only               # 只显示礼物

输出:
    终端   : 按 --types 过滤 (默认 chat,gift,member)
    JSONL  : 全量消息 (弹幕/礼物/进场/点赞/互动/人气/控制…)
    CSV    : 全量消息, Excel 可直接打开
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time

import client
import colors
import utils

DEFAULT_TYPES = "chat,gift,member"
ALL_TYPES = "chat,gift,member,like,social,stats,control,fansclub,other"


# --------------------------------------------------------------------------
# 输出格式
# --------------------------------------------------------------------------
def hhmmss(rec: dict) -> str:
    return utils.safe_strftime(rec.get("create_time") or rec.get("recv_at"))


def full_time(rec: dict) -> str:
    return utils.safe_strftime(rec.get("create_time") or rec.get("recv_at"),
                               "%Y-%m-%d %H:%M:%S", fallback="")


def line_of(rec: dict, plain: bool = False, show_avatar: bool = False) -> str:
    """一条消息 -> 一行终端文本(带颜色); 返回空串表示不显示"""
    k = rec["kind"]
    t = hhmmss(rec)
    av = f" {rec['avatar_short']}" if (show_avatar and rec.get("avatar_short")) else ""
    if plain:                      # 管道友好: 纯正文, 不上色
        return rec.get("content", "") if k == "chat" else ""
    if k == "chat":
        return (f"{colors.c('[' + t + ']', colors.TIME_COLOR)} "
                f"{colors.marked(rec, '[弹幕]', rec.get('user',''), '：' + rec.get('content',''))}{av}")
    if k == "gift":
        n = rec.get("repeat_count") or 1
        total = rec.get("total_count")
        extra = f" (累计 x{total})" if total else ""
        combo = f" 连击x{rec['combo_count']}" if rec.get("combo_count") else ""
        gname = rec.get("gift_name") or f"礼物{rec.get('gift_id','?')}"
        return (f"{colors.c('[' + t + ']', colors.TIME_COLOR)} "
                f"{colors.marked(rec, '[礼物]', rec.get('user',''), f' 送出 {gname} x{n}{combo}{extra}')}{av}")
    if k == "member":
        v = f" (在线{rec['viewer_count']})" if rec.get("viewer_count") else ""
        return (f"{colors.c('[' + t + ']', colors.TIME_COLOR)} "
                f"{colors.marked(rec, '[进场]', rec.get('user',''), f' 来了{v}')}{av}")
    if k == "like":
        tail = f" 点赞 +{rec.get('count',0)} (累计 {rec.get('total',0)})"
        return (f"{colors.c('[' + t + ']', colors.TIME_COLOR)} "
                f"{colors.marked(rec, '[点赞]', rec.get('user',''), tail)}{av}")
    if k == "social":
        act = {1: "关注了主播", 2: "分享了直播间", 3: "关注了主播"}.get(rec.get("action"), "互动")
        return (f"{colors.c('[' + t + ']', colors.TIME_COLOR)} "
                f"{colors.marked(rec, '[互动]', rec.get('user',''), ' ' + act)}{av}")
    if k == "stats":
        return (f"{colors.c('[' + t + ']', colors.TIME_COLOR)} "
                f"{colors.tag(rec, '[人气]')} {colors.body(rec, '在线 ' + str(rec.get('display') or rec.get('viewer', 0)))}")
    if k == "fansclub":
        return (f"{colors.c('[' + t + ']', colors.TIME_COLOR)} "
                f"{colors.marked(rec, '[粉丝团]', rec.get('user',''), ' 粉丝团消息')}{av}")
    if k == "control":
        tail = f"status={rec.get('status')} {rec.get('text','')}"
        return (f"{colors.c('[' + t + ']', colors.TIME_COLOR)} "
                f"{colors.tag(rec, '[控制]')} {colors.body(rec, tail)}")
    return (f"{colors.c('[' + t + ']', colors.TIME_COLOR)} "
            f"{colors.tag(rec, '[未知]')} {colors.body(rec, rec.get('method',''))}") if k == "other" else ""


AVATAR_SUFFIX = "~c5_100x100.jpeg"      # 抖音图床缩放后缀: 拿原始 URL 自己拼, 省流量


def shorten_avatar(url: str) -> str:
    """把头像 URL 压成可读长度: 保留文件名 + 缩放后缀"""
    if not url:
        return ""
    base = url.split("?")[0]
    return base.rsplit("/", 1)[-1] + AVATAR_SUFFIX


# --------------------------------------------------------------------------
# 落盘
# --------------------------------------------------------------------------
CSV_FIELDS = ["time", "kind", "user", "uid", "content", "gift_id", "gift_name",
              "repeat_count", "combo_count", "total_count", "count", "total",
              "viewer_count", "viewer", "display", "level", "sec_uid",
              "avatar", "avatar_uri", "msg_id", "method"]


class Sink:
    """JSONL + CSV 落盘; 终端输出可被管道关闭而不影响落盘"""

    def __init__(self, jsonl="", csv_path=""):
        self.jf = self.cf = self.cw = None
        self.stdout_ok = True
        if jsonl:
            os.makedirs(os.path.dirname(os.path.abspath(jsonl)), exist_ok=True)
            self.jf = open(jsonl, "a", encoding="utf-8")
        if csv_path:
            os.makedirs(os.path.dirname(os.path.abspath(csv_path)), exist_ok=True)
            fresh = not os.path.exists(csv_path) or os.path.getsize(csv_path) == 0
            self.cf = open(csv_path, "a", encoding="utf-8-sig", newline="")
            self.cw = csv.DictWriter(self.cf, fieldnames=CSV_FIELDS, extrasaction="ignore")
            if fresh:
                self.cw.writeheader()

    def save(self, rec: dict):
        if self.jf:
            self.jf.write(json.dumps(rec, ensure_ascii=False) + "\n")
            self.jf.flush()
        if self.cw:
            row = dict(rec)
            row["time"] = full_time(rec)
            self.cw.writerow(row)
            self.cf.flush()

    def show(self, text: str):
        if not self.stdout_ok or not text:
            return
        try:
            print(text, flush=True)
        except (BrokenPipeError, OSError, ValueError):
            self.stdout_ok = False
            try:
                sys.stdout = open(os.devnull, "w")
            except Exception:
                pass
            print("[!] 标准输出已关闭, 继续采集并只写文件", file=sys.stderr, flush=True)

    def close(self):
        for f in (self.jf, self.cf):
            if f:
                f.close()


def parse_types(s: str) -> set[str] | None:
    s = (s or "").strip()
    if s in ("all", "*"):
        return None
    return {x.strip() for x in s.split(",") if x.strip()}


def main():
    ap = argparse.ArgumentParser(description="抖音直播全量消息采集 (弹幕+礼物+进场+点赞+互动+人气)")
    ap.add_argument("target", help="web_rid 或直播间 URL")
    ap.add_argument("--types", default=DEFAULT_TYPES,
                    help=f"终端显示的类别, 逗号分隔; all=全部 (默认 {DEFAULT_TYPES})")
    ap.add_argument("--only", default="", help="同 --types, 只显示指定类别 (如 --only gift)")
    ap.add_argument("--gift-only", action="store_true", help="只显示礼物")
    ap.add_argument("--chat-only", action="store_true", help="只显示弹幕")
    ap.add_argument("--plain", action="store_true", help="弹幕只输出正文")
    ap.add_argument("--avatar", action="store_true", help="终端里附带发消息人的头像(缩短后的 URL)")
    ap.add_argument("--jsonl", default="", help="全量 JSONL 输出路径")
    ap.add_argument("--csv", default="", help="全量 CSV 输出路径")
    ap.add_argument("--seconds", type=int, default=0, help="采集秒数, 0=不限")
    ap.add_argument("--stats-every", type=int, default=60, help="统计间隔秒, 0=关闭")
    ap.add_argument("--quiet", action="store_true", help="不打印消息, 只写文件")
    ap.add_argument("--no-color", action="store_true", help="关闭彩色输出(默认管道/重定向时自动关闭)")
    a = ap.parse_args()

    if a.no_color or os.environ.get("NO_COLOR") is not None:
        colors.disable()
    else:
        colors.enable()

    if a.gift_only:
        types = {"gift"}
    elif a.chat_only:
        types = {"chat"}
    else:
        types = parse_types(a.only or a.types)

    cli = client.DouyinLive(a.target, verbose=not a.quiet)
    try:
        info = cli.connect()
    except RuntimeError as e:
        print(f"[x] {e}")
        if "brotli" in str(e):
            print("    → 缺 brotli: pip install brotli  或  "
                  f"uv run --with brotli main.py {client.pick_web_rid(a.target)}")
        return 1

    show = "全部" if types is None else ",".join(sorted(types))
    print(f"[*] 开始采集 | 终端显示: {show} | 全量落盘: "
          f"{'JSONL' if a.jsonl else '-'}/{'CSV' if a.csv else '-'} | Ctrl+C 停止",
          file=sys.stderr, flush=True)
    if str(info.get("status")) != "2":
        print(f"[!] 房间状态 = {info.get('status')} (非 2 = 未开播/已结束), "
              f"只有历史消息, 可能长时间没新消息", file=sys.stderr, flush=True)

    sink = Sink(a.jsonl, a.csv)
    stats: dict[str, int] = {}
    gifts: list[dict] = []
    t0 = last = time.time()
    chat_n = gift_n = 0
    try:
        for rec in cli.stream(seconds=a.seconds, want=None, stats=stats):
            if rec.get("avatar"):
                rec["avatar_short"] = shorten_avatar(rec["avatar"])
            sink.save(rec)
            k = rec["kind"]
            if k == "chat":
                chat_n += 1
            elif k == "gift":
                gift_n += 1
                gifts.append(rec)
            if not a.quiet and (types is None or k in types):
                sink.show(line_of(rec, a.plain, a.avatar))
            if a.stats_every and time.time() - last >= a.stats_every:
                last = time.time()
                el = max(time.time() - t0, 1)
                print(f"[*] {el:.0f}s | 弹幕 {chat_n} ({chat_n/el*60:.1f}/min) | 礼物 {gift_n} | "
                      f"消息明细 {stats}", file=sys.stderr, flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        sink.close()

    print(f"\n[*] 结束 | 弹幕 {chat_n} 条 | 礼物 {gift_n} 条 | 全部消息 {stats}", file=sys.stderr)
    if gifts:
        from collections import Counter
        c = Counter(g.get("gift_name") or f"id{g.get('gift_id')}" for g in gifts)
        print(f"[*] 礼物明细: {dict(c)}", file=sys.stderr)
    for p in (a.jsonl, a.csv):
        if p:
            print(f"[*] 已写入 {os.path.abspath(p)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        pass
