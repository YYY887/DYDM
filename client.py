# -*- coding: utf-8 -*-
"""
client.py —— 抖音直播 纯协议客户端 (HTTP 长轮询, 无浏览器 / 无签名算法依赖)

链路:
    1) GET  https://live.douyin.com/{web_rid}                 -> 拿 ttwid cookie
    2) GET  /webcast/room/web/enter/?web_rid={web_rid}        -> 拿 真实 room_id / 主播 uid
    3) POST /webcast/im/fetch/  (cursor 长轮询, protobuf)      -> 推流消息

关键点:
    * fetch 的 body 用 form 编码, cursor 首轮传 "t-毫秒时间戳", 之后用服务端回的 cursor
    * 响应统一自解压 (gzip / deflate / br); Linux 默认 requests 不支持 br, 故必需 brotli
    * 同一批消息服务端会重复推送, 用 msg_id 去重
"""
from __future__ import annotations

import gzip
import json
import random
import re
import sys
import time
import zlib
from urllib.parse import urlparse

import requests

import proto

# Windows 控制台默认 GBK: 让输出不因个别字符直接抛异常
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

try:
    import brotli as _brotli
except ImportError:
    _brotli = None

DECODE_HEADERS = {
    "Accept-Encoding": "gzip, deflate, br",
    "Accept": "application/json, text/plain, */*",
}

BASE_HEADERS = {
    "User-Agent": UA,
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Origin": "https://live.douyin.com",
    "Referer": "https://live.douyin.com/",
    **DECODE_HEADERS,
}

COMMON_QUERY = {
    "aid": "6383",
    "app_name": "douyin_web",
    "live_id": "1",
    "device_platform": "web",
    "language": "zh-CN",
    "enter_from": "web_live",
    "cookie_enabled": "true",
    "screen_width": "1920",
    "screen_height": "1080",
    "browser_language": "zh-CN",
    "browser_platform": "Win32",
    "browser_name": "Chrome",
    "browser_version": "131.0.0.0",
}

ENTER_API = "https://live.douyin.com/webcast/room/web/enter/"
FETCH_API = "https://live.douyin.com/webcast/im/fetch/"


def decode_body(resp) -> bytes:
    """统一解压 gzip / deflate / br, 不依赖 requests 的自动解码行为"""
    enc = (resp.headers.get("Content-Encoding") or "").lower()
    raw = resp.content or b""
    try:
        if raw[:2] == b"\x1f\x8b" or enc == "gzip":
            return gzip.decompress(raw)
        if raw[:2] == b"\x78\x9c" or enc == "deflate":
            return zlib.decompress(raw, -zlib.MAX_WBITS)
        if enc == "br":
            if _brotli is None:
                raise RuntimeError("服务端返回 brotli(br) 压缩, 本机缺少 brotli 模块")
            return _brotli.decompress(raw)
    except RuntimeError:
        raise
    except Exception:
        return raw
    return raw


def pick_web_rid(target: str) -> str:
    """支持 web_rid / 完整直播间 URL / 带 query 的 URL"""
    m = re.search(r"live\.douyin\.com/(\d+)", target)
    if m:
        return m.group(1)
    m = re.search(r"(\d{6,})", urlparse(target).path or target)
    return m.group(1) if m else target.strip()


