# -*- coding: utf-8 -*-
"""
smoketest.py —— 离线自测 (不需要直播间在播)

验证: 输出格式 / 只显示弹幕 / 管道断裂不丢数据 / JSONL+CSV 落盘 / protobuf 解析

用法:
    python smoketest.py
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import time

import danmaku
import proto

FAKE = [
    {"method": "WebcastChatMessage", "kind": "chat", "msg_id": "1", "room_id": "7700000000000000000",
     "create_time": 1794202900000, "user": "观众A", "uid": "123", "content": "测试弹幕 one"},
    {"method": "WebcastMemberMessage", "kind": "member", "msg_id": "2", "user": "观众B", "uid": "124"},
    {"method": "WebcastGiftMessage", "kind": "gift", "msg_id": "3", "user": "观众C",
     "gift_name": "小心心", "repeat_count": 3},
    {"method": "WebcastChatMessage", "kind": "chat", "msg_id": "4",
     "create_time": 1794202901000, "user": "观众D", "content": "测试弹幕 two"},
]


class FakeStream:
    """冒充 client.DouyinLive.stream"""

    def __init__(self, recs, stats):
        self.recs, self.stats = recs, stats

    def __call__(self, seconds=0, want=None, stats=None, on_record=None, backoff_max=15):
        for r in self.recs:
            self.stats[r["kind"]] = self.stats.get(r["kind"], 0) + 1
            if stats is not None:
                stats[r["kind"]] = stats.get(r["kind"], 0) + 1
            yield dict(r)


def run_with_fake(args, recs=None):
    """把 danmaku.main 的 stream 换成假的, 并捕获 stdout"""
    import client
    recs = recs if recs is not None else FAKE
    real_stream = client.DouyinLive.stream
    real_connect = client.DouyinLive.connect
    client.DouyinLive.connect = lambda self: {"room_id": "7700000000000000000",
                                              "title": "自测房间", "anchor": "主播X",
                                              "anchor_id": "1", "viewer": "1"}
    client.DouyinLive.stream = lambda self, **kw: FakeStream(recs, {})(**kw)
    buf = io.StringIO()
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = buf, io.StringIO()
    argv = sys.argv
    sys.argv = ["danmaku.py", "000000000000"] + args
    try:
        rc = danmaku.main()
    finally:
        sys.stdout, sys.stderr = old_out, old_err
        sys.argv = argv
        client.DouyinLive.stream = real_stream
        client.DouyinLive.connect = real_connect
    return rc, buf.getvalue()


def main():
    ok = True

    print("=" * 60)
    print("[1] 默认: 终端只出弹幕")
    rc, out = run_with_fake([])
    lines = [x for x in out.splitlines() if "[" not in x[:1]]
    chat_lines = [x for x in out.splitlines() if "：测试弹幕" in x]
    others = [x for x in out.splitlines() if any(k in x for k in ("进入直播间", "送出", "📊"))]
    print(f"    弹幕行 {len(chat_lines)} 条, 非弹幕行 {len(others)} 条")
    for x in chat_lines:
        print("      " + x)
    if len(chat_lines) != 2 or others:
        print("    [FAIL] 期望 2 条弹幕、0 条非弹幕"); ok = False
    else:
        print("    [OK] 通过")

    print("[2] --plain: 只有正文")
    rc, out = run_with_fake(["--plain"])
    body = [x for x in out.splitlines() if x.startswith("测试弹幕")]
    print(f"    正文行 {len(body)} 条: {body}")
    if len(body) != 2:
        print("    [FAIL] 期望 2 行正文"); ok = False
    else:
        print("    [OK] 通过")

    print("[3] --show-all: 非弹幕也上屏")
    rc, out = run_with_fake(["--show-all"])
    if any(k in out for k in ("进入直播间", "送出", "📊")):
        print("    [OK] 通过")
    else:
        print("    [FAIL] 期望出现 进场/礼物/人气"); ok = False

    print("[4] 落盘: JSONL 全量 + CSV, 且 stdout 断裂也不丢")
    d = tempfile.mkdtemp()
    jp, cp = os.path.join(d, "r.jsonl"), os.path.join(d, "r.csv")
    import client
    real_print = print

    class Boom(io.StringIO):          # 模拟管道被提前关闭
        def write(self, *a, **k):
            raise BrokenPipeError("模拟管道断裂")

    client.DouyinLive.stream = lambda self, **kw: FakeStream(FAKE, {})(**kw)
    client.DouyinLive.connect = lambda self: {"room_id": "1", "title": "t", "anchor": "a",
                                              "anchor_id": "1", "viewer": "1"}
    old_out, old_err, old_argv = sys.stdout, sys.stderr, sys.argv
    sys.stdout, sys.stderr = Boom(), io.StringIO()
    sys.argv = ["danmaku.py", "000000000000", "--jsonl", jp, "--csv", cp]
    try:
        rc = danmaku.main()
    except BrokenPipeError:
        rc = -1
    finally:
        sys.stdout, sys.stderr, sys.argv = old_out, old_err, old_argv
    rows = [json.loads(l) for l in open(jp, encoding="utf-8")]
    csv_lines = open(cp, encoding="utf-8-sig").read().strip().splitlines()
    print(f"    stdout 断裂后 JSONL 落盘 {len(rows)} 条, CSV {len(csv_lines)-1} 行")
    if len(rows) == len(FAKE) and len(csv_lines) - 1 == len(FAKE):
        print("    [OK] 通过")
    else:
        print("    [FAIL] 期望落盘全部 4 条"); ok = False

    print("[5] protobuf 解析: 字段号回环")
    def v(n):
        out = b""
        while True:
            b = n & 0x7F
            n >>= 7
            out += bytes([b | (0x80 if n else 0)])
            if not n:
                return out

    def lenf(fno, payload):
        return v(fno << 3 | 2) + v(len(payload)) + payload

    def varf(fno, n):
        return v(fno << 3) + v(n)

    user = (lenf(1, b"111111") + lenf(3, "昵称".encode())
            + varf(73, 987654321) + lenf(46, b"SEC_UID"))
    payload = lenf(1, lenf(1, b"WebcastChatMessage")) + lenf(2, user) + lenf(3, "你好世界".encode())
    msg = lenf(1, b"WebcastChatMessage") + lenf(2, payload) + varf(3, 42)
    resp = (lenf(1, msg) + lenf(2, b"t-123_r-456") + varf(3, 1000) + lenf(5, b"dim|wss"))
    recs, cursor, interval, iext = proto.parse_response(resp)
    r = recs[0] if recs else {}
    print(f"    {json.dumps(r, ensure_ascii=False)}")
    print(f"    cursor={cursor} interval={interval} ext={iext}")
    if (r.get("kind") == "chat" and r.get("content") == "你好世界"
            and r.get("user") == "昵称" and r.get("uid") == "987654321"
            and cursor == "t-123_r-456" and interval == 1000):
        print("    [OK] 通过")
    else:
        print("    [FAIL] 解析结果不符"); ok = False

    print("=" * 60)
    print("全部通过 [OK]" if ok else "存在失败 [FAIL]")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
