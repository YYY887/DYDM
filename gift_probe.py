# -*- coding: utf-8 -*-
"""
gift_probe.py —— 定位 WebcastGiftMessage 的真实字段号

用法:
    python gift_probe.py [web_rid ...]         # 不给参数则自动找几个在播房间
    python gift_probe.py 300000 400000 --rounds 60

需要等直播间出现礼物才会打印结构; 没有礼物时它会一直挂着轮询。
"""
from __future__ import annotations

import json
import re
import sys
import time

import client
import proto

# 重定向到文件时也逐行刷出, 方便 tail
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

# 已验证的抖音 webcast schema 字段号 (app 端另有一套, 以实测为准)
COUNT_FIELDS = [4, 5, 6, 7, 9, 12, 18, 20]
GIFT_DETAIL_FIELDS = [15, 18, 20, 21, 24]


def find_live_rooms(limit=5):
    """从分类/广场页拿真实 web_rid(URL 里那个), 校验在播后返回
    注意: /webcast/feed/ 里的 data.id_str 是内部 room_id, 不是 web_rid, 不能直接用
    """
    s = client.DouyinLive("0", verbose=False).session
    out = []
    for url in ("https://live.douyin.com/category/1", "https://live.douyin.com/category/2",
                "https://live.douyin.com/category/3", "https://live.douyin.com/"):
        try:
            html = client.decode_body(s.get(url, timeout=20)).decode("utf-8", "ignore")
        except Exception:
            continue
        for rid in re.findall(r'live\.douyin\.com/(\d{6,})', html):
            if rid not in out:
                out.append(rid)
        if len(out) >= limit * 6:
            break
    print(f"[*] 候选 web_rid {len(out)} 个, 逐个校验…")
    alive = []
    for rid in out:
        cli = client.DouyinLive(rid, verbose=False)
        try:
            cli.handshake()
            info = cli.room_info()
            if str(info.get("status")) == "2":
                alive.append((rid, info))
                print(f"[+] 在播: {rid} {info['title'][:20]} 在线={info['viewer']}")
                if len(alive) >= limit:
                    break
            else:
                print(f"[-] 跳过 {rid} status={info.get('status')}")
        except Exception as e:
            print(f"[!] {rid} {str(e)[:60]}")
    return alive


def probe(rid, rounds=40):
    cli = client.DouyinLive(rid, verbose=False)
    try:
        info = cli.connect()
    except RuntimeError as e:
        print(f"[x] {rid}: {e}")
        return None
    print(f"\n{'='*72}\n[*] {rid} room_id={info['room_id']} 状态={info['status']} "
          f"在线={info['viewer']} 标题={info['title']}\n{'='*72}")
    seen_methods = {}
    gift_samples = 0
    for i in range(rounds):
        try:
            raw = cli.fetch_once()
        except Exception as e:
            print(f"  轮{i+1} 异常 {e}")
            time.sleep(2)
            continue
        recs = cli.parse(raw)
        r = cli.last_round
        for m, c in r["methods"].items():
            seen_methods[m] = seen_methods.get(m, 0) + c
        if i < 3 or r["recv"]:
            print(f"  轮{i+1}: {r['bytes']}B recv={r['recv']} new={r['new']} dup={r['dup']} "
                  f"{r['kinds']} cursor={r['cursor'][:22]}…")
        for m in [x for x in proto.pb_all(raw, 1) if proto.pb_str(x, 1) == "WebcastGiftMessage"]:
            gift_samples += 1
            payload = proto.pb_get(m, 2, 2) or b""
            print(f"\n  ===== 捕获 WebcastGiftMessage #{gift_samples} =====")
            print("  字段号 -> 值:")
            for f, w, v in proto.pb_fields(payload):
                if w == 0:
                    print(f"    {f} (varint) = {v}")
                elif w == 2:
                    try:
                        t = v.decode("utf-8")
                        printable = t and all(c.isprintable() for c in t)
                    except UnicodeDecodeError:
                        printable = False
                    if printable and len(t) < 60:
                        print(f"    {f} (str) = {t!r}")
                    else:
                        sub = [s for s, _, _ in proto.pb_fields(v)]
                        print(f"    {f} (msg {len(v)}B) 子字段={sub}")
            print("  ---- 猜测对照 ----")
            for f in COUNT_FIELDS:
                val = proto.pb_int(payload, f)
                if val:
                    print(f"    payload.{f} = {val}   (可能是 repeat/combo/total count)")
            for f in GIFT_DETAIL_FIELDS:
                det = proto.pb_get(payload, f, 2)
                if det:
                    names = [(s, proto.pb_str(det, s)) for s in (1, 2, 3, 4, 16, 17, 18, 19)]
                    print(f"    payload.{f} 礼物详情: {[(s, x) for s, x in names if x][:4]}")
            for f in (7, 2, 21):
                u = proto.pb_get(payload, f, 2)
                if u:
                    parsed = proto.parse_user(u)
                    if parsed.get("nickname"):
                        print(f"    payload.{f} 用户: {parsed.get('nickname')} uid={parsed.get('uid')}")
                    else:
                        print(f"    payload.{f} 子字段={[s for s, _, _ in proto.pb_fields(u)][:12]}")
            if gift_samples >= 3:
                print("\n[✓] 已抓到 3 条礼物样本")
                return seen_methods
        time.sleep(max(cli.fetch_interval, 300) / 1000)
    print(f"\n[汇总] {rounds} 轮内消息类型: {seen_methods}")
    print(f"[!] 未捕获礼物 (本轮窗口该房间没人送礼物)")
    return seen_methods


def main():
    argv = sys.argv[1:]
    rounds = 40
    args = []
    i = 0
    while i < len(argv):
        if argv[i] == "--rounds":
            rounds = int(argv[i + 1])
            i += 2
            continue
        if not argv[i].startswith("-"):
            args.append(argv[i])
        i += 1
    if not args:
        print("[*] 未指定房间, 自动寻找在播房间…")
        alive = find_live_rooms(8)
        args = [rid for rid, _ in alive]
    if not args:
        print("[x] 没找到在播房间, 请手动传 web_rid")
        return 1
    for rid in args[:8]:
        probe(rid, rounds=rounds)
    return 0


if __name__ == "__main__":
    sys.exit(main())
