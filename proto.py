# -*- coding: utf-8 -*-
"""
proto.py —— 最小 protobuf wire 解析 + 抖音直播消息结构解析

不依赖 protoc / .proto 编译, 直接按 protobuf wire format 读字段:
    0=varint  1=64bit  2=length-delimited  5=32bit

字段号来源: 抖音 webcast protobuf schema + 实际抓包核对(见 README 备注)
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# 1. wire 层
# ---------------------------------------------------------------------------


def _varint(buf: bytes, i: int):
    r = s = 0
    while True:
        b = buf[i]
        i += 1
        r |= (b & 0x7F) << s
        if not (b & 0x80):
            return r, i
        s += 7


def pb_fields(buf: bytes):
    """yield (field_number, wire_type, value)；wire_type=2 时 value 是 bytes"""
    i, n = 0, len(buf or b"")
    while i < n:
        try:
            key, i = _varint(buf, i)
        except IndexError:
            return
        fno, wt = key >> 3, key & 7
        try:
            if wt == 0:
                v, i = _varint(buf, i)
            elif wt == 2:
                ln, i = _varint(buf, i)
                v = buf[i:i + ln]
                i += ln
            elif wt == 5:
                v, i = buf[i:i + 4], i + 4
            elif wt == 1:
                v, i = buf[i:i + 8], i + 8
            else:
                return
        except IndexError:
            return
        yield fno, wt, v


def pb_get(buf: bytes, fno: int, wt: int = 2, default=None):
    for f, w, v in pb_fields(buf):
        if f == fno and w == wt:
            return v
    return default


def pb_all(buf: bytes, fno: int, wt: int = 2):
    return [v for f, w, v in pb_fields(buf) if f == fno and w == wt]


def pb_str(buf: bytes, fno: int, default: str = "") -> str:
    v = pb_get(buf, fno, 2)
    return default if v is None else v.decode("utf-8", "ignore")


def pb_int(buf: bytes, fno: int, default: int = 0) -> int:
    v = pb_get(buf, fno, 0)
    return default if v is None else v


# ---------------------------------------------------------------------------
# 2. 业务结构
# ---------------------------------------------------------------------------

# method -> 语义类别
MESSAGE_KINDS = {
    "WebcastChatMessage": "chat",       # 弹幕
    "WebcastGiftMessage": "gift",       # 礼物
    "WebcastMemberMessage": "member",   # 进场
    "WebcastLikeMessage": "like",       # 点赞
    "WebcastSocialMessage": "social",   # 关注/分享
    "WebcastRoomUserSeqMessage": "stats",
    "WebcastRoomStatsMessage": "stats",
    "WebcastControlMessage": "control",
    "WebcastFansclubMessage": "fansclub",
    "WebcastEmojiChatMessage": "chat",
    "WebcastHotChatMessage": "chat",
}


def parse_user(ub: bytes) -> dict:
    """
    User 结构:
      1=打码 uid(web 端常为 111111)  2=short_id  3=nickname  4=gender  5=level
      9=avatar(1=url_list, 2=uri, 5=color)   46=sec_uid   73=真实 uid(数字)
      1028=打码 uid
    头像: 9.1 是 URL 列表(抖音 p11/p3 CDN 同一张图的多个域名), 取第一个即可
    """
    if not ub:
        return {}
    real_id = pb_int(ub, 73)
    avatar = pb_get(ub, 9, 2)
    u = {
        "uid": str(real_id) if real_id else pb_str(ub, 1028) or pb_str(ub, 1),
        "nickname": pb_str(ub, 3),
        "gender": pb_int(ub, 4),
        "level": pb_int(ub, 5),
        "sec_uid": pb_str(ub, 46),
        "short_id": pb_str(ub, 2),
    }
    if avatar:
        urls = [x.decode("utf-8", "ignore") for x in pb_all(avatar, 1)]
        if urls:
            u["avatar"] = urls[0]
            if len(urls) > 1:
                u["avatar_backup"] = urls[1]
        uri = pb_str(avatar, 2)
        if uri:
            u["avatar_uri"] = uri
    return u


def parse_common(cb: bytes) -> dict:
    """Common: 1=method 2=msg_id 3=room_id 4=create_time(秒) 15=create_time(毫秒)"""
    return {
        "method": pb_str(cb, 1),
        "msg_id": str(pb_int(cb, 2)),
        "room_id": str(pb_int(cb, 3)),
        "create_time": pb_int(cb, 15) or pb_int(cb, 4) * 1000,
    }


def _looks_text(b: bytes) -> bool:
    """粗判一段 bytes 是不是可读文本(以避开二进制子结构)"""
    try:
        t = b.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return bool(t) and all(c.isprintable() for c in t)


def _user_fields(user: dict) -> dict:
    """把 User 里对使用方有用的字段摊平到消息记录上 (含头像)"""
    out = {"user": user.get("nickname", ""), "uid": user.get("uid", "")}
    for k in ("avatar", "avatar_uri", "sec_uid", "level", "gender"):
        if user.get(k):
            out[k] = user[k]
    return out


def parse_message(msg: bytes) -> dict | None:
    """PushFrame 里的一条消息 -> dict"""
    method = pb_str(msg, 1)
    payload = pb_get(msg, 2, 2)
    if payload is None:
        return None
    kind = MESSAGE_KINDS.get(method, "other")
    rec = {
        "method": method,
        "kind": kind,
        "msg_id": str(pb_int(msg, 3)),
    }
    rec.update({k: v for k, v in parse_common(pb_get(payload, 1, 2) or b"").items()
                if k != "method" and v})

    if method in ("WebcastChatMessage", "WebcastEmojiChatMessage", "WebcastHotChatMessage"):
        user = parse_user(pb_get(payload, 2, 2) or b"")
        rec.update(_user_fields(user))
        rec["content"] = pb_str(payload, 3) or pb_str(payload, 4)
    elif method == "WebcastGiftMessage":
        user = parse_user(pb_get(payload, 7, 2) or b"")
        gift = pb_get(payload, 15, 2) or b""
        rec.update(_user_fields(user))
        rec.update({
            "gift_id": pb_int(payload, 2),
            "gift_name": pb_str(gift, 16),
            "repeat_count": pb_int(payload, 5) or 1,
            "combo_count": pb_int(payload, 6),
            "total_count": pb_int(payload, 18),
        })
        if not rec.get("gift_name") or not user:
            # 字段号对不上时留证据: 全部数字字段 + 内层字符串, 便于离线修正映射
            rec["gift_unknown_fields"] = {f: v for f, w, v in pb_fields(payload) if w == 0}
            rec["gift_unknown_texts"] = [
                v.decode("utf-8", "ignore") for f, w, v in pb_fields(payload)
                if w == 2 and 0 < len(v) <= 40 and _looks_text(v)
            ][:6]
            if not rec["gift_unknown_texts"]:
                rec.pop("gift_unknown_texts")
    elif method == "WebcastMemberMessage":
        user = parse_user(pb_get(payload, 2, 2) or b"")
        rec.update(_user_fields(user))
        rec["viewer_count"] = pb_int(payload, 3)
    elif method == "WebcastLikeMessage":
        user = parse_user(pb_get(payload, 5, 2) or b"")
        rec.update(_user_fields(user))
        rec.update({"count": pb_int(payload, 2), "total": pb_int(payload, 3)})
    elif method == "WebcastSocialMessage":
        user = parse_user(pb_get(payload, 2, 2) or b"")
        rec.update(_user_fields(user))
        rec["action"] = pb_int(payload, 5)
    elif kind == "stats":
        display = pb_str(payload, 4) or pb_str(payload, 3)
        n = pb_int(payload, 3) or pb_int(payload, 5)
        if not n and display:
            m = re.search(r"(\d+)", display)
            n = int(m.group(1)) if m else 0
        rec.update({"viewer": n, "display": display})
    elif method == "WebcastControlMessage":
        rec.update({"status": pb_int(payload, 2), "text": pb_str(payload, 3)})

    return {k: v for k, v in rec.items() if v != "" and v is not None}


def parse_response(raw: bytes):
    """
    WebcastResponse:
      1=消息列表  2=cursor  3=fetch_interval(ms)  5=internal_ext  9=need_ack
    -> (records, cursor, fetch_interval, internal_ext)
    """
    msgs, cursor, interval, iext = [], "", None, ""
    for f, w, v in pb_fields(raw):
        if f == 1 and w == 2:
            msgs.append(v)
        elif f == 2 and w == 2:
            cursor = v.decode("utf-8", "ignore")
        elif f == 3 and w == 0:
            interval = v
        elif f == 5 and w == 2:
            iext = v.decode("utf-8", "ignore")
    recs = [r for r in (parse_message(m) for m in msgs) if r]
    return recs, cursor, interval, iext


def dump_tree(buf: bytes, depth: int = 0, maxdepth: int = 4, path: str = ""):
    """调试用: 递归打印字段树"""
    pad = "  " * depth
    for fno, wt, v in pb_fields(buf):
        p = f"{path}.{fno}" if path else str(fno)
        if wt == 2:
            try:
                t = v.decode("utf-8")
                if t and all(c.isprintable() or c in "\r\n\t" for c in t):
                    print(f"{pad}{p} (str) = {t[:120]!r}")
                    continue
            except UnicodeDecodeError:
                pass
            if depth < maxdepth:
                print(f"{pad}{p} (msg {len(v)}B)")
                dump_tree(v, depth + 1, maxdepth, p)
            else:
                print(f"{pad}{p} (bytes {len(v)}B)")
        else:
            print(f"{pad}{p} (wt{wt}) = {v}")
