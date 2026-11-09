"""标识符生成：订单号、幂等键、回测号、会话号。

全部线程安全（基于 os.urandom / uuid4），不使用全局计数器。
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime


def short_id(length: int = 6) -> str:
    """返回大写字母数字短 ID。"""
    length = max(2, min(32, int(length)))
    raw = os.urandom(length * 2).hex().upper()
    alphabet = "0123456789ABCDEFGHJKLMNPQRSTUVWXYZ"  # 去掉易混的 I/O
    return "".join(alphabet[int(raw[i:i + 2], 16) % len(alphabet)] for i in range(length))


def _stamp(dt: datetime = None) -> str:
    dt = dt or datetime.now()
    return dt.strftime("%Y%m%d%H%M%S")


def new_order_id(dt: datetime = None) -> str:
    return "ORD-%s-%s" % (_stamp(dt), short_id(6))


def new_run_id(dt: datetime = None) -> str:
    return "BT-%s-%s" % ((dt or datetime.now()).strftime("%Y%m%d"), short_id(6))


def new_session_id(dt: datetime = None) -> str:
    return "LIVE-%s" % _stamp(dt)


def client_order_id(strategy_id: str, symbol: str, side: str,
                    bar_dt: datetime, seq: int) -> str:
    """幂等键：同一策略、同一标的、同一方向、同一根 bar 内唯一。"""
    raw = "%s|%s|%s|%s|%d" % (strategy_id, symbol, side, bar_dt.isoformat(), int(seq))
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12].upper()
    return "C-%s" % digest


def stable_key(*parts: str) -> str:
    """把任意字符串组合成稳定短键（用于缓存/文件名）。"""
    joined = "|".join(str(p) for p in parts)
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()[:16]
