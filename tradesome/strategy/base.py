"""策略基类与参数声明。

约定：
- 参数用 ParamSpec 声明，界面据此自动生成表单，策略作者不用写任何 UI 代码；
- on_bars 每个时间点调用一次（多标的），on_bar 是单标的便捷钩子；
- 策略只能通过 ctx 下单与取数，拿不到账户/数据库对象，避免越权。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from ..core.errors import ValidationError
from ..core.types import Bar, Order, OrderRequest, Position

PARAM_TYPES = ("int", "float", "bool", "str", "choice")
RISK_LEVELS = ("低", "中", "高")
CATEGORIES = ("趋势", "均值回归", "网格", "轮动", "情绪", "自定义")


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "y", "on", "是", "开"):
        return True
    if text in ("0", "false", "no", "n", "off", "否", "关"):
        return False
    raise ValueError("不是布尔值")


@dataclass(frozen=True)
class ParamSpec:
    """一个可调参数。type 只影响控件与校验，不影响策略逻辑。"""

    name: str
    label: str
    type: str = "int"
    default: Any = 0
    min: Optional[float] = None
    max: Optional[float] = None
    step: Optional[float] = None
    choices: Tuple[Any, ...] = ()
    help: str = ""
    unit: str = ""

    def __post_init__(self) -> None:
        if not self.name or not self.name.isidentifier():
            raise ValidationError("参数名不合法：%r" % (self.name,))
        if self.type not in PARAM_TYPES:
            raise ValidationError("参数 %s 的类型 %r 不支持" % (self.name, self.type))
        if self.type == "choice" and not self.choices:
            raise ValidationError("参数 %s 是选择型但未给出选项" % self.name)

    # ------------------------------------------------------------------

    def coerce(self, value: Any) -> Any:
        """把界面/文件里的任意值转成合法值；非法时抛出带字段名的错误。"""
        try:
            if self.type == "bool":
                result: Any = _as_bool(value)
            elif self.type == "int":
                result = int(round(float(value)))
            elif self.type == "float":
                result = float(value)
            elif self.type == "choice":
                result = value
                if result not in self.choices:
                    raise ValueError("可选值为 %s" % (list(self.choices),))
            else:
                result = str(value)
        except (TypeError, ValueError) as exc:
            raise ValidationError("参数「%s」取值不合法：%s（%s）" % (self.label, value, exc))
        if self.type in ("int", "float"):
            if self.min is not None and result < self.min:
                raise ValidationError("参数「%s」不能小于 %s" % (self.label, _fmt_num(self.min)))
            if self.max is not None and result > self.max:
                raise ValidationError("参数「%s」不能大于 %s" % (self.label, _fmt_num(self.max)))
        return result

    def default_value(self) -> Any:
        try:
            return self.coerce(self.default)
        except ValidationError:
            return self.default

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name, "label": self.label, "type": self.type,
            "default": self.default, "min": self.min, "max": self.max,
            "step": self.step, "choices": list(self.choices),
            "help": self.help, "unit": self.unit,
        }


def _fmt_num(value: float) -> str:
    return ("%d" % value) if float(value).is_integer() else ("%g" % value)


def coerce_params(specs: Sequence[ParamSpec],
                  values: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """按声明顺序产出完整参数字典；未提供的用默认值。"""
    values = dict(values or {})
    result: Dict[str, Any] = {}
    for spec in specs:
        if spec.name in values and values[spec.name] is not None:
            result[spec.name] = spec.coerce(values[spec.name])
        else:
            result[spec.name] = spec.default_value()
    return result


def default_params(specs: Sequence[ParamSpec]) -> Dict[str, Any]:
    return {spec.name: spec.default_value() for spec in specs}


@dataclass(frozen=True)
class StrategyMeta:
    key: str
    name: str
    category: str = "趋势"
    risk_level: str = "中"
    description: str = ""
    principle: str = ""
    works_when: str = ""
    fails_when: str = ""
    tags: Tuple[str, ...] = ()
    version: str = "1.0.0"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key, "name": self.name, "category": self.category,
            "risk_level": self.risk_level, "description": self.description,
            "principle": self.principle, "works_when": self.works_when,
            "fails_when": self.fails_when, "tags": list(self.tags),
            "version": self.version,
        }


class Strategy:
    """所有策略的基类。子类只需声明 meta / param_specs 并实现钩子。"""

    meta: StrategyMeta = StrategyMeta(key="base", name="基类")

    def __init__(self) -> None:
        self.p: Dict[str, Any] = default_params(self.param_specs())
        self.ctx = None                     # type: Any
        self.state: Dict[str, Any] = {}
        self.logs: List[str] = []

    # ------------------------------------------------------------ 声明

    def param_specs(self) -> List[ParamSpec]:
        return []

    def configure(self, values: Optional[Mapping[str, Any]] = None) -> None:
        self.p = coerce_params(self.param_specs(), values)
        self.on_configure()

    def on_configure(self) -> None:
        """参数确定后调用，可在这里做派生计算。"""

    # ------------------------------------------------------------ 生命周期

    def bind(self, ctx: Any) -> None:
        self.ctx = ctx

    def on_start(self) -> None:
        """回测/模拟盘开始，指标可预热。"""

    def on_bars(self, bars: Mapping[str, Bar], ts) -> None:
        for bar in bars.values():
            self.on_bar(bar)

    def on_bar(self, bar: Bar) -> None:
        """单标的钩子；默认实现什么都不做。"""

    def on_order(self, order: Order) -> None:
        """订单状态变化（成交/拒绝）时回调，用于记录。"""

    def on_stop(self) -> None:
        """结束回调；不要在这里下单。"""

    def reset(self) -> None:
        self.state.clear()
        self.logs.clear()

    # ------------------------------------------------------------ 便捷取数

    def buy(self, symbol: str, weight: Optional[float] = None,
            qty: Optional[int] = None, limit_price: Optional[float] = None,
            reason: str = "") -> Optional[Order]:
        return self.ctx.buy(symbol, weight=weight, qty=qty,
                            limit_price=limit_price, reason=reason)

    def sell(self, symbol: str, weight: Optional[float] = None,
             qty: Optional[int] = None, limit_price: Optional[float] = None,
             reason: str = "") -> Optional[Order]:
        return self.ctx.sell(symbol, weight=weight, qty=qty,
                             limit_price=limit_price, reason=reason)

    def close(self, symbol: str, reason: str = "策略平仓") -> Optional[Order]:
        return self.ctx.close(symbol, reason=reason)

    def close_all(self, reason: str = "策略清仓") -> List[Order]:
        return self.ctx.close_all(reason=reason)

    def position_of(self, symbol: str):
        return self.ctx.position_of(symbol)

    def price(self, symbol: str) -> Optional[float]:
        return self.ctx.price(symbol)

    def history(self, symbol: str, n: Optional[int] = None,
                field: str = "close") -> List[float]:
        return self.ctx.history(symbol, n=n, field=field)

    def log(self, message: str, level: str = "INFO") -> None:
        self.logs.append(message)
        if self.ctx is not None:
            self.ctx.log(message, level=level)

    @property
    def indicator(self):
        return self.ctx.indicator

    @property
    def cash(self) -> float:
        return self.ctx.cash

    @property
    def equity(self) -> float:
        return self.ctx.equity


def last(values: Sequence[float]) -> Optional[float]:
    """取最后一个有效值；预热期为 nan 时返回 None。"""
    if not values:
        return None
    value = values[-1]
    if value is None:
        return None
    try:
        if value != value:  # nan
            return None
    except TypeError:
        return None
    return value


def crossed_up(fast: Sequence[float], slow: Sequence[float]) -> bool:
    """fast 上穿 slow（用最后两根判断）。"""
    if len(fast) < 2 or len(slow) < 2:
        return False
    a0, a1 = fast[-2], fast[-1]
    b0, b1 = slow[-2], slow[-1]
    if any(v is None or v != v for v in (a0, a1, b0, b1)):
        return False
    return a0 <= b0 and a1 > b1


def crossed_down(fast: Sequence[float], slow: Sequence[float]) -> bool:
    if len(fast) < 2 or len(slow) < 2:
        return False
    a0, a1 = fast[-2], fast[-1]
    b0, b1 = slow[-2], slow[-1]
    if any(v is None or v != v for v in (a0, a1, b0, b1)):
        return False
    return a0 >= b0 and a1 < b1
