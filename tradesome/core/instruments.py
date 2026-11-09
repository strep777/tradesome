"""标的注册表与交易规则推导。

一个 Instrument 同时携带"是什么"（名称/类型）和"怎么交易"（手数/涨跌停/
T+1/费用），因此回测、风控、撮合都只依赖它，新增市场只需新增规则，
不必修改任何策略或引擎代码。
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Tuple

from .errors import ValidationError
from .money import round_price
from .types import (
    KIND_ETF, KIND_INDEX, KIND_STOCK, FeeProfile, Instrument,
)

# (代码, 名称, 类型) —— 演示与内置策略使用的标的池
_CATALOG: Tuple[Tuple[str, str, str], ...] = (
    ("600519.SH", "贵州茅台", KIND_STOCK),
    ("601318.SH", "中国平安", KIND_STOCK),
    ("600036.SH", "招商银行", KIND_STOCK),
    ("600900.SH", "长江电力", KIND_STOCK),
    ("601899.SH", "紫金矿业", KIND_STOCK),
    ("600030.SH", "中信证券", KIND_STOCK),
    ("688981.SH", "中芯国际", KIND_STOCK),
    ("688111.SH", "金山办公", KIND_STOCK),
    ("000001.SZ", "平安银行", KIND_STOCK),
    ("000858.SZ", "五粮液", KIND_STOCK),
    ("002594.SZ", "比亚迪", KIND_STOCK),
    ("002415.SZ", "海康威视", KIND_STOCK),
    ("300750.SZ", "宁德时代", KIND_STOCK),
    ("300059.SZ", "东方财富", KIND_STOCK),
    ("301236.SZ", "软通动力", KIND_STOCK),
    ("510300.SH", "沪深300ETF", KIND_ETF),
    ("510500.SH", "中证500ETF", KIND_ETF),
    ("512880.SH", "证券ETF", KIND_ETF),
    ("518880.SH", "黄金ETF", KIND_ETF),
    ("588000.SH", "科创50ETF", KIND_ETF),
    ("159915.SZ", "创业板ETF", KIND_ETF),
    ("159919.SZ", "沪深300ETF深", KIND_ETF),
    ("000300.SH", "沪深300", KIND_INDEX),
    ("000001.SH", "上证指数", KIND_INDEX),
    ("399001.SZ", "深证成指", KIND_INDEX),
    ("399006.SZ", "创业板指", KIND_INDEX),
)

_INDEX_SYMBOLS = {s for s, _n, k in _CATALOG if k == KIND_INDEX}
_ETF_PREFIXES = ("15", "16", "50", "51", "52", "56", "58")


def guess_exchange(symbol: str) -> str:
    """由 6 位代码推断交易所。"""
    code = symbol.split(".")[0].strip()
    if code.startswith(("6", "5", "9")):
        return "SH"
    if code.startswith(("4", "8")):
        return "BJ"
    return "SZ"


def normalize_symbol(symbol: str) -> str:
    """把 600519 / sh600519 / 600519.sh 统一成 600519.SH。"""
    text = str(symbol or "").strip().upper()
    if not text:
        raise ValidationError("标的代码不能为空")
    if text.startswith(("SH", "SZ", "BJ")) and len(text) >= 8:
        text = "%s.%s" % (text[2:], text[:2])
    if text.endswith((".SH", ".SZ", ".BJ")):
        code, suffix = text.rsplit(".", 1)
        if not (code.isdigit() and len(code) == 6):
            raise ValidationError("标的代码格式不正确：%s" % symbol,
                                  hint="正确格式如 600519.SH")
        return "%s.%s" % (code, suffix)
    if text.isdigit() and len(text) == 6:
        return "%s.%s" % (text, guess_exchange(text))
    raise ValidationError("标的代码格式不正确：%s" % symbol,
                          hint="请使用 6 位数字代码，如 600519 或 600519.SH")


def guess_board(symbol: str, kind: str = KIND_STOCK) -> str:
    code = symbol.split(".")[0]
    if kind == KIND_INDEX:
        return "INDEX"
    if kind == KIND_ETF:
        return "ETF"
    if symbol.endswith(".BJ"):
        return "BJ"
    if code.startswith(("688", "689")):
        return "STAR"
    if code.startswith(("300", "301")):
        return "CHINEXT"
    return "MAIN"


def price_limit_for(board: str, name: str = "") -> float:
    if "ST" in (name or "").upper():
        return 0.05
    return {
        "STAR": 0.20,
        "CHINEXT": 0.20,
        "BJ": 0.30,
        "ETF": 0.10,
        "INDEX": 1.0,  # 指数不可交易，给一个不生效的上限
        "MAIN": 0.10,
    }.get(board, 0.10)


def lot_size_for(board: str) -> int:
    return 200 if board == "STAR" else 100


def make_instrument(symbol: str, name: str = "", kind: str = KIND_STOCK,
                    board: Optional[str] = None) -> Instrument:
    """按代码规则生成标的元数据（名称未知时用代码兜底）。"""
    symbol = normalize_symbol(symbol)
    board = board or guess_board(symbol, kind)
    fee = FeeProfile()
    if kind == KIND_ETF:
        # ETF 免印花税
        fee = FeeProfile(stamp_tax_rate=0.0)
    if kind == KIND_INDEX:
        fee = FeeProfile(commission_rate=0.0, min_commission=0.0,
                         stamp_tax_rate=0.0, transfer_fee_rate=0.0, slippage_bps=0.0)
    return Instrument(
        symbol=symbol,
        name=name or symbol,
        kind=kind,
        lot_size=lot_size_for(board),
        price_limit_pct=price_limit_for(board, name),
        t_plus=0 if kind == KIND_INDEX else 1,
        tick_size=0.001 if kind == KIND_ETF else 0.01,
        fee=fee,
        board=board,
    )


class InstrumentRegistry:
    """标的注册表：查不到就按代码规则自动生成，永不 KeyError。"""

    def __init__(self, instruments: Optional[Iterable[Instrument]] = None) -> None:
        self._items: Dict[str, Instrument] = {}
        for item in instruments or ():
            self.register(item)

    # ---------------------------------------------------------- 构建

    @classmethod
    def with_defaults(cls) -> "InstrumentRegistry":
        registry = cls()
        for symbol, name, kind in _CATALOG:
            registry.register(make_instrument(symbol, name, kind))
        return registry

    def register(self, instrument: Instrument) -> Instrument:
        if not isinstance(instrument, Instrument):
            raise ValidationError("只能注册 Instrument 对象")
        self._items[instrument.symbol] = instrument
        return instrument

    def ensure(self, symbol: str, name: str = "", kind: str = KIND_STOCK) -> Instrument:
        symbol = normalize_symbol(symbol)
        found = self._items.get(symbol)
        if found is not None:
            return found
        return self.register(make_instrument(symbol, name, kind))

    # ---------------------------------------------------------- 查询

    def get(self, symbol: str, default: Optional[Instrument] = None) -> Optional[Instrument]:
        try:
            key = normalize_symbol(symbol)
        except ValidationError:
            return default
        item = self._items.get(key)
        if item is None:
            if default is not None:
                return default
            return self.ensure(key)
        return item

    def must_get(self, symbol: str) -> Instrument:
        item = self.get(symbol)
        if item is None:  # pragma: no cover - get 从不返回 None
            raise ValidationError("未知标的：%s" % symbol)
        return item

    def all(self) -> List[Instrument]:
        return [self._items[k] for k in sorted(self._items)]

    def by_kind(self, kind: str) -> List[Instrument]:
        return [i for i in self.all() if i.kind == kind]

    def stocks(self) -> List[Instrument]:
        return self.by_kind(KIND_STOCK)

    def etfs(self) -> List[Instrument]:
        return self.by_kind(KIND_ETF)

    def indices(self) -> List[Instrument]:
        return self.by_kind(KIND_INDEX)

    def tradable(self) -> List[Instrument]:
        return [i for i in self.all() if not i.is_index]

    def symbols(self, tradable_only: bool = False) -> List[str]:
        items = self.tradable() if tradable_only else self.all()
        return [i.symbol for i in items]

    def display(self, symbol: str) -> str:
        item = self.get(symbol)
        return item.display if item else str(symbol)

    def search(self, keyword: str) -> List[Instrument]:
        text = str(keyword or "").strip().upper()
        if not text:
            return self.all()
        return [i for i in self.all()
                if text in i.symbol.upper() or text in i.name.upper()]

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, symbol: object) -> bool:
        try:
            return normalize_symbol(str(symbol)) in self._items
        except ValidationError:
            return False


# ---------------------------------------------------------------- 交易规则


def price_limits(instrument: Instrument, prev_close: float) -> Tuple[float, float]:
    """返回 (跌停价, 涨停价)，按最小价位对齐。"""
    if prev_close <= 0:
        return (0.0, 0.0)
    upper = round_price(prev_close * (1.0 + instrument.price_limit_pct), instrument.tick_size)
    lower = round_price(prev_close * (1.0 - instrument.price_limit_pct), instrument.tick_size)
    return (max(0.01, lower), upper)


def validate_qty(instrument: Instrument, qty: int) -> Tuple[bool, str]:
    """校验下单数量是否符合板块规则。"""
    if qty is None or int(qty) <= 0:
        return False, "下单数量必须大于 0"
    qty = int(qty)
    if instrument.is_index:
        return False, "指数不可直接交易"
    if instrument.board == "STAR":
        if qty < 200:
            return False, "科创板单笔申报不少于 200 股"
        return True, ""
    if qty % instrument.lot_size != 0:
        return False, "%s 需按 %d 股整数倍下单" % (instrument.name, instrument.lot_size)
    return True, ""


def round_qty(instrument: Instrument, qty: float) -> int:
    """把数量向下取整到手数；科创板不足 200 股按 0 处理。"""
    if instrument.is_index or qty <= 0:
        return 0
    lot = instrument.lot_size
    result = int(qty // lot) * lot
    if instrument.board == "STAR" and result < 200:
        return 0
    return result


DEFAULT_REGISTRY = InstrumentRegistry.with_defaults()


def default_registry() -> InstrumentRegistry:
    """返回进程内共享的默认注册表（只读使用；需要隔离时自行 new）。"""
    return DEFAULT_REGISTRY


def build_registry(items: Optional[Iterable[Instrument]] = None) -> InstrumentRegistry:
    registry = InstrumentRegistry.with_defaults()
    for item in items or ():
        registry.register(item)
    return registry
