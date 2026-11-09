"""日志辅助：测试之间关闭文件处理器，避免 Windows 临时目录被占用。"""

from __future__ import annotations

import logging


def close_log_handlers() -> None:
    """关闭并移除根日志的所有处理器（含 setup_logging 写的文件）。"""
    root = logging.getLogger()
    for handler in list(root.handlers):
        try:
            handler.flush()
        except Exception:  # noqa: BLE001
            pass
        try:
            handler.close()
        except Exception:  # noqa: BLE001
            pass
        root.removeHandler(handler)
    for name in ("tradesome", "backtest", "execution", "risk", "data", "app"):
        logger = logging.getLogger(name)
        for handler in list(logger.handlers):
            try:
                handler.close()
            except Exception:  # noqa: BLE001
                pass
            logger.removeHandler(handler)
