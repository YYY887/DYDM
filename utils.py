# -*- coding: utf-8 -*-
"""utils.py —— 小工具"""

from __future__ import annotations

import time


def safe_strftime(ms, fmt="%H:%M:%S", fallback="--:--:--") -> str:
    """
    把毫秒时间戳安全地格式化。
    服务端偶尔会下发越界/异常的 create_time(例如超过 2^31 秒), time.localtime 会抛 OSError,
    这里统一兜住, 不让一条脏数据打断采集。
    """
    try:
        ts = int(ms) / 1000 if ms else 0
    except (TypeError, ValueError):
        return fallback
    if ts <= 0 or ts > 4102444800:      # 2100-01-01 之后视为异常
        return fallback
    try:
        return time.strftime(fmt, time.localtime(ts))
    except (OSError, OverflowError, ValueError):
        return fallback


def now_ms() -> int:
    return int(time.time() * 1000)
