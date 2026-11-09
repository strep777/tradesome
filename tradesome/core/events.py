"""事件主题与事件对象。

主题命名：<域>.<动作>，全小写，点分隔。新增主题必须在此登记，
避免字符串散落导致订阅失效（拼写错误不会报错，只会静默无事件）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict

# 应用
APP_READY = "app.ready"
APP_ERROR = "app.error"
APP_CLOSING = "app.closing"

# 数据
DATA_GENERATED = "data.generated"
DATA_IMPORTED = "data.imported"
DATA_DELETED = "data.deleted"

# 回测
BACKTEST_STARTED = "backtest.started"
BACKTEST_PROGRESS = "backtest.progress"
BACKTEST_FINISHED = "backtest.finished"
BACKTEST_FAILED = "backtest.failed"

# 交易
ORDER_SUBMITTED = "order.submitted"
ORDER_FILLED = "order.filled"
ORDER_REJECTED = "order.rejected"
ORDER_CANCELLED = "order.cancelled"

# 实盘
GATEWAY_CONNECTING = "gateway.connecting"
GATEWAY_CONNECTED = "gateway.connected"
GATEWAY_DISCONNECTED = "gateway.disconnected"
GATEWAY_ERROR = "gateway.error"
ACCOUNT_UPDATED = "account.updated"
POSITION_UPDATED = "position.updated"
ORDER_UPDATED = "order.updated"
LIVE_TRADE_STARTED = "live_trade.started"
LIVE_TRADE_STOPPED = "live_trade.stopped"
AUTO_TRADE_TRIGGERED = "live_trade.auto"

# 风控
RISK_TRIGGERED = "risk.triggered"
RISK_STATE_CHANGED = "risk.state_changed"

# 模拟盘
LIVE_STARTED = "live.started"
LIVE_TICK = "live.tick"
LIVE_STOPPED = "live.stopped"

# 审计与配置
AUDIT_WRITTEN = "audit.written"
CONFIG_CHANGED = "config.changed"

ALL_TOPICS = (
    APP_READY, APP_ERROR, APP_CLOSING,
    DATA_GENERATED, DATA_IMPORTED, DATA_DELETED,
    BACKTEST_STARTED, BACKTEST_PROGRESS, BACKTEST_FINISHED, BACKTEST_FAILED,
    ORDER_SUBMITTED, ORDER_FILLED, ORDER_REJECTED, ORDER_CANCELLED,
    GATEWAY_CONNECTING, GATEWAY_CONNECTED, GATEWAY_DISCONNECTED, GATEWAY_ERROR,
    ACCOUNT_UPDATED, POSITION_UPDATED, ORDER_UPDATED,
    LIVE_TRADE_STARTED, LIVE_TRADE_STOPPED, AUTO_TRADE_TRIGGERED,
    RISK_TRIGGERED, RISK_STATE_CHANGED,
    LIVE_STARTED, LIVE_TICK, LIVE_STOPPED,
    AUDIT_WRITTEN, CONFIG_CHANGED,
)


@dataclass(frozen=True)
class Event:
    """一次事件发布。payload 只放不可变数据或拷贝，禁止传可变共享对象。"""

    topic: str
    payload: Dict[str, Any] = field(default_factory=dict)
    ts: datetime = field(default_factory=datetime.now)
    seq: int = 0

    def get(self, key: str, default: Any = None) -> Any:
        return self.payload.get(key, default)
