"""领域值对象：行情、标的、订单、成交、持仓、账户快照、回测配置。

所有对象不可变（frozen dataclass），可安全跨线程传递。
金额单位：元；数量单位：股；时间为 naive datetime（交易所本地时间）。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Any, Dict, Optional, Tuple

from .errors import ValidationError

# ---------------------------------------------------------------- 常量

TIMEFRAMES: Tuple[str, ...] = ("1d", "1w", "1M", "1m", "5m", "15m", "30m", "60m")

SIDE_BUY = "BUY"
SIDE_SELL = "SELL"
SIDES: Tuple[str, ...] = (SIDE_BUY, SIDE_SELL)

ORDER_MARKET = "MARKET"
ORDER_LIMIT = "LIMIT"
ORDER_TYPES: Tuple[str, ...] = (ORDER_MARKET, ORDER_LIMIT)

ST_PENDING = "PENDING"
ST_SUBMITTED = "SUBMITTED"
ST_PARTIAL = "PARTIAL"
ST_FILLED = "FILLED"
ST_CANCELLED = "CANCELLED"
ST_REJECTED = "REJECTED"
ST_UNKNOWN = "UNKNOWN"
ORDER_STATUSES: Tuple[str, ...] = (
    ST_PENDING, ST_SUBMITTED, ST_PARTIAL, ST_FILLED, ST_CANCELLED, ST_REJECTED, ST_UNKNOWN,
)
TERMINAL_STATUSES = frozenset({ST_FILLED, ST_CANCELLED, ST_REJECTED})

KIND_STOCK = "STOCK"
KIND_ETF = "ETF"
KIND_INDEX = "INDEX"
KINDS: Tuple[str, ...] = (KIND_STOCK, KIND_ETF, KIND_INDEX)


def _require(condition: bool, message: str, hint: str = "") -> None:
    if not condition:
        raise ValidationError(message, hint=hint)


# ---------------------------------------------------------------- 行情


@dataclass(frozen=True)
class Bar:
    """一根 K 线。"""

    symbol: str
    dt: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    amount: float = 0.0
    timeframe: str = "1d"

    def __post_init__(self) -> None:
        _require(bool(self.symbol), "K 线的标的代码不能为空")
        _require(isinstance(self.dt, datetime), "K 线的时间必须是 datetime")
        for name in ("open", "high", "low", "close"):
            value = getattr(self, name)
            _require(isinstance(value, (int, float)) and value > 0,
                     "K 线的%s必须是正数" % name, "请检查数据源的价格列")
        _require(self.high >= max(self.open, self.close, self.low),
                 "K 线最高价小于开盘/收盘/最低价", "数据可能损坏或被复权处理错乱")
        _require(self.low <= min(self.open, self.close),
                 "K 线最低价大于开盘/收盘", "数据可能损坏或被复权处理错乱")
        _require(self.volume >= 0, "K 线成交量不能为负")
        _require(self.amount >= 0, "K 线成交额不能为负")
        _require(self.timeframe in TIMEFRAMES, "不支持的周期：%s" % self.timeframe)

    @property
    def change_pct(self) -> Optional[float]:
        """相对开盘的涨跌幅（无前收时的近似）。"""
        if self.open <= 0:
            return None
        return self.close / self.open - 1.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "dt": self.dt.isoformat(sep=" "),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "amount": self.amount,
            "timeframe": self.timeframe,
        }


@dataclass(frozen=True)
class Quote:
    """实时/最新报价快照（模拟盘使用）。"""

    symbol: str
    dt: datetime
    last: float
    bid: float = 0.0
    ask: float = 0.0
    volume: float = 0.0


# ---------------------------------------------------------------- 标的


@dataclass(frozen=True)
class FeeProfile:
    """费用模型（A 股）。"""

    commission_rate: float = 0.00025
    min_commission: float = 5.0
    stamp_tax_rate: float = 0.0005
    transfer_fee_rate: float = 0.00001
    slippage_bps: float = 2.0

    def __post_init__(self) -> None:
        _require(0 <= self.commission_rate < 0.01, "佣金率不合理")
        _require(self.min_commission >= 0, "最低佣金不能为负")
        _require(0 <= self.stamp_tax_rate < 0.01, "印花税率不合理")
        _require(0 <= self.transfer_fee_rate < 0.01, "过户费率不合理")
        _require(0 <= self.slippage_bps < 500, "滑点设置过大")

    def to_dict(self) -> Dict[str, float]:
        return {
            "commission_rate": self.commission_rate,
            "min_commission": self.min_commission,
            "stamp_tax_rate": self.stamp_tax_rate,
            "transfer_fee_rate": self.transfer_fee_rate,
            "slippage_bps": self.slippage_bps,
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "FeeProfile":
        data = data or {}
        defaults = cls()
        return cls(
            commission_rate=float(data.get("commission_rate", defaults.commission_rate)),
            min_commission=float(data.get("min_commission", defaults.min_commission)),
            stamp_tax_rate=float(data.get("stamp_tax_rate", defaults.stamp_tax_rate)),
            transfer_fee_rate=float(data.get("transfer_fee_rate", defaults.transfer_fee_rate)),
            slippage_bps=float(data.get("slippage_bps", defaults.slippage_bps)),
        )


@dataclass(frozen=True)
class Instrument:
    """标的元数据 + 交易规则。"""

    symbol: str
    name: str
    kind: str = KIND_STOCK
    lot_size: int = 100
    price_limit_pct: float = 0.10
    t_plus: int = 1
    tick_size: float = 0.01
    fee: FeeProfile = field(default_factory=FeeProfile)
    board: str = "MAIN"

    def __post_init__(self) -> None:
        _require(bool(self.symbol), "标的代码不能为空")
        _require(self.kind in KINDS, "未知标的类型：%s" % self.kind)
        _require(self.lot_size > 0, "最小交易单位必须为正")
        _require(0 < self.price_limit_pct <= 1, "涨跌幅限制必须在 (0,1]")
        _require(self.t_plus in (0, 1), "T+1 标记只能是 0 或 1")
        _require(self.tick_size > 0, "最小价位必须为正")

    @property
    def is_index(self) -> bool:
        return self.kind == KIND_INDEX

    @property
    def is_etf(self) -> bool:
        return self.kind == KIND_ETF

    @property
    def display(self) -> str:
        return "%s %s" % (self.symbol, self.name)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "kind": self.kind,
            "lot_size": self.lot_size,
            "price_limit_pct": self.price_limit_pct,
            "t_plus": self.t_plus,
            "tick_size": self.tick_size,
            "board": self.board,
            "fee": self.fee.to_dict(),
        }


# ---------------------------------------------------------------- 订单


@dataclass(frozen=True)
class OrderRequest:
    """策略发出的下单意图（尚未经过风控与校验）。"""

    symbol: str
    side: str
    qty: int
    order_type: str = ORDER_MARKET
    limit_price: float = 0.0
    reason: str = ""
    client_id: str = ""

    def __post_init__(self) -> None:
        _require(self.side in SIDES, "买卖方向只能是 BUY 或 SELL")
        _require(self.order_type in ORDER_TYPES, "不支持的订单类型")
        _require(self.qty > 0, "下单数量必须为正")
        if self.order_type == ORDER_LIMIT:
            _require(self.limit_price > 0, "限价单必须给出限价")


@dataclass(frozen=True)
class Order:
    """订单（对外快照，状态变化时生成新对象）。"""

    order_id: str
    client_id: str
    symbol: str
    side: str
    qty: int
    order_type: str = ORDER_MARKET
    limit_price: float = 0.0
    status: str = ST_PENDING
    filled_qty: int = 0
    avg_fill_price: float = 0.0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    strategy_id: str = ""
    session_id: str = ""
    reason: str = ""
    gateway: str = ""
    broker_order_id: str = ""

    def __post_init__(self) -> None:
        _require(bool(self.order_id), "订单号不能为空")
        _require(self.side in SIDES, "买卖方向非法")
        _require(self.qty > 0, "订单数量必须为正")
        _require(self.status in ORDER_STATUSES, "订单状态非法")
        _require(0 <= self.filled_qty <= self.qty, "已成交数量超出订单数量")

    @property
    def is_open(self) -> bool:
        return self.status in (ST_PENDING, ST_SUBMITTED, ST_PARTIAL)

    @property
    def remaining(self) -> int:
        return max(0, self.qty - self.filled_qty)

    @property
    def notional(self) -> float:
        price = self.avg_fill_price or self.limit_price
        return price * self.qty

    def evolve(self, **changes: Any) -> "Order":
        return replace(self, **changes)


@dataclass(frozen=True)
class Fill:
    """一笔成交。"""

    fill_id: str
    order_id: str
    symbol: str
    side: str
    qty: int
    price: float
    dt: datetime
    commission: float = 0.0
    stamp_tax: float = 0.0
    transfer_fee: float = 0.0
    slippage_cost: float = 0.0
    trade_id: str = ""

    def __post_init__(self) -> None:
        _require(self.qty > 0, "成交数量必须为正")
        _require(self.price > 0, "成交价格必须为正")

    @property
    def fees(self) -> float:
        return self.commission + self.stamp_tax + self.transfer_fee

    @property
    def gross_amount(self) -> float:
        return self.price * self.qty

    @property
    def net_amount(self) -> float:
        """对现金的影响：买入为负，卖出为正。"""
        if self.side == SIDE_BUY:
            return -(self.gross_amount + self.fees)
        return self.gross_amount - self.fees


# ---------------------------------------------------------------- 持仓与账户


@dataclass(frozen=True)
class Position:
    symbol: str
    qty: int
    available_qty: int
    avg_cost: float
    last_price: float
    opened_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        _require(self.qty >= 0, "持仓数量不能为负")
        _require(0 <= self.available_qty <= self.qty, "可卖数量必须在 [0, 持仓] 之间")
        _require(self.avg_cost >= 0, "持仓成本不能为负")

    @property
    def market_value(self) -> float:
        return self.qty * self.last_price

    @property
    def cost_value(self) -> float:
        return self.qty * self.avg_cost

    @property
    def unrealized_pnl(self) -> float:
        return self.market_value - self.cost_value

    @property
    def unrealized_pct(self) -> float:
        if self.avg_cost <= 0:
            return 0.0
        return self.last_price / self.avg_cost - 1.0

    def evolve(self, **changes: Any) -> "Position":
        return replace(self, **changes)


@dataclass(frozen=True)
class AccountSnapshot:
    """账户某一时刻的完整状态。"""

    dt: datetime
    cash: float
    frozen_cash: float
    market_value: float
    equity: float
    realized_pnl: float
    unrealized_pnl: float
    day_pnl: float
    peak_equity: float
    drawdown: float
    positions: Tuple[Position, ...] = ()

    @property
    def total_cash(self) -> float:
        return self.cash + self.frozen_cash

    @property
    def position_count(self) -> int:
        return len([p for p in self.positions if p.qty > 0])

    @property
    def exposure(self) -> float:
        return (self.market_value / self.equity) if self.equity > 0 else 0.0

    def position(self, symbol: str) -> Optional[Position]:
        for item in self.positions:
            if item.symbol == symbol:
                return item
        return None


# ---------------------------------------------------------------- 信号与交易记录


@dataclass(frozen=True)
class Signal:
    symbol: str
    dt: datetime
    action: str = "HOLD"
    strength: float = 1.0
    reason: str = ""

    def __post_init__(self) -> None:
        _require(self.action in ("BUY", "SELL", "HOLD"), "信号动作非法")


@dataclass(frozen=True)
class TradeRecord:
    """一笔完整往返交易（开仓 → 平仓）。"""

    symbol: str
    open_dt: Optional[datetime]
    close_dt: Optional[datetime]
    qty: int
    entry_price: float
    exit_price: float
    pnl: float
    pnl_pct: float
    fees: float
    holding_days: int
    exit_reason: str = ""

    @property
    def is_win(self) -> bool:
        return self.pnl > 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "open_dt": self.open_dt.isoformat(sep=" ") if self.open_dt else "",
            "close_dt": self.close_dt.isoformat(sep=" ") if self.close_dt else "",
            "qty": self.qty,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "pnl": self.pnl,
            "pnl_pct": self.pnl_pct,
            "fees": self.fees,
            "holding_days": self.holding_days,
            "exit_reason": self.exit_reason,
        }


# ---------------------------------------------------------------- 回测


@dataclass(frozen=True)
class BacktestConfig:
    symbols: Tuple[str, ...]
    start: date
    end: date
    initial_cash: float = 100000.0
    timeframe: str = "1d"
    strategy_key: str = ""
    strategy_params: Dict[str, Any] = field(default_factory=dict)
    fee: FeeProfile = field(default_factory=FeeProfile)
    max_volume_ratio: float = 0.05
    benchmark: str = ""
    seed: int = 42
    risk_enabled: bool = True

    def __post_init__(self) -> None:
        _require(bool(self.symbols), "请至少选择一个标的")
        _require(self.start <= self.end, "开始日期不能晚于结束日期")
        _require(self.initial_cash >= 1000, "初始资金至少 1000 元")
        _require(self.timeframe in TIMEFRAMES, "不支持的周期")
        _require(0 < self.max_volume_ratio <= 1, "成交量占比必须在 (0,1]")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbols": list(self.symbols),
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "initial_cash": self.initial_cash,
            "timeframe": self.timeframe,
            "strategy_key": self.strategy_key,
            "strategy_params": dict(self.strategy_params),
            "fee": self.fee.to_dict(),
            "max_volume_ratio": self.max_volume_ratio,
            "benchmark": self.benchmark,
            "seed": self.seed,
            "risk_enabled": self.risk_enabled,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BacktestConfig":
        return cls(
            symbols=tuple(data.get("symbols") or ()),
            start=date.fromisoformat(data["start"]),
            end=date.fromisoformat(data["end"]),
            initial_cash=float(data.get("initial_cash", 100000.0)),
            timeframe=data.get("timeframe", "1d"),
            strategy_key=data.get("strategy_key", ""),
            strategy_params=dict(data.get("strategy_params") or {}),
            fee=FeeProfile.from_dict(data.get("fee")),
            max_volume_ratio=float(data.get("max_volume_ratio", 0.05)),
            benchmark=data.get("benchmark", ""),
            seed=int(data.get("seed", 42)),
            risk_enabled=bool(data.get("risk_enabled", True)),
        )

    def evolve(self, **changes: Any) -> "BacktestConfig":
        return replace(self, **changes)


@dataclass
class BacktestResult:
    """回测输出（可变容器，构建期追加、结束后只读使用）。"""

    run_id: str
    config: BacktestConfig
    metrics: Dict[str, Any] = field(default_factory=dict)
    equity_curve: Tuple[Tuple[datetime, float], ...] = ()
    drawdown_curve: Tuple[Tuple[datetime, float], ...] = ()
    daily_returns: Tuple[Tuple[datetime, float], ...] = ()
    monthly_returns: Dict[str, float] = field(default_factory=dict)
    trades: Tuple[TradeRecord, ...] = ()
    orders: Tuple[Order, ...] = ()
    logs: Tuple[str, ...] = ()
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    duration_sec: float = 0.0
    cancelled: bool = False
    benchmark_curve: Tuple[Tuple[datetime, float], ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "config": self.config.to_dict(),
            "metrics": dict(self.metrics),
            "equity_curve": [[dt.isoformat(sep=" "), v] for dt, v in self.equity_curve],
            "drawdown_curve": [[dt.isoformat(sep=" "), v] for dt, v in self.drawdown_curve],
            "daily_returns": [[dt.isoformat(sep=" "), v] for dt, v in self.daily_returns],
            "monthly_returns": dict(self.monthly_returns),
            "trades": [t.to_dict() for t in self.trades],
            "orders": [
                {
                    "order_id": o.order_id, "symbol": o.symbol, "side": o.side,
                    "qty": o.qty, "status": o.status, "filled_qty": o.filled_qty,
                    "avg_fill_price": o.avg_fill_price, "reason": o.reason,
                }
                for o in self.orders
            ],
            "logs": list(self.logs),
            "started_at": self.started_at.isoformat(sep=" ") if self.started_at else "",
            "finished_at": self.finished_at.isoformat(sep=" ") if self.finished_at else "",
            "duration_sec": self.duration_sec,
            "cancelled": self.cancelled,
            "benchmark_curve": [[dt.isoformat(sep=" "), v] for dt, v in self.benchmark_curve],
        }
