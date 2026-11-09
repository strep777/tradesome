"""配置：默认值、加载、校验、原子保存。

原则：
1. 任何非法值都不抛异常，而是回退默认值并记录一条警告（用户不该被配置卡死）；
2. 保存使用"临时文件 + os.replace"，避免断电写出半个 JSON；
3. 新增字段必须有默认值，旧配置文件无需迁移即可读取。
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

SCHEMA_VERSION = 1
CONFIG_FILENAME = "settings.json"

VALID_THEMES = ("macos", "dark", "light")
VALID_START_PAGES = ("dashboard", "market", "strategy_lab", "backtest", "live", "live_trade")
VALID_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
VALID_GATEWAY_KINDS = ("sim", "qmt")

RULE_KEYS = (
    "single_trade", "single_symbol", "gross_exposure", "daily_loss",
    "drawdown", "order_rate", "price_band", "session",
)


def _num(data: Dict[str, Any], key: str, default: float, low: float, high: float,
         warnings: List[str], label: str) -> float:
    raw = data.get(key, default)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        warnings.append("%s 不是数字，已使用默认值 %s" % (label, default))
        return float(default)
    if value != value or value in (float("inf"), float("-inf")):
        warnings.append("%s 不是有限数字，已使用默认值" % label)
        return float(default)
    if value < low or value > high:
        clamped = max(low, min(high, value))
        warnings.append("%s 超出范围 [%s, %s]，已修正为 %s" % (label, low, high, clamped))
        return float(clamped)
    return value


def _int(data: Dict[str, Any], key: str, default: int, low: int, high: int,
         warnings: List[str], label: str) -> int:
    raw = data.get(key, default)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        warnings.append("%s 不是整数，已使用默认值 %s" % (label, default))
        return int(default)
    if value < low or value > high:
        clamped = max(low, min(high, value))
        warnings.append("%s 超出范围 [%s, %s]，已修正为 %s" % (label, low, high, clamped))
        return int(clamped)
    return value


def _bool(data: Dict[str, Any], key: str, default: bool) -> bool:
    raw = data.get(key, default)
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(raw, (int, float)):
        return bool(raw)
    return bool(default)


def _choice(data: Dict[str, Any], key: str, default: str, choices: Tuple[str, ...],
            warnings: List[str], label: str) -> str:
    raw = data.get(key, default)
    value = str(raw).strip()
    if value not in choices:
        warnings.append("%s 取值 %r 不支持，已使用 %s" % (label, value, default))
        return default
    return value


# ---------------------------------------------------------------- 各配置段


@dataclass
class AppearanceConfig:
    theme: str = "macos"
    font_scale: float = 1.0
    start_page: str = "dashboard"
    show_tips: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {"theme": self.theme, "font_scale": self.font_scale,
                "start_page": self.start_page, "show_tips": self.show_tips}


@dataclass
class TradingDefaults:
    initial_cash: float = 100000.0
    commission_rate: float = 0.00025
    min_commission: float = 5.0
    stamp_tax_rate: float = 0.0005
    transfer_fee_rate: float = 0.00001
    slippage_bps: float = 2.0
    max_volume_ratio: float = 0.05
    benchmark: str = "000300.SH"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "initial_cash": self.initial_cash,
            "commission_rate": self.commission_rate,
            "min_commission": self.min_commission,
            "stamp_tax_rate": self.stamp_tax_rate,
            "transfer_fee_rate": self.transfer_fee_rate,
            "slippage_bps": self.slippage_bps,
            "max_volume_ratio": self.max_volume_ratio,
            "benchmark": self.benchmark,
        }


@dataclass
class RiskDefaults:
    single_trade_pct: float = 0.25
    single_symbol_pct: float = 0.25
    max_gross_exposure: float = 0.80
    daily_loss_halt: float = 0.03
    daily_loss_close: float = 0.05
    max_drawdown_reduce: float = 0.15
    max_drawdown_halt: float = 0.20
    max_orders_per_minute: int = 20
    enabled_rules: Tuple[str, ...] = RULE_KEYS
    kill_switch: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "single_trade_pct": self.single_trade_pct,
            "single_symbol_pct": self.single_symbol_pct,
            "max_gross_exposure": self.max_gross_exposure,
            "daily_loss_halt": self.daily_loss_halt,
            "daily_loss_close": self.daily_loss_close,
            "max_drawdown_reduce": self.max_drawdown_reduce,
            "max_drawdown_halt": self.max_drawdown_halt,
            "max_orders_per_minute": self.max_orders_per_minute,
            "enabled_rules": list(self.enabled_rules),
            "kill_switch": self.kill_switch,
        }


@dataclass
class DataDefaults:
    default_timeframe: str = "1d"
    auto_generate_demo: bool = False
    auto_install_akshare: bool = True
    synthetic_seed: int = 42

    def to_dict(self) -> Dict[str, Any]:
        return {
            "default_timeframe": self.default_timeframe,
            "auto_generate_demo": self.auto_generate_demo,
            "auto_install_akshare": self.auto_install_akshare,
            "synthetic_seed": self.synthetic_seed,
        }


@dataclass
class LiveDefaults:
    """实盘交易配置。默认关闭，启用需在界面输入确认词。"""

    enabled: bool = False
    gateway_kind: str = "sim"
    qmt_path: str = ""
    qmt_account: str = ""
    qmt_account_type: str = "STOCK"
    auto_trade: bool = False
    run_time: str = "14:55"
    max_order_value: float = 50000.0
    max_orders_per_day: int = 200
    allow_market_orders: bool = False
    require_confirm: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "gateway_kind": self.gateway_kind,
            "qmt_path": self.qmt_path,
            "qmt_account": self.qmt_account,
            "qmt_account_type": self.qmt_account_type,
            "auto_trade": self.auto_trade,
            "run_time": self.run_time,
            "max_order_value": self.max_order_value,
            "max_orders_per_day": self.max_orders_per_day,
            "allow_market_orders": self.allow_market_orders,
            "require_confirm": self.require_confirm,
        }


@dataclass
class SecurityDefaults:
    vault_enabled: bool = True
    mask_secrets: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {"vault_enabled": self.vault_enabled, "mask_secrets": self.mask_secrets}


@dataclass
class AdvancedConfig:
    log_level: str = "INFO"
    max_workers: int = 2
    backtest_progress_every: int = 50

    def to_dict(self) -> Dict[str, Any]:
        return {
            "log_level": self.log_level,
            "max_workers": self.max_workers,
            "backtest_progress_every": self.backtest_progress_every,
        }


@dataclass
class AppConfig:
    schema: int = SCHEMA_VERSION
    appearance: AppearanceConfig = field(default_factory=AppearanceConfig)
    trading: TradingDefaults = field(default_factory=TradingDefaults)
    risk: RiskDefaults = field(default_factory=RiskDefaults)
    data: DataDefaults = field(default_factory=DataDefaults)
    security: SecurityDefaults = field(default_factory=SecurityDefaults)
    live: LiveDefaults = field(default_factory=LiveDefaults)
    advanced: AdvancedConfig = field(default_factory=AdvancedConfig)
    first_run: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": self.schema,
            "appearance": self.appearance.to_dict(),
            "trading": self.trading.to_dict(),
            "risk": self.risk.to_dict(),
            "data": self.data.to_dict(),
            "security": self.security.to_dict(),
            "live": self.live.to_dict(),
            "advanced": self.advanced.to_dict(),
            "first_run": self.first_run,
        }

    def copy(self) -> "AppConfig":
        return config_from_dict(self.to_dict())[0]


def default_config() -> AppConfig:
    return AppConfig()


# ---------------------------------------------------------------- 解析


def _section(raw: Dict[str, Any], key: str, warnings: List[str]) -> Dict[str, Any]:
    value = raw.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        warnings.append("%s 配置段格式错误，已使用默认值" % key)
        return {}
    return value


def _time_text(value: Any, warnings: List[str]) -> str:
    """校验 HH:MM 格式；非法时回退 14:55。"""
    text = str(value or "").strip()
    try:
        hour, minute = text.split(":")
        if 0 <= int(hour) <= 23 and 0 <= int(minute) <= 59:
            return "%02d:%02d" % (int(hour), int(minute))
    except (ValueError, TypeError):
        pass
    warnings.append("自动交易时间格式应为 HH:MM，已使用 14:55")
    return "14:55"


def config_from_dict(raw: Optional[Dict[str, Any]]) -> Tuple[AppConfig, List[str]]:
    """把 JSON 字典解析为 AppConfig，非法值修正并给出警告。"""
    warnings: List[str] = []
    if raw is None:
        return default_config(), warnings
    if not isinstance(raw, dict):
        return default_config(), ["配置文件根节点必须是对象，已使用默认配置"]

    a = _section(raw, "appearance", warnings)
    appearance = AppearanceConfig(
        theme=_choice(a, "theme", "macos", VALID_THEMES, warnings, "主题"),
        font_scale=_num(a, "font_scale", 1.0, 0.8, 1.6, warnings, "字体缩放"),
        start_page=_choice(a, "start_page", "dashboard", VALID_START_PAGES,
                           warnings, "启动页"),
        show_tips=_bool(a, "show_tips", True),
    )

    t = _section(raw, "trading", warnings)
    trading = TradingDefaults(
        initial_cash=_num(t, "initial_cash", 100000.0, 1000.0, 1e9, warnings, "初始资金"),
        commission_rate=_num(t, "commission_rate", 0.00025, 0.0, 0.003,
                             warnings, "佣金率"),
        min_commission=_num(t, "min_commission", 5.0, 0.0, 100.0,
                            warnings, "最低佣金"),
        stamp_tax_rate=_num(t, "stamp_tax_rate", 0.0005, 0.0, 0.003,
                            warnings, "印花税率"),
        transfer_fee_rate=_num(t, "transfer_fee_rate", 0.00001, 0.0, 0.001,
                               warnings, "过户费率"),
        slippage_bps=_num(t, "slippage_bps", 2.0, 0.0, 100.0, warnings, "滑点"),
        max_volume_ratio=_num(t, "max_volume_ratio", 0.05, 0.0001, 1.0,
                              warnings, "成交量占比上限"),
        benchmark=str(t.get("benchmark", "000300.SH") or "000300.SH"),
    )

    r = _section(raw, "risk", warnings)
    enabled_raw = r.get("enabled_rules")
    if enabled_raw is None:
        enabled = RULE_KEYS
    elif isinstance(enabled_raw, (list, tuple)):
        picked = tuple(str(x) for x in enabled_raw if str(x) in RULE_KEYS)
        unknown = [str(x) for x in enabled_raw if str(x) not in RULE_KEYS]
        if unknown:
            warnings.append("未知风控规则已忽略：%s" % ", ".join(unknown))
        enabled = picked or RULE_KEYS
    else:
        warnings.append("enabled_rules 必须是数组，已使用全部规则")
        enabled = RULE_KEYS
    risk = RiskDefaults(
        single_trade_pct=_num(r, "single_trade_pct", 0.02, 0.001, 0.5,
                              warnings, "单笔风险占比"),
        single_symbol_pct=_num(r, "single_symbol_pct", 0.25, 0.01, 1.0,
                               warnings, "单标的占比上限"),
        max_gross_exposure=_num(r, "max_gross_exposure", 0.80, 0.05, 1.0,
                                warnings, "总仓位上限"),
        daily_loss_halt=_num(r, "daily_loss_halt", 0.03, 0.005, 0.5,
                             warnings, "当日亏损停开仓阈值"),
        daily_loss_close=_num(r, "daily_loss_close", 0.05, 0.005, 0.9,
                              warnings, "当日亏损强平阈值"),
        max_drawdown_reduce=_num(r, "max_drawdown_reduce", 0.15, 0.01, 0.9,
                                 warnings, "回撤降仓阈值"),
        max_drawdown_halt=_num(r, "max_drawdown_halt", 0.20, 0.01, 0.95,
                               warnings, "回撤停机阈值"),
        max_orders_per_minute=_int(r, "max_orders_per_minute", 20, 1, 600,
                                   warnings, "每分钟下单上限"),
        enabled_rules=enabled,
        kill_switch=_bool(r, "kill_switch", False),
    )
    if risk.daily_loss_close <= risk.daily_loss_halt:
        risk.daily_loss_close = min(0.9, risk.daily_loss_halt + 0.01)
        warnings.append("强平阈值必须大于停开仓阈值，已自动上调")
    if risk.max_drawdown_halt <= risk.max_drawdown_reduce:
        risk.max_drawdown_halt = min(0.95, risk.max_drawdown_reduce + 0.01)
        warnings.append("停机回撤阈值必须大于降仓阈值，已自动上调")

    d = _section(raw, "data", warnings)
    data = DataDefaults(
        default_timeframe=_choice(d, "default_timeframe", "1d",
                                  ("1d", "1w", "1M"), warnings, "默认周期"),
        auto_generate_demo=_bool(d, "auto_generate_demo", False),
        auto_install_akshare=_bool(d, "auto_install_akshare", True),
        synthetic_seed=_int(d, "synthetic_seed", 42, 0, 999999, warnings, "示例数据种子"),
    )

    s = _section(raw, "security", warnings)
    security = SecurityDefaults(
        vault_enabled=_bool(s, "vault_enabled", True),
        mask_secrets=_bool(s, "mask_secrets", True),
    )

    adv = _section(raw, "advanced", warnings)
    advanced = AdvancedConfig(
        log_level=_choice(adv, "log_level", "INFO", VALID_LOG_LEVELS,
                          warnings, "日志级别"),
        max_workers=_int(adv, "max_workers", 2, 1, 8, warnings, "后台线程数"),
        backtest_progress_every=_int(adv, "backtest_progress_every", 50, 1, 1000,
                                     warnings, "回测进度上报间隔"),
    )

    lv = _section(raw, "live", warnings)
    live = LiveDefaults(
        enabled=_bool(lv, "enabled", False),
        gateway_kind=_choice(lv, "gateway_kind", "sim", VALID_GATEWAY_KINDS,
                             warnings, "实盘网关类型"),
        qmt_path=str(lv.get("qmt_path", "") or "").strip(),
        qmt_account=str(lv.get("qmt_account", "") or "").strip(),
        qmt_account_type=_choice(lv, "qmt_account_type", "STOCK",
                                 ("STOCK", "CREDIT"), warnings, "QMT 账号类型"),
        auto_trade=_bool(lv, "auto_trade", False),
        run_time=_time_text(lv.get("run_time", "14:55"), warnings),
        max_order_value=_num(lv, "max_order_value", 50000.0, 1000.0, 1e8,
                             warnings, "单笔金额上限"),
        max_orders_per_day=_int(lv, "max_orders_per_day", 200, 1, 10000,
                                warnings, "单日委托上限"),
        allow_market_orders=_bool(lv, "allow_market_orders", False),
        require_confirm=_bool(lv, "require_confirm", True),
    )

    config = AppConfig(
        schema=_int(raw, "schema", SCHEMA_VERSION, 1, 99, warnings, "配置版本"),
        appearance=appearance,
        trading=trading,
        risk=risk,
        data=data,
        security=security,
        live=live,
        advanced=advanced,
        first_run=_bool(raw, "first_run", True),
    )
    return config, warnings


# ---------------------------------------------------------------- IO


def load_config(path: Union[str, Path, None] = None) -> Tuple[AppConfig, List[str]]:
    """读取配置。文件不存在返回默认值（不视为错误）。"""
    warnings: List[str] = []
    if path is None:
        return default_config(), warnings
    target = Path(path)
    if not target.exists():
        return default_config(), warnings
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        warnings.append("配置文件读取失败（%s），已使用默认配置" % exc)
        return default_config(), warnings
    if not text.strip():
        warnings.append("配置文件为空，已使用默认配置")
        return default_config(), warnings
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        backup = target.with_suffix(".bad.json")
        try:
            target.replace(backup)
            warnings.append("配置文件格式损坏，已备份为 %s 并使用默认配置" % backup.name)
        except OSError:
            warnings.append("配置文件格式损坏，已使用默认配置")
        return default_config(), warnings
    config, parse_warnings = config_from_dict(raw)
    warnings.extend(parse_warnings)
    return config, warnings


def save_config(config: AppConfig, path: Union[str, Path]) -> Path:
    """原子保存配置，返回实际写入路径。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(config.to_dict(), ensure_ascii=False, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=".settings-", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, str(target))
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return target


def dataclass_to_dict(value: Any) -> Dict[str, Any]:
    """通用 dataclass 转字典（供扩展配置段使用）。"""
    if not is_dataclass(value):
        raise TypeError("需要 dataclass 实例")
    return {f.name: getattr(value, f.name) for f in fields(value)}
