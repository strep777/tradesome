"""自由组合策略：条件表校验、语言化摘要。

用户不写代码、不建文件，只在界面上选择 1–4 个入场条件与一组退出规则。
本模块只做纯数据校验与描述，运行时由 ``profile.RuleStrategy`` 解释执行。

条件表格式（同时存入 strategy_profiles.spec_json 的 rules 键）::

    {
      "match": "all",
      "entry": [{"type": "ma_cross", "fast": 5, "slow": 20}],
      "exit": {"stop_pct": 5.0, "take_pct": 20.0,
               "ma_period": 0, "max_hold_days": 30},
      "weight": 0.2
    }
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Tuple

from ..core.errors import ValidationError
from .base import ParamSpec

MAX_RULES = 4
DEFAULT_WEIGHT = 0.2

WEIGHT_SPEC = ParamSpec(
    "weight", "单标的仓位", "float", DEFAULT_WEIGHT, 0.05, 0.50, 0.05,
    help="每次买入占总资产的比例；组合条件本身在策略工坊里修改")


@dataclass(frozen=True)
class RuleField:
    name: str
    label: str
    default: float
    minimum: float
    maximum: float
    integer: bool = True
    unit: str = ""

    def coerce(self, raw: Any) -> float:
        label = self.label
        try:
            value = float(raw)
        except (TypeError, ValueError):
            raise ValidationError("「%s」需要填数字" % label)
        if value != value or value in (float("inf"), float("-inf")):
            raise ValidationError("「%s」不是有效数字" % label)
        if self.integer:
            value = float(int(round(value)))
        if value < self.minimum or value > self.maximum:
            raise ValidationError("「%s」需在 %g–%g 之间" % (
                label, self.minimum, self.maximum))
        return int(value) if self.integer else round(value, 3)


@dataclass(frozen=True)
class RuleType:
    key: str
    label: str
    fields: Tuple[RuleField, ...]
    phrase: str  # 含 {name} 占位符

    def coerce(self, raw: Mapping[str, Any]) -> Dict[str, Any]:
        result: Dict[str, Any] = {"type": self.key}
        for spec in self.fields:
            result[spec.name] = spec.coerce(raw.get(spec.name, spec.default))
        return result

    def phrase_of(self, rule: Mapping[str, Any]) -> str:
        return self.phrase.format(**{k: rule[k] for k in rule if k != "type"})


RULE_TYPES: Tuple[RuleType, ...] = (
    RuleType(
        "ma_cross", "均线金叉",
        (RuleField("fast", "快线", 5, 2, 120, unit="日"),
         RuleField("slow", "慢线", 20, 5, 250, unit="日")),
        "{fast} 日均线上穿 {slow} 日均线"),
    RuleType(
        "breakout", "突破前高",
        (RuleField("period", "周期", 20, 5, 250, unit="日"),),
        "收盘价突破前 {period} 日最高价"),
    RuleType(
        "rsi_low", "RSI 超卖回升",
        (RuleField("period", "RSI 周期", 14, 2, 60, unit="日"),
         RuleField("level", "回升线", 30, 5, 50, integer=False)),
        "{period} 日 RSI 从 {level} 下方回升"),
    RuleType(
        "boll_low", "布林下轨",
        (RuleField("period", "周期", 20, 5, 120, unit="日"),
         RuleField("k", "标准差倍数", 2.0, 1.0, 4.0, integer=False)),
        "收盘价跌破 {period} 日布林下轨（{k} 倍标准差）"),
    RuleType(
        "momentum", "动量",
        (RuleField("period", "周期", 20, 5, 250, unit="日"),
         RuleField("pct", "涨幅", 3.0, 0.5, 50.0, integer=False, unit="%")),
        "{period} 日涨幅超过 {pct}%"),
    RuleType(
        "volume_surge", "放量",
        (RuleField("period", "均量周期", 20, 5, 120, unit="日"),
         RuleField("ratio", "放量倍数", 1.8, 1.0, 10.0, integer=False)),
        "成交量达到 {period} 日均量的 {ratio} 倍"),
)

BY_TYPE: Dict[str, RuleType] = {item.key: item for item in RULE_TYPES}
RULE_LABELS: Dict[str, str] = {item.key: item.label for item in RULE_TYPES}

EXIT_SPECS: Tuple[ParamSpec, ...] = (
    ParamSpec("stop_pct", "止损（%）", "float", 5.0, 0.0, 50.0, 0.5,
              help="收盘价相对成本下跌超过该比例时清仓；0 表示关闭"),
    ParamSpec("take_pct", "止盈（%）", "float", 20.0, 0.0, 200.0, 0.5,
              help="收盘价相对成本上涨超过该比例时清仓；0 表示关闭"),
    ParamSpec("ma_period", "跌破均线卖出", "int", 0, 0, 250, 5,
              help="收盘价跌破该均线时清仓；0 表示关闭", unit="日"),
    ParamSpec("max_hold_days", "最长持有", "int", 0, 0, 250, 5,
              help="持有满该交易日数后清仓；0 表示关闭", unit="日"),
)
EXIT_NAMES = tuple(spec.name for spec in EXIT_SPECS)
EXIT_DEFAULTS: Dict[str, Any] = {spec.name: spec.default_value() for spec in EXIT_SPECS}


def default_rules() -> Dict[str, Any]:
    return {
        "match": "all",
        "entry": [{"type": "ma_cross", "fast": 5, "slow": 20}],
        "exit": dict(EXIT_DEFAULTS),
        "weight": DEFAULT_WEIGHT,
    }


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise ValidationError("入场条件格式不正确")
    return list(value)


def validate_rules(payload: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """校验并归一化条件表；错误信息直接展示给用户。"""
    if not isinstance(payload, Mapping):
        raise ValidationError("组合条件格式不正确")

    match = str(payload.get("match") or "all").lower()
    if match not in ("all", "any"):
        raise ValidationError("组合方式只能是「全部满足」或「任一满足」")

    raw_entries = _as_list(payload.get("entry"))
    if not raw_entries:
        raise ValidationError("至少添加一条入场条件")
    if len(raw_entries) > MAX_RULES:
        raise ValidationError("入场条件最多 %d 条，条件越多越容易过拟合" % MAX_RULES)

    entries: List[Dict[str, Any]] = []
    for index, raw in enumerate(raw_entries, start=1):
        if not isinstance(raw, Mapping):
            raise ValidationError("第 %d 条入场条件格式不正确" % index)
        kind = str(raw.get("type") or "")
        spec = BY_TYPE.get(kind)
        if spec is None:
            raise ValidationError("第 %d 条入场条件类型不存在" % index)
        rule = spec.coerce(raw)
        if kind == "ma_cross" and rule["fast"] >= rule["slow"]:
            raise ValidationError("第 %d 条：快线周期必须小于慢线周期" % index)
        entries.append(rule)

    raw_exit = payload.get("exit") or {}
    if not isinstance(raw_exit, Mapping):
        raise ValidationError("退出规则格式不正确")
    exits: Dict[str, Any] = {}
    for spec in EXIT_SPECS:
        exits[spec.name] = spec.coerce(raw_exit.get(spec.name, spec.default))
    if not any(float(exits[name]) > 0 for name in EXIT_NAMES):
        raise ValidationError("至少设置一条退出规则（止损、止盈、跌破均线或最长持有）")

    try:
        weight = WEIGHT_SPEC.coerce(payload.get("weight", DEFAULT_WEIGHT))
    except ValidationError as exc:
        raise ValidationError("仓位设置不合法：%s" % exc)

    return {"match": match, "entry": entries, "exit": exits, "weight": weight}


def summarize_rules(rules: Mapping[str, Any]) -> str:
    """把条件表翻成一句人话，用于向导预览与策略详情。"""
    entries = list(rules.get("entry") or [])
    phrases = []
    for rule in entries:
        spec = BY_TYPE.get(str(rule.get("type") or ""))
        if spec is None:
            continue
        try:
            phrases.append(spec.phrase_of(rule))
        except KeyError:
            continue
    if phrases:
        joiner = " 且 " if rules.get("match", "all") == "all" else " 或 "
        entry_text = "当" + joiner.join(phrases) + "时买入"
    else:
        entry_text = "尚未设置入场条件"

    exits = rules.get("exit") or {}
    parts: List[str] = []
    if float(exits.get("stop_pct") or 0) > 0:
        parts.append("止损 %g%%" % float(exits["stop_pct"]))
    if float(exits.get("take_pct") or 0) > 0:
        parts.append("止盈 %g%%" % float(exits["take_pct"]))
    if int(exits.get("ma_period") or 0) > 0:
        parts.append("跌破 %d 日均线卖出" % int(exits["ma_period"]))
    if int(exits.get("max_hold_days") or 0) > 0:
        parts.append("最长持有 %d 个交易日" % int(exits["max_hold_days"]))
    exit_text = "；".join(parts) if parts else "未设置退出规则"

    weight = float(rules.get("weight") or DEFAULT_WEIGHT) * 100.0
    return "%s；%s；单标的仓位 %g%%。" % (
        entry_text, exit_text, round(weight, 1))


def describe_partial(entry: List[Mapping[str, Any]], match: str,
                     exits: Mapping[str, Any], weight: Any) -> str:
    """界面编辑过程中的容错预览：字段不全时也能给出可读文字。"""
    draft = {
        "match": match,
        "entry": [dict(item) for item in entry],
        "exit": dict(exits),
        "weight": weight,
    }
    try:
        return summarize_rules(validate_rules(draft))
    except ValidationError:
        # 只显示能拼出来的部分，避免预览整段消失
        phrases = []
        for rule in entry:
            spec = BY_TYPE.get(str(rule.get("type") or ""))
            if spec is None:
                continue
            try:
                phrases.append(spec.phrase_of(rule))
            except KeyError:
                continue
        if not phrases:
            return "选好条件后，这里会显示一句人话说明。"
        joiner = " 且 " if match == "all" else " 或 "
        return "当" + joiner.join(phrases) + "时买入（退出规则待完善）。"
