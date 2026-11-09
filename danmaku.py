# -*- coding: utf-8 -*-
"""
danmaku.py —— 抖音直播弹幕采集 (纯协议, 默认只输出弹幕)

用法:
    python danmaku.py <web_rid 或 直播间URL> [选项]

选项:
    --plain           只输出弹幕正文(不带时间和昵称)
    --jsonl FILE      全字段追加写入 JSONL (含弹幕/礼物/进场等)
    --csv FILE        追加写入 CSV (可直接 Excel 打开)
    --seconds N       采集 N 秒后退出 (0 = 一直跑, Ctrl+C 停止)
    --show-all        终端同时打印 进场/礼物/点赞/人气 等非弹幕消息
    --stats-every N   每 N 秒在 stderr 打印一次统计 (默认 60, 0=关闭)

示例:
    python danmaku.py 343074527583
    python danmaku.py "https://live.douyin.com/343074527583" --plain --jsonl data/room.jsonl
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


def ts_of(rec: dict) -> str:
    return utils.safe_strftime(rec.get("create_time") or rec.get("recv_at"))


class Out:
    """把 stdout 包成"管道断了也不崩"的写出器。

    被 `| head`、`| Select-Object -First N`、`> $null`(PowerShell) 等提前关闭时,
    Python 默认会抛 BrokenPipeError 直接把进程干掉, 导致落盘数据被截断。
    """

    def __init__(self):
        self.ok = True

    def write(self, line: str):
        if not self.ok or not line:
            return
        try:
            print(line, flush=True)
        except (BrokenPipeError, OSError, ValueError):
            self.ok = False
            try:
                sys.stdout = open(os.devnull, "w")
            except Exception:
                pass
            print("[!] 标准输出已关闭(管道断裂), 继续采集但只写文件", file=sys.stderr, flush=True)


def line_of(rec: dict, plain: bool = False) -> str:
    k = rec["kind"]
    if k == "chat":
        return rec.get("content", "") if plain else f"[{ts_of(rec)}] {rec.get('user','')}：{rec.get('content','')}"
    t = ts_of(rec)
    if k == "gift":
        return f"[{t}] 🎁 {rec.get('user','')} 送出 {rec.get('gift_name','')} x{rec.get('repeat_count',1)}"
    if k == "member":
        return f"[{t}] ➕ {rec.get('user','')} 进入直播间"
    if k == "like":
        return f"[{t}] 👍 {rec.get('user','')} 点赞 +{rec.get('count',0)} (累计 {rec.get('total',0)})"
    if k == "social":
        return f"[{t}] 🔗 {rec.get('user','')} 互动 action={rec.get('action')}"
    if k == "stats":
        return f"[{t}] 📊 在线 {rec.get('display','') or rec.get('viewer',0)}"
    if k == "control":
        return f"[{t}] ⚠ 控制 status={rec.get('status')} {rec.get('text','')}"
    return ""


CSV_FIELDS = ["time", "kind", "user", "uid", "content", "gift_name",
              "count", "total", "viewer", "msg_id", "method"]


class Writers:
    def __init__(self, jsonl_path="", csv_path=""):
        self.jf = self.cf = None
        self.cw = None
        if jsonl_path:
            os.makedirs(os.path.dirname(os.path.abspath(jsonl_path)), exist_ok=True)
            self.jf = open(jsonl_path, "a", encoding="utf-8")
        if csv_path:
            os.makedirs(os.path.dirname(os.path.abspath(csv_path)), exist_ok=True)
            fresh = not os.path.exists(csv_path) or os.path.getsize(csv_path) == 0
            self.cf = open(csv_path, "a", encoding="utf-8-sig", newline="")
            self.cw = csv.DictWriter(self.cf, fieldnames=CSV_FIELDS, extrasaction="ignore")
            if fresh:
                self.cw.writeheader()

    def write(self, rec: dict):
        if self.jf:
            self.jf.write(json.dumps(rec, ensure_ascii=False) + "\n")
            self.jf.flush()
        if self.cw:
            row = dict(rec)
            row["time"] = time.strftime(
                "%Y-%m-%d %H:%M:%S",
                time.localtime((rec.get("create_time") or rec.get("recv_at") or 0) / 1000))
            self.cw.writerow(row)
            self.cf.flush()

    def close(self):
        for f in (self.jf, self.cf):
            if f:
                f.close()


def main():
    ap = argparse.ArgumentParser(description="抖音直播弹幕采集 (纯协议)")
    ap.add_argument("target", help="web_rid 或直播间 URL")
    ap.add_argument("--plain", action="store_true", help="只输出弹幕正文")
    ap.add_argument("--jsonl", default="", help="全字段 JSONL 输出路径")
    ap.add_argument("--csv", default="", help="CSV 输出路径")
    ap.add_argument("--seconds", type=int, default=0, help="采集秒数, 0=不限")
    ap.add_argument("--show-all", action="store_true", help="终端也打印非弹幕消息")
    ap.add_argument("--stats-every", type=int, default=60, help="统计打印间隔秒, 0=关闭")
    a = ap.parse_args()

    cli = client.DouyinLive(a.target)
    try:
        info = cli.connect()
    except RuntimeError as e:
        print(f"[x] {e}")
        if "brotli" in str(e):
            print("    → 缺 brotli 解码器, 任选一种:")
            print("        pip install brotli")
            print(f"        uv run --with brotli danmaku.py {client.pick_web_rid(a.target)}")
        return 1

    print(f"[*] 开始采集弹幕 (Ctrl+C 停止){'' if a.plain else ' — 只显示弹幕'}",
          file=sys.stderr, flush=True)
    if str(info.get("status")) != "2":
        print(f"[!] 提示: 房间状态 = {info.get('status')} (非 2 = 未开播/已结束), "
              f"推流里只有历史消息, 可能长时间没有新弹幕", file=sys.stderr, flush=True)
    w = Writers(a.jsonl, a.csv)
    out = Out()
    display_kinds = None if a.show_all else {"chat"}   # 终端只显示弹幕; 文件始终全量
    stats: dict[str, int] = {}
    last = time.time()
    t0 = last
    chat_n = 0
    try:
        for rec in cli.stream(seconds=a.seconds, want=None, stats=stats):
            if rec["kind"] == "chat":
                chat_n += 1
            if w.jf or w.cf:                 # 落盘: 全量消息
                w.write(rec)
            if display_kinds is None or rec["kind"] in display_kinds:
                out.write(line_of(rec, a.plain))
            if a.stats_every and time.time() - last >= a.stats_every:
                last = time.time()
                el = max(time.time() - t0, 1)
                print(f"[*] {el:.0f}s | 弹幕 {chat_n} ({chat_n / el * 60:.1f}/min) | "
                      f"消息明细 {stats}", file=sys.stderr, flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        w.close()
    print(f"\n[*] 结束 | 弹幕共 {chat_n} 条 | 全部消息 {stats}", file=sys.stderr)
    for p in (a.jsonl, a.csv):
        if p:
            print(f"[*] 已写入 {os.path.abspath(p)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        pass