class DouyinLive:
    def __init__(self, target: str, verbose: bool = True):
        self.web_rid = pick_web_rid(target)
        self.verbose = verbose
        self.session = requests.Session()
        self.session.headers.update(BASE_HEADERS)
        self.room_id = ""
        self.anchor: dict = {}
        self.title = ""
        self.status = ""
        self.user_unique_id = ""
        self.cursor = f"t-{int(time.time() * 1000)}"
        self.internal_ext = ""
        self.fetch_interval = 1000
        self.device_id = "".join(random.choice("0123456789") for _ in range(19))
        self.seen: set[str] = set()
        self.seq = 0
        self.rounds = 0
        self.last_round: dict = {}
        self.last_http_len = 0

    def _log(self, msg: str):
        if self.verbose:
            print(msg, flush=True)

    # ---------------- 握手 ----------------
    def handshake(self):
        r = self.session.get(f"https://live.douyin.com/{self.web_rid}", timeout=20)
        self._log(f"[*] 房间页 HTTP {r.status_code} | cookies={list(self.session.cookies.keys())}")

    def room_info(self) -> dict:
        q = dict(COMMON_QUERY, web_rid=self.web_rid)
        h = dict(BASE_HEADERS, Referer="https://live.douyin.com/")
        r = self.session.get(ENTER_API, params=q, headers=h, timeout=20)
        body = decode_body(r)
        try:
            j = json.loads(body.decode("utf-8", "ignore"))
        except Exception:
            raise RuntimeError(
                f"room/enter 返回非 JSON: HTTP {r.status_code} "
                f"enc={r.headers.get('Content-Encoding')} len={len(body)} head={body[:100]!r}")
        data = j.get("data") or {}
        rooms = data.get("data") or []
        if not rooms:
            raise RuntimeError("未取到房间信息 (可能未开播/已结束/风控): "
                               + json.dumps(j, ensure_ascii=False)[:200])
        room = rooms[0]
        self.room_id = str(room.get("id_str") or room.get("id") or "")
        self.title = room.get("title", "")
        self.anchor = data.get("user") or {}
        self.user_unique_id = str(self.anchor.get("id_str") or self.anchor.get("id") or "")
        self.status = str(room.get("status_str") or room.get("status") or "")
        stats = room.get("room_view_stats") or {}
        return {
            "web_rid": self.web_rid,
            "room_id": self.room_id,
            "title": self.title,
            "status": self.status,
            "viewer": stats.get("display_long") or room.get("user_count_str") or "0",
            "like_count": room.get("like_count"),
            "anchor": self.anchor.get("nickname"),
            "anchor_id": self.user_unique_id,
        }

    # ---------------- 拉流 ----------------
    def fetch_once(self) -> bytes:
        q = dict(COMMON_QUERY)
        q.update({
            "resp_content_type": "protobuf",
            "did_rule": "3",
            "version_code": "180800",
            "webcast_sdk_version": "1.0.14-beta.0",
            "update_version_code": "1.0.14-beta.0",
        })
        form = {
            "cursor": self.cursor,
            "internal_ext": self.internal_ext,
            "device_id": self.device_id,
            "last_rtt": "0",
            "live_id": "1",
            "did_rule": "3",
            "endpoint": "live_pc",
            "support_wrds": "1",
            "user_unique_id": self.user_unique_id,
            "im_path": "/webcast/im/fetch/",
            "identity": "audience",
            "need_persist_cursor": "true",
            "ttwid": self.session.cookies.get("ttwid", ""),
            "room_id": self.room_id,
            "heartbeatDuration": "0",
            "resp_content_type": "protobuf",
            "version_code": "180800",
        }
        h = dict(BASE_HEADERS)
        h["Content-Type"] = "application/x-www-form-urlencoded"
        h["Referer"] = f"https://live.douyin.com/{self.web_rid}"
        r = self.session.post(FETCH_API, params=q, data=form, headers=h, timeout=20)
        if r.status_code != 200:
            raise RuntimeError(f"fetch HTTP {r.status_code}")
        return decode_body(r)

    def parse(self, raw: bytes) -> list[dict]:
        recs, cursor, interval, iext = proto.parse_response(raw)
        self.last_raw_len = len(raw)
        self.last_http_len = len(raw)
        self.last_round = {
            "bytes": len(raw),
            "recv": len(recs),
            "new": 0,
            "cursor": cursor,
            "interval": self.fetch_interval,
            "ext": bool(iext),
            "kinds": {},
            "methods": {},
            "dup": 0,
        }
        if cursor:
            self.cursor = cursor
        if iext:
            self.internal_ext = iext
        if interval:
            self.fetch_interval = max(interval, 300)
        out = []
        for rec in recs:
            self.last_round["kinds"][rec["kind"]] = self.last_round["kinds"].get(rec["kind"], 0) + 1
            self.last_round["methods"][rec["method"]] = \
                self.last_round["methods"].get(rec["method"], 0) + 1
            key = rec.get("msg_id") or ""
            if key and key in self.seen:      # 去重
                self.last_round["dup"] += 1
                continue
            if key:
                self.seen.add(key)
            rec["seq"] = self.seq
            rec["recv_at"] = int(time.time() * 1000)
            self.seq += 1
            out.append(rec)
        self.last_round["new"] = len(out)
        self.rounds += 1
        return out

    def connect(self) -> dict:
        self.handshake()
        info = self.room_info()
        self._log(f"[*] 房间 {info['room_id']} | 标题: {info['title']} | "
                  f"主播: {info['anchor']}({info['anchor_id']}) | 在线: {info['viewer']}")
        return info

    # ---------------- 主循环 ----------------
    def stream(self, seconds=0, want=("chat",), on_record=None, stats: dict | None = None,
               backoff_max=15):
        """
        持续产出消息; want=None 表示全部类别。
        stats 传 dict 时会把各类别计数累加进去。
        """
        if stats is None:
            stats = {}
        t0 = time.time()
        err = 0
        while True:
            if seconds and time.time() - t0 >= seconds:
                return stats
            try:
                raw = self.fetch_once()
                err = 0
            except Exception as e:
                err += 1
                wait = min(2 ** err, backoff_max)
                print(f"[!] fetch 异常({err}): {e} -> {wait}s 后重试", flush=True)
                time.sleep(wait)
                continue
            for rec in self.parse(raw):
                stats[rec["kind"]] = stats.get(rec["kind"], 0) + 1
                if on_record:
                    on_record(rec)
                if want is None or rec["kind"] in want:
                    yield rec
            time.sleep(max(self.fetch_interval, 300) / 1000.0)
