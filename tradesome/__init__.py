"""TradeSome —— 本地桌面量化交易工作站。

应用本体零第三方依赖：Python 3.9+ 标准库 + Tkinter + SQLite。
真实行情同步可选使用 akshare，首次启动会自动装到软件目录 `vendor/site`。
"""

import sys as _sys
from pathlib import Path as _Path

# 自动安装的 akshare 及其依赖放在软件目录内，不写系统 Python 的 site-packages。
_VENDOR_SITE = _Path(__file__).resolve().parents[1] / "vendor" / "site"
if _VENDOR_SITE.is_dir() and str(_VENDOR_SITE) not in _sys.path:
    _sys.path.insert(0, str(_VENDOR_SITE))

__version__ = "1.0.0"
__product__ = "TradeSome"
__tagline__ = "本地量化交易工作站"
__author__ = "TradeSome"

RISK_DISCLAIMER = (
    "本软件用于学习与研究，不构成投资建议；实盘默认关闭，需本机 QMT 客户端与逐笔确认。"
    "历史回测不代表未来收益。股市有风险，入市需谨慎。"
)
