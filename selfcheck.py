# -*- coding: utf-8 -*-
"""
selfcheck.py —— 逐层自检纯协议链路, 定位"采不到弹幕"的原因

用法:
    python selfcheck.py 343074527583
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter

import client
import proto


def main():
    rid = sys.argv[1] if len(sys.argv) > 1 else "343074527583"
    print("=" * 66)
    print("环境")
    print(f"  python        : {sys.version.split()[0]}")
    print(f"  requests      : {client.requests.__version__}")
    print(f"  brotli 解码器  : {'有' if client._brotli else '无 ← 服务端返 br 时会直接报错, 需 pip install brotli'}")
    print("=" * 66)

    cli = client.DouyinLive(rid, verbose=False)

    print("[1] GET 房间页 / 取 ttwid")
    r = cli.session.get(f"https://live.douyin.com/{cli.web_rid}", timeout=20)
    print(f"    HTTP {r.status_code} | enc={r.headers.get('Content-Encoding')} "
          f"| {len(r.content)}B | cookies={list(cli.session.cookies.keys())}")

    print("[2] GET /webcast/room/web/enter/")
    try:
        info = cli.room_info()
    except RuntimeError as e:
        print(f"    [FAIL] {e}")
        return 1
    print(f"    [OK] room_id={info['room_id']} 标题={info['title']} 状态={info['status']} "
          f"在线={info['viewer']} 主播={info['anchor']}")
    if info["status"] not in ("2", 2):
        print("    ! 该房间当前不在直播中, 推流里不会有新弹幕")

    print("[3] POST /webcast/im/fetch/ 长轮询 8 轮")
    kinds = Counter()
    chats = []
    for i in range(8):
        t0 = time.time()
        try:
            raw = cli.fetch_once()
        except Exception as e:
            print(f"    第{i+1}轮 [FAIL] {e}")
            time.sleep(2)
            continue
        recs = cli.parse(raw)
        c = Counter(x["kind"] for x in recs)
        kinds.update(c)
        chats += [x for x in recs if x["kind"] == "chat"]
        print(f"    第{i+1}轮 {len(raw)}B / {len(recs)}条 {dict(c)} "
              f"| {time.time()-t0:.2f}s | cursor={cli.cursor[:24]}...")
        for x in recs[:2]:
            print(f"         * {proto_line(x)}")
        time.sleep(max(cli.fetch_interval, 300) / 1000)

    print("[4] 汇总")
    print(f"    全部类别: {dict(kinds)}")
    print(f"    弹幕条数: {len(chats)}")
    for x in chats[:5]:
        print("    " + json.dumps(x, ensure_ascii=False))
    if not kinds:
        print("    [FAIL] 一轮都没消息: 检查网络/是否被风控(HTTP 非 200)")
    elif "chat" not in kinds:
        print("    ! 收到消息但没有弹幕: 该时段观众没发言, 或直播间弹幕本身很少")
    return 0


def proto_line(rec: dict) -> str:
    k = rec["kind"]
    if k == "chat":
        return f"弹幕 {rec.get('user','')}: {rec.get('content','')}"
    if k == "member":
        return f"进场 {rec.get('user','')}"
    if k == "gift":
        return f"礼物 {rec.get('user','')} {rec.get('gift_name','')}"
    if k == "like":
        return f"点赞 {rec.get('user','')} +{rec.get('count',0)}"
    if k == "stats":
        return f"人气 {rec.get('display','')}"
    return rec.get("method", "?")


if __name__ == "__main__":
    sys.exit(main())
