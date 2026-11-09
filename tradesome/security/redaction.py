"""敏感信息脱敏：日志、界面、导出共用一套规则。"""

from __future__ import annotations

import re
from typing import Optional

SECRET_KEYS = ("api_key", "apikey", "access_token", "token", "secret",
               "password", "passwd", "authorization", "private_key")

_SECRET_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|token|secret|password|passwd|authorization)"
    r"\b\s*[:=]\s*([^\s,;'\"]+)")
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}")


def redact_text(text: Optional[str]) -> str:
    if not text:
        return ""
    # 先处理 Bearer，否则 "Authorization: Bearer xxx" 会被键值规则先吃掉 Bearer，
    # 令牌本体反而原样留在日志里。
    masked = _BEARER_PATTERN.sub("Bearer ***", str(text))
    return _SECRET_PATTERN.sub(lambda m: "%s=***" % m.group(1), masked)


def mask_secret(value: Optional[str], head: int = 3, tail: int = 4) -> str:
    if not value:
        return ""
    text = str(value)
    if len(text) <= head + tail:
        return "*" * len(text)
    return text[:head] + "****" + text[-tail:]


def looks_secret(key: str) -> bool:
    low = (key or "").lower()
    return any(word in low for word in SECRET_KEYS)
