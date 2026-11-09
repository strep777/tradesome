"""输入校验与小工具：路径、代码格式、文件名、CSV 公式注入防护。"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable, Optional

from ..core.errors import SecurityError, ValidationError

SYMBOL_PATTERN = re.compile(r"^[0-9]{6}\.(SH|SZ|BJ)$")
SAFE_NAME_PATTERN = re.compile(r"^[^\\/:*?\"<>|\r\n]{1,120}$")
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def validate_symbol(symbol: str) -> str:
    text = (symbol or "").strip().upper()
    if not SYMBOL_PATTERN.match(text):
        raise ValidationError("标的代码格式应为 600519.SH 这样的形式：%s" % symbol)
    return text


def validate_filename(name: str, suffix: str = "") -> str:
    text = (name or "").strip()
    if not text or not SAFE_NAME_PATTERN.match(text) or set(text) <= {"."}:
        raise ValidationError("文件名不合法：%s" % name)
    if suffix and not text.lower().endswith(suffix.lower()):
        raise ValidationError("文件扩展名应为 %s" % suffix)
    return text


def safe_join(base: Path, *parts: str) -> Path:
    """只允许落在 base 目录内，拒绝绝对路径、盘符与 .. 穿越。"""
    root = Path(base).resolve()
    for part in parts:
        text = str(part)
        if not text:
            continue
        if os.path.isabs(text) or re.match(r"^[A-Za-z]:", text):
            raise SecurityError("不允许使用绝对路径：%s" % text)
        if ".." in Path(text).parts:
            raise SecurityError("路径中不允许出现 ..：%s" % text)
    target = (root.joinpath(*[str(p) for p in parts])).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise SecurityError("路径越界：%s" % target)
    return target


def sanitize_csv_cell(value) -> str:
    """防止导出的 CSV 在 Excel 中被当成公式执行。"""
    text = "" if value is None else str(value)
    if text.startswith(_FORMULA_PREFIXES):
        return "'" + text
    return text


def sanitize_csv_row(values: Iterable) -> list:
    return [sanitize_csv_cell(v) for v in values]


def clamp_text(text: Optional[str], limit: int = 2000) -> str:
    value = "" if text is None else str(text)
    return value if len(value) <= limit else value[:limit] + "…"


def is_safe_text(text: str) -> bool:
    return "\x00" not in (text or "")
