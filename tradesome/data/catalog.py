"""演示标的口径：代码、行业、风格标签。

这些标签只用于界面展示与策略选股，不参与计算；
真实交易请以导入数据为准。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional, Sequence

from ..core.instruments import normalize_symbol

DEMO_START = date(2022, 1, 4)
DEMO_END = date(2026, 10, 30)
BENCHMARK = "000300.SH"


@dataclass(frozen=True)
class UniverseItem:
    symbol: str
    name: str
    sector: str
    style: str


UNIVERSE: Sequence[UniverseItem] = (
    UniverseItem("600519.SH", "贵州茅台", "食品饮料", "低波动价值"),
    UniverseItem("000858.SZ", "五粮液", "食品饮料", "价值"),
    UniverseItem("601318.SH", "中国平安", "非银金融", "大盘价值"),
    UniverseItem("600036.SH", "招商银行", "银行", "低波动红利"),
    UniverseItem("600900.SH", "长江电力", "公用事业", "防御红利"),
    UniverseItem("002594.SZ", "比亚迪", "汽车", "高波动成长"),
    UniverseItem("300750.SZ", "宁德时代", "电力设备", "高波动成长"),
    UniverseItem("510300.SH", "沪深300ETF", "宽基指数", "指数"),
)

SECTORS: Dict[str, str] = {item.symbol: item.sector for item in UNIVERSE}
STYLES: Dict[str, str] = {item.symbol: item.style for item in UNIVERSE}


def demo_symbols() -> List[str]:
    return [item.symbol for item in UNIVERSE]


def demo_universe() -> List[UniverseItem]:
    return list(UNIVERSE)


def names() -> Dict[str, str]:
    return {item.symbol: item.name for item in UNIVERSE}


def sector_of(symbol: str) -> str:
    try:
        return SECTORS.get(normalize_symbol(symbol), "其他")
    except Exception:  # noqa: BLE001 - 展示用，非法代码不抛错
        return "其他"


def style_of(symbol: str) -> str:
    try:
        return STYLES.get(normalize_symbol(symbol), "—")
    except Exception:  # noqa: BLE001
        return "—"


def default_symbols(count: int = 6) -> List[str]:
    """默认策略/回测使用的标的数（太多会拖慢首次回测）。"""
    return demo_symbols()[: max(1, int(count))]
