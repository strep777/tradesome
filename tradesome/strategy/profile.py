"""策略工坊：用参数与风控叠加层保存策略，不需要写任何文件。

参考 vnpy CtaTemplate 的设计：策略 = 模板（逻辑）+ 参数（settings）+
生命周期（on_init/on_start/on_bar/on_stop）。这里把「模板」限定为内置
策略，「settings」由界面表单生成，并额外提供三个通用风控叠加层：

* 止损：收盘价相对持仓成本下跌超过阈值时清仓；
* 止盈：上涨超过阈值时清仓；
* 趋势过滤：收盘价在长期均线下方时不允许开新仓。

策略档案保存为 SQLite 中的 JSON，运行时动态生成 Strategy 子类，
因此用户既不用创建文件，也不会有任意代码执行。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple, Type

from ..core.errors import ValidationError
from .base import ParamSpec, Strategy, StrategyMeta, coerce_params
from .rules import (DEFAULT_WEIGHT, WEIGHT_SPEC, default_rules, summarize_rules,
                    validate_rules)

STOP_LOSS_SPEC = ParamSpec(
    "risk_stop_pct", "风控止损（%）", "float", 5.0, 0.0, 50.0, 0.5,
    help="收盘价相对成本下跌超过该比例时清仓；0 表示关闭")
TAKE_PROFIT_SPEC = ParamSpec(
    "risk_take_pct", "风控止盈（%）", "float", 20.0, 0.0, 200.0, 0.5,
    help="收盘价相对成本上涨超过该比例时清仓；0 表示关闭")
TREND_MA_SPEC = ParamSpec(
    "risk_trend_ma", "风控趋势均线", "int", 60, 0, 250, 5,
    help="在模板自身逻辑之外再限制：收盘价低于该均线时不许开新仓；0 关闭", unit="日")

OVERLAY_SPECS: Tuple[ParamSpec, ...] = (STOP_LOSS_SPEC, TAKE_PROFIT_SPEC, TREND_MA_SPEC)
OVERLAY_NAMES = frozenset(spec.name for spec in OVERLAY_SPECS)
MAX_NAME = 24


def _text(value: Any) -> str:
    return str(value or "").strip()


def _coerce_overlays(values: Mapping[str, Any]) -> Dict[str, Any]:
    return coerce_params(OVERLAY_SPECS, values)


@dataclass
class ProfileSpec:
    """一条可保存、可回测、可上模拟盘/实盘的策略档案。"""

    profile_id: str
    name: str
    template: str
    params: Dict[str, Any] = field(default_factory=dict)
    stop_loss_pct: float = 5.0
    take_profit_pct: float = 20.0
    trend_ma: int = 60
    kind: str = "template"
    rules: Dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""

    # ---------------------------------------------------------- 标识

    @property
    def key(self) -> str:
        return "custom_%s" % self.profile_id

    def to_dict(self) -> Dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "key": self.key,
            "name": self.name,
            "template": self.template,
            "params": dict(self.params),
            "stop_loss_pct": float(self.stop_loss_pct),
            "take_profit_pct": float(self.take_profit_pct),
            "trend_ma": int(self.trend_ma),
            "kind": self.kind,
            "rules": dict(self.rules),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def overlay_values(self) -> Dict[str, Any]:
        return {
            "risk_stop_pct": float(self.stop_loss_pct),
            "risk_take_pct": float(self.take_profit_pct),
            "risk_trend_ma": int(self.trend_ma),
        }

    def summary(self) -> str:
        if self.kind == "rules":
            return "自由组合：" + summarize_rules(self.rules)
        parts = ["基于「%s」" % self.template]
        if self.params:
            parts.append("参数 " + "、".join("%s=%s" % (k, v) for k, v in self.params.items()))
        if self.stop_loss_pct > 0:
            parts.append("止损 %.1f%%" % self.stop_loss_pct)
        if self.take_profit_pct > 0:
            parts.append("止盈 %.1f%%" % self.take_profit_pct)
        if self.trend_ma > 0:
            parts.append("%d 日均线过滤" % self.trend_ma)
        return "；".join(parts)


def new_profile_id() -> str:
    return uuid.uuid4().hex[:10]


def validate_profile(payload: Mapping[str, Any], registry,
                     profile_id: Optional[str] = None) -> ProfileSpec:
    """把界面提交的字典校验成 ProfileSpec；错误信息面向最终用户。"""
    name = _text(payload.get("name"))
    if not 2 <= len(name) <= MAX_NAME:
        raise ValidationError("策略名称需为 2–%d 个字符" % MAX_NAME)
    if any(ord(ch) < 32 for ch in name):
        raise ValidationError("策略名称不能包含控制字符")

    kind = _text(payload.get("kind")) or "template"
    if kind not in ("template", "rules"):
        raise ValidationError("策略类型不正确：%s" % kind)

    now = time.strftime("%Y-%m-%d %H:%M:%S")
    if kind == "rules":
        rules = validate_rules(payload.get("rules") or {})
        return ProfileSpec(
            profile_id=profile_id or new_profile_id(),
            name=name,
            template="rules",
            params={},
            stop_loss_pct=0.0,
            take_profit_pct=0.0,
            trend_ma=0,
            kind="rules",
            rules=rules,
            created_at=now,
            updated_at=now,
        )

    template = _text(payload.get("template"))
    if not template:
        raise ValidationError("请选择策略模板")
    if template.startswith("custom_") or template not in registry:
        raise ValidationError("策略模板不存在：%s" % template)
    if registry.is_profile(template):
        raise ValidationError("不能基于自定义策略再套一层模板")

    base_cls = registry._class(template)  # noqa: SLF001 - 同包内复用注册表校验
    base_specs = list(base_cls.__new__(base_cls).param_specs())
    raw_params = payload.get("params") or {}
    if not isinstance(raw_params, Mapping):
        raise ValidationError("参数格式不正确")
    params = coerce_params(base_specs, raw_params)

    overlays = _coerce_overlays({
        "risk_stop_pct": payload.get("stop_loss_pct", STOP_LOSS_SPEC.default),
        "risk_take_pct": payload.get("take_profit_pct", TAKE_PROFIT_SPEC.default),
        "risk_trend_ma": payload.get("trend_ma", TREND_MA_SPEC.default),
    })
    if overlays["risk_stop_pct"] >= 50:
        raise ValidationError("止损阈值需小于 50%")
    if overlays["risk_take_pct"] > 200:
        raise ValidationError("止盈阈值不能超过 200%")
    if overlays["risk_trend_ma"] and overlays["risk_trend_ma"] < 5:
        raise ValidationError("趋势过滤均线至少 5 日，或填 0 关闭")

    return ProfileSpec(
        profile_id=profile_id or new_profile_id(),
        name=name,
        template=template,
        params=params,
        stop_loss_pct=float(overlays["risk_stop_pct"]),
        take_profit_pct=float(overlays["risk_take_pct"]),
        trend_ma=int(overlays["risk_trend_ma"]),
        kind="template",
        created_at=now,
        updated_at=now,
    )


def profile_from_dict(data: Mapping[str, Any]) -> ProfileSpec:
    """从数据库 JSON 恢复；损坏数据给出可读错误。"""
    try:
        profile_id = _text(data.get("profile_id")) or new_profile_id()
        kind = _text(data.get("kind")) or "template"
        if kind not in ("template", "rules"):
            raise ValidationError("未知的策略类型：%s" % kind)
        return ProfileSpec(
            profile_id=profile_id,
            name=_text(data.get("name")),
            template=_text(data.get("template")),
            params=dict(data.get("params") or {}),
            stop_loss_pct=float(data.get("stop_loss_pct", 0) or 0),
            take_profit_pct=float(data.get("take_profit_pct", 0) or 0),
            trend_ma=int(data.get("trend_ma", 0) or 0),
            kind=kind,
            rules=dict(data.get("rules") or {}),
            created_at=_text(data.get("created_at")),
            updated_at=_text(data.get("updated_at")),
        )
    except (TypeError, ValueError) as exc:
        raise ValidationError("策略档案损坏：%s" % exc)


class ProfileStrategy(Strategy):
    """由档案驱动的策略：委托模板，再叠加止损/止盈/趋势过滤。"""

    profile_spec: ProfileSpec
    base_cls: Type[Strategy]
    meta: StrategyMeta

    def __init__(self) -> None:
        spec = self.profile_spec
        self._base = self.base_cls()
        self._base.configure(spec.params)
        self.p = dict(spec.params)
        self.p.update(spec.overlay_values())
        self.ctx = None
        self.state: Dict[str, Any] = {}
        self.logs: List[str] = []
        self._blocked: Optional[bool] = None

    # ---------------------------------------------------------- 声明

    def param_specs(self) -> List[ParamSpec]:
        return list(self._base.param_specs()) + list(OVERLAY_SPECS)

    def configure(self, values: Optional[Mapping[str, Any]] = None) -> None:
        values = dict(values or {})
        base_values = {k: v for k, v in values.items() if k not in OVERLAY_NAMES}
        overlay_values = {k: v for k, v in values.items() if k in OVERLAY_NAMES}
        merged_base = dict(self._base.p)
        merged_base.update(base_values)
        self._base.configure(merged_base)
        self.p = dict(self._base.p)
        merged = self.profile_spec.overlay_values()
        merged.update(overlay_values)
        self.p.update(_coerce_overlays(merged))
        if "risk_stop_pct" in overlay_values:
            self.profile_spec.stop_loss_pct = float(self.p["risk_stop_pct"])
        if "risk_take_pct" in overlay_values:
            self.profile_spec.take_profit_pct = float(self.p["risk_take_pct"])
        if "risk_trend_ma" in overlay_values:
            self.profile_spec.trend_ma = int(self.p["risk_trend_ma"])

    # ---------------------------------------------------------- 生命周期

    def bind(self, ctx: Any) -> None:
        super().bind(ctx)
        self._base.bind(ctx)

    def on_start(self) -> None:
        if self.ctx is not None:
            self.ctx.buy_filter = self._buy_allowed
        self._base.on_start()

    def on_bar(self, bar) -> None:
        self._blocked = self._trend_blocked(bar)
        self._base.on_bar(bar)
        self._check_exit(bar)

    def on_stop(self) -> None:
        self._base.on_stop()

    def reset(self) -> None:
        super().reset()
        self._base.reset()
        self._blocked = None

    # ---------------------------------------------------------- 叠加层

    def _buy_allowed(self, symbol: str) -> bool:
        """ctx 在下买单前调用；只拦新仓，不拦止损/平仓。"""
        return not self._blocked

    def _trend_blocked(self, bar) -> bool:
        period = int(self.p.get("risk_trend_ma") or 0)
        if period <= 0:
            return False
        closes = self.history(bar.symbol, period + 1)
        if len(closes) < period:
            return False
        average = _sma(closes, period)
        if average is None:
            return False
        return bar.close < average

    def _check_exit(self, bar) -> None:
        position = self.position_of(bar.symbol)
        if position is None or position.qty <= 0:
            return
        cost = float(position.avg_cost or 0)
        if cost <= 0:
            return
        change = (bar.close - cost) / cost
        stop = float(self.p.get("risk_stop_pct") or 0) / 100.0
        take = float(self.p.get("risk_take_pct") or 0) / 100.0
        if stop > 0 and change <= -stop:
            self.close(bar.symbol, reason="止损 %.1f%%" % (stop * 100))
        elif take > 0 and change >= take:
            self.close(bar.symbol, reason="止盈 %.1f%%" % (take * 100))


class RuleStrategy(Strategy):
    """自由组合策略：按条件表入场，按退出规则离场。"""

    profile_spec: ProfileSpec
    meta: StrategyMeta

    def __init__(self) -> None:
        rules = self.profile_spec.rules or default_rules()
        self.p: Dict[str, Any] = {
            "weight": float(rules.get("weight") or DEFAULT_WEIGHT)}
        self.ctx = None
        self.state: Dict[str, Any] = {}
        self.logs: List[str] = []
        self._seq = 0
        self._last_entry: Dict[str, int] = {}
        self._held: Dict[str, int] = {}

    # ---------------------------------------------------------- 声明

    def param_specs(self) -> List[ParamSpec]:
        return [WEIGHT_SPEC]

    def configure(self, values: Optional[Mapping[str, Any]] = None) -> None:
        merged = dict(self.p)
        merged.update(dict(values or {}))
        self.p = coerce_params([WEIGHT_SPEC], merged)

    def reset(self) -> None:
        super().reset()
        self._seq = 0
        self._last_entry.clear()
        self._held.clear()

    # ---------------------------------------------------------- 生命周期

    def on_bar(self, bar) -> None:
        symbol = bar.symbol
        self._seq += 1
        position = self.position_of(symbol)
        if position is not None and position.qty > 0:
            self._held[symbol] = self._held.get(symbol, 0) + 1
            self._exit_if_needed(bar, position)
            return

        self._held.pop(symbol, None)
        if self._seq - self._last_entry.get(symbol, -99) <= 1:
            return
        if not self._entry_signal(bar):
            return
        order = self.buy(symbol, weight=self.p["weight"], reason="组合条件")
        if order is not None:
            self._last_entry[symbol] = self._seq

    # ---------------------------------------------------------- 入场

    def _entry_signal(self, bar) -> bool:
        rules = self.profile_spec.rules or {}
        entries = list(rules.get("entry") or [])
        if not entries:
            return False
        results = [self._match(bar, rule) for rule in entries]
        if str(rules.get("match") or "all") == "any":
            return any(results)
        return all(results)

    def _match(self, bar, rule: Mapping[str, Any]) -> bool:
        kind = str(rule.get("type") or "")
        if kind == "ma_cross":
            return self._ma_cross(bar, int(rule["fast"]), int(rule["slow"]))
        if kind == "breakout":
            return self._breakout(bar, int(rule["period"]))
        if kind == "rsi_low":
            return self._rsi_low(bar, int(rule["period"]), float(rule["level"]))
        if kind == "boll_low":
            return self._boll_low(bar, int(rule["period"]), float(rule["k"]))
        if kind == "momentum":
            return self._momentum(bar, int(rule["period"]), float(rule["pct"]))
        if kind == "volume_surge":
            return self._volume_surge(bar, int(rule["period"]), float(rule["ratio"]))
        return False

    def _ma_cross(self, bar, fast: int, slow: int) -> bool:
        closes = self.history(bar.symbol, slow + 2)
        if len(closes) < slow + 2:
            return False
        fast_now = _sma(closes[-fast:], fast)
        slow_now = _sma(closes[-slow:], slow)
        fast_prev = _sma(closes[-fast - 1:-1], fast)
        slow_prev = _sma(closes[-slow - 1:-1], slow)
        if None in (fast_now, slow_now, fast_prev, slow_prev):
            return False
        return fast_prev <= slow_prev and fast_now > slow_now

    def _breakout(self, bar, period: int) -> bool:
        highs = self.history(bar.symbol, period + 1, field="high")
        if len(highs) < period + 1:
            return False
        previous = max(highs[-period - 1:-1])
        return bar.close > previous

    def _rsi_low(self, bar, period: int, level: float) -> bool:
        closes = self.history(bar.symbol, period + 3)
        series = _rsi_series(closes, period)
        if len(series) < 2:
            return False
        previous, current = series[-2], series[-1]
        if previous is None or current is None:
            return False
        return previous < level <= current

    def _boll_low(self, bar, period: int, k: float) -> bool:
        closes = self.history(bar.symbol, period)
        if len(closes) < period:
            return False
        window = closes[-period:]
        middle = sum(window) / float(period)
        deviation = _std(window, middle)
        return bar.close < middle - k * deviation

    def _momentum(self, bar, period: int, pct: float) -> bool:
        closes = self.history(bar.symbol, period + 1)
        if len(closes) < period + 1:
            return False
        base = closes[-period - 1]
        if not base:
            return False
        return (bar.close / base - 1.0) * 100.0 >= pct

    def _volume_surge(self, bar, period: int, ratio: float) -> bool:
        volumes = self.history(bar.symbol, period + 1, field="volume")
        if len(volumes) < period + 1:
            return False
        average = sum(volumes[-period - 1:-1]) / float(period)
        return average > 0 and bar.volume >= average * ratio

    # ---------------------------------------------------------- 退出

    def _exit_if_needed(self, bar, position) -> None:
        exits = (self.profile_spec.rules or {}).get("exit") or {}
        cost = float(position.avg_cost or 0)
        change = (bar.close - cost) / cost if cost > 0 else 0.0
        stop = float(exits.get("stop_pct") or 0) / 100.0
        take = float(exits.get("take_pct") or 0) / 100.0
        if stop > 0 and change <= -stop:
            self.close(bar.symbol, reason="止损 %.1f%%" % (stop * 100))
            return
        if take > 0 and change >= take:
            self.close(bar.symbol, reason="止盈 %.1f%%" % (take * 100))
            return

        period = int(exits.get("ma_period") or 0)
        if period > 0:
            closes = self.history(bar.symbol, period)
            average = _sma(closes, period)
            if average is not None and bar.close < average:
                self.close(bar.symbol, reason="跌破 %d 日均线" % period)
                return

        max_hold = int(exits.get("max_hold_days") or 0)
        if max_hold > 0 and self._held.get(bar.symbol, 0) >= max_hold:
            self.close(bar.symbol, reason="持有满 %d 个交易日" % max_hold)


def _std(values: List[float], mean: float) -> float:
    if not values:
        return 0.0
    variance = sum((value - mean) ** 2 for value in values) / float(len(values))
    return variance ** 0.5


def _rsi_series(closes: List[float], period: int) -> List[Optional[float]]:
    """简单 RSI 序列；预热期为 None，长度与 closes 对齐。"""
    result: List[Optional[float]] = [None] * len(closes)
    if len(closes) <= period:
        return result
    gains: List[float] = []
    losses: List[float] = []
    for index in range(1, len(closes)):
        diff = closes[index] - closes[index - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
    for index in range(period, len(closes)):
        average_gain = sum(gains[index - period:index]) / float(period)
        average_loss = sum(losses[index - period:index]) / float(period)
        if average_loss == 0:
            result[index] = 100.0
        else:
            rs = average_gain / average_loss
            result[index] = 100.0 - 100.0 / (1.0 + rs)
    return result


def _sma(values: List[float], period: int) -> Optional[float]:
    clean = [float(v) for v in values if v is not None and v == v]
    if len(clean) < period:
        return None
    window = clean[-period:]
    return sum(window) / float(period)


def make_rule_class(spec: ProfileSpec) -> Type[RuleStrategy]:
    """把一条自由组合档案变成可注册的策略类。"""
    spec.rules = validate_rules(spec.rules)
    summary = summarize_rules(spec.rules)
    meta = StrategyMeta(
        key=spec.key,
        name=spec.name,
        category="自定义",
        risk_level="中",
        description=summary,
        principle=summary + "\n\n条件与退出规则都在策略库 → 编辑里修改，不需要写文件。",
        works_when="条件同时成立时入场，退出规则负责离场。",
        fails_when="条件过多或参数拟合历史时，换一段行情可能失效。",
        tags=("组合", "可视化"),
        version="1.0.0",
    )
    name = "Rule_%s" % spec.profile_id
    return type(name, (RuleStrategy,), {
        "profile_spec": spec,
        "meta": meta,
    })


def make_profile_class(spec: ProfileSpec, base_cls: Type[Strategy]) -> Type[ProfileStrategy]:
    """把一条档案变成可注册的策略类；类属性不可变，实例各自持模板状态。"""
    base_meta = base_cls.meta
    overlay_text = spec.summary()
    base_desc = (base_meta.description or base_meta.name).rstrip("。")
    base_principle = (base_meta.principle or base_desc).rstrip("。")
    meta = StrategyMeta(
        key=spec.key,
        name=spec.name,
        category=base_meta.category,
        risk_level=base_meta.risk_level,
        description="%s。本策略由策略工坊基于「%s」保存，可随时编辑参数。"
                    % (base_desc, base_meta.name),
        principle="%s\n\n保存参数：%s。" % (base_principle, overlay_text),
        works_when=base_meta.works_when,
        fails_when=base_meta.fails_when,
        tags=tuple(base_meta.tags) + ("可视化",),
        version="1.0.0",
    )
    name = "Profile_%s" % spec.profile_id
    return type(name, (ProfileStrategy,), {
        "profile_spec": spec,
        "base_cls": base_cls,
        "meta": meta,
    })
