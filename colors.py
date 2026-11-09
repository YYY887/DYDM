# -*- coding: utf-8 -*-
"""
colors.py —— 终端配色（ANSI）

- 管道/重定向/无 TTY 时自动关闭颜色；环境变量 NO_COLOR 也关闭
- Windows 下尝试打开 VT 处理，失败则整体降级为无色
- 类别 -> 颜色映射集中在这里，改配色只改这一处
"""
from __future__ import annotations

import os
import sys

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"

# 类别 -> (标签色, 昵称色, 内容色)
KIND_STYLE = {
    "chat":    ("\033[36m", "\033[1;36m", "\033[97m"),   # 弹幕: 青
    "gift":    ("\033[33m", "\033[1;33m", "\033[93m"),   # 礼物: 金
    "member":  ("\033[90m", "\033[90m",   "\033[90m"),   # 进场: 灰
    "like":    ("\033[35m", "\033[1;35m", "\033[95m"),   # 点赞: 紫
    "social":  ("\033[34m", "\033[1;34m", "\033[94m"),   # 关注/分享: 蓝
    "stats":   ("\033[32m", "\033[32m",   "\033[92m"),   # 人气: 绿
    "control": ("\033[31m", "\033[31m",   "\033[91m"),   # 控制/警告: 红
    "fansclub": ("\033[33m", "\033[33m",  "\033[93m"),
    "other":   ("\033[90m", "\033[90m",   "\033[90m"),
}
TIME_COLOR = "\033[2;37m"

_enabled = False


def _is_tty(stream) -> bool:
    try:
        return bool(stream) and stream.isatty()
    except Exception:
        return False


def enable() -> bool:
    """在支持的终端里打开 ANSI 颜色; 返回最终是否启用"""
    global _enabled
    if os.environ.get("NO_COLOR") is not None:
        _enabled = False
        return False
    if not (_is_tty(sys.stdout) and _is_tty(sys.stderr)):
        _enabled = False
        return False
    if os.name == "nt":
        try:
            import ctypes
            k = ctypes.windll.kernel32
            h = k.GetStdHandle(-11)
            mode = ctypes.c_uint32()
            if not k.GetConsoleMode(h, ctypes.byref(mode)):
                _enabled = False
                return False
            k.SetConsoleMode(h, mode.value | 0x0004)   # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        except Exception:
            _enabled = False
            return False
    _enabled = True
    return True


def enabled() -> bool:
    return _enabled


def disable():
    global _enabled
    _enabled = False


def c(text: str, code: str) -> str:
    """给文本上色（未启用时原样返回）"""
    if not _enabled or not code or not text:
        return text
    return f"{code}{text}{RESET}"


def colorize_line(rec: dict, text: str, kind: str | None = None) -> str:
    """按消息类别给整行上色: 时间暗色, 标签与昵称用类别的强调色"""
    if not _enabled or not text:
        return text
    k = kind or rec.get("kind", "other")
    tag_color, nick_color, body_color = KIND_STYLE.get(k, KIND_STYLE["other"])
    # 统一格式: [时间] [标签] 昵称... 内容
    # 时间
    if text.startswith("["):
        end = text.find("]")
        if end > 0:
            text = c(text[:end + 1], TIME_COLOR) + text[end + 1:]
    # 标签
    i = text.find("] [")
    if i >= 0:
        j = text.find("]", i + 3)
        if j > 0:
            text = text[:i + 2] + c(text[i + 2:j + 1], tag_color) + text[j + 1:]
    return c(text, "") if False else text


def tag(rec: dict, label: str) -> str:
    """给标签配色的小工具"""
    color = KIND_STYLE.get(rec.get("kind", "other"), KIND_STYLE["other"])[0]
    return c(label, color)


def nick(rec: dict, name: str) -> str:
    color = KIND_STYLE.get(rec.get("kind", "other"), KIND_STYLE["other"])[1]
    return c(name, color)


def body(rec: dict, text: str) -> str:
    color = KIND_STYLE.get(rec.get("kind", "other"), KIND_STYLE["other"])[2]
    return c(text, color)


def marked(rec: dict, kind_label: str, name: str, tail: str) -> str:
    """组合出: [颜色标签] 彩色昵称 尾部内容"""
    if not _enabled:
        return f"{kind_label} {name}{tail}"
    return f"{tag(rec, kind_label)} {nick(rec, name)}{body(rec, tail)}"
