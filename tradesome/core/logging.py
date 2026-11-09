"""日志：滚动文件 + 控制台 + 密钥脱敏。

日志目录不可写时降级为空处理器，绝不让"写日志失败"拖垮程序启动。
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import re
import sys
from pathlib import Path
from typing import Optional, Union

LOGGER_NAME = "tradesome"
LOG_FILENAME = "tradesome.log"
_MAX_BYTES = 5 * 1024 * 1024
_BACKUPS = 5
_FORMAT = "%(asctime)s.%(msecs)03d %(levelname)-7s %(name)s %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# 只需覆盖最常见的几种写法，宁可多遮也不漏遮
_SECRET_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|token|secret|password|passwd|authorization)"
    r"(\s*[:=]\s*)([^\s,;\"']+)"
)


class RedactFilter(logging.Filter):
    """把日志中的密钥替换为 ***。"""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - 格式化失败时原样放行
            return True
        cleaned = _SECRET_PATTERN.sub(lambda m: "%s%s***" % (m.group(1), m.group(2)), message)
        if cleaned != message:
            record.msg = cleaned
            record.args = ()
        return True


def redact_text(text: str) -> str:
    """对任意文本做同样的脱敏（审计、导出复用）。"""
    return _SECRET_PATTERN.sub(lambda m: "%s%s***" % (m.group(1), m.group(2)), str(text))


def _existing_handlers(logger: logging.Logger):
    return [h for h in logger.handlers if getattr(h, "_tradesome", False)]


def setup_logging(log_dir: Union[str, Path, None] = None, level: str = "INFO",
                  console: bool = True, filename: str = LOG_FILENAME) -> logging.Logger:
    """初始化 tradesome 日志器；重复调用会替换旧处理器。"""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(_level_of(level))
    logger.propagate = False
    for handler in _existing_handlers(logger):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:  # noqa: BLE001
            pass
    formatter = logging.Formatter(_FORMAT, datefmt=_DATE_FORMAT)
    redactor = RedactFilter()

    if log_dir:
        try:
            directory = Path(log_dir)
            directory.mkdir(parents=True, exist_ok=True)
            file_handler = logging.handlers.RotatingFileHandler(
                str(directory / filename), maxBytes=_MAX_BYTES,
                backupCount=_BACKUPS, encoding="utf-8", delay=True,
            )
            file_handler.setFormatter(formatter)
            file_handler.addFilter(redactor)
            file_handler._tradesome = True  # type: ignore[attr-defined]
            logger.addHandler(file_handler)
        except OSError as exc:
            logger.addHandler(logging.NullHandler())
            logger.warning("日志目录不可写（%s），仅输出到控制台", exc)

    if console and sys.stderr is not None:
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        stream.addFilter(redactor)
        stream._tradesome = True  # type: ignore[attr-defined]
        logger.addHandler(stream)

    if not logger.handlers:
        logger.addHandler(logging.NullHandler())
    return logger


def _level_of(level: str) -> int:
    text = str(level or "INFO").upper()
    return getattr(logging, text, logging.INFO) if text in (
        "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL") else logging.INFO


def get_logger(name: str = "") -> logging.Logger:
    """获取子日志器；不传名称则返回根 tradesome 日志器。"""
    if not name:
        return logging.getLogger(LOGGER_NAME)
    if name.startswith(LOGGER_NAME + "."):
        return logging.getLogger(name)
    return logging.getLogger("%s.%s" % (LOGGER_NAME, name))


def set_level(level: str) -> None:
    logging.getLogger(LOGGER_NAME).setLevel(_level_of(level))


def log_file_path(log_dir: Union[str, Path]) -> Path:
    return Path(log_dir) / LOG_FILENAME
