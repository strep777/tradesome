"""执行层：券商协议、模拟券商、订单路由与历史回放引擎。

为避免与 ``backtest.engine`` 形成循环导入，这里用惰性导入暴露公开类；
``from tradesome.execution import OrderRouter`` 仍然可用，但不会在包初始化时
把整条回测链路拉进来。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - 仅供类型检查
    from .live_engine import LiveEngine
    from .paper_broker import PaperBroker
    from .ports import Broker
    from .router import OrderRouter

_EXPORTS = {
    "Broker": (".ports", "Broker"),
    "PaperBroker": (".paper_broker", "PaperBroker"),
    "OrderRouter": (".router", "OrderRouter"),
    "LiveEngine": (".live_engine", "LiveEngine"),
}

__all__ = ["Broker", "LiveEngine", "OrderRouter", "PaperBroker"]


def __getattr__(name: str):
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    from importlib import import_module

    module = import_module(target[0], __name__)
    value = getattr(module, target[1])
    globals()[name] = value
    return value
