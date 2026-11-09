"""报告服务：把回测结果整理成可读的 Markdown 与 CSV 文件。"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from ...backtest.metrics import format_metric
from ...core.errors import DataError
from ...core.logging import get_logger
from ...core.types import BacktestResult
from ...security.validation import safe_join, sanitize_csv_cell

LOGGER = get_logger("app.report")

METRIC_LABELS = [
    ("total_return", "累计收益"), ("annual_return", "年化收益"),
    ("annual_volatility", "年化波动"), ("sharpe", "夏普比率"),
    ("sortino", "索提诺比率"), ("max_drawdown", "最大回撤"),
    ("max_dd_duration", "最长回撤天数"), ("calmar", "卡玛比率"),
    ("win_rate", "胜率"), ("profit_factor", "盈亏比"),
    ("payoff_ratio", "平均盈亏比"), ("trade_count", "交易笔数"),
    ("turnover", "换手率"), ("total_fees", "总费用"),
    ("fee_ratio", "费用占比"), ("exposure", "平均仓位"),
    ("benchmark_return", "基准收益"), ("alpha", "超额收益(年化)"),
    ("beta", "Beta"),
]


class ReportService:
    def __init__(self, container) -> None:
        self.c = container

    # ---------------------------------------------------------- 摘要

    def summary(self, result: BacktestResult) -> Dict[str, str]:
        metrics = result.metrics or {}
        return {key: format_metric(key, metrics.get(key)) for key, _ in METRIC_LABELS
                if key in metrics}

    def headline(self, result: BacktestResult) -> str:
        metrics = result.metrics or {}
        return "%s ｜ 收益 %s ｜ 回撤 %s ｜ 夏普 %s ｜ %d 笔" % (
            result.config.strategy_key or "回测",
            format_metric("total_return", metrics.get("total_return")),
            format_metric("max_drawdown", metrics.get("max_drawdown")),
            format_metric("sharpe", metrics.get("sharpe")),
            int(metrics.get("trade_count") or 0))

    # ---------------------------------------------------------- Markdown

    def build_markdown(self, result: BacktestResult) -> str:
        config = result.config
        metrics = result.metrics or {}
        lines: List[str] = []
        lines.append("# 回测报告 %s" % result.run_id)
        lines.append("")
        lines.append("> 本报告由 TradeSome 生成，仅用于策略研究，不构成投资建议。")
        lines.append("")
        lines.append("## 一、配置")
        lines.append("")
        lines.append("| 项目 | 内容 |")
        lines.append("| --- | --- |")
        lines.append("| 策略 | %s |" % (config.strategy_key or "-"))
        lines.append("| 标的 | %s |" % "、".join(config.symbols))
        lines.append("| 区间 | %s 至 %s |" % (config.start, config.end))
        lines.append("| 周期 | %s |" % config.timeframe)
        lines.append("| 初始资金 | %s 元 |" % ("{:,.2f}".format(config.initial_cash)))
        lines.append("| 基准 | %s |" % (config.benchmark or "-"))
        lines.append("| 参数 | %s |" % (", ".join("%s=%s" % (k, v)
                                                for k, v in sorted(config.strategy_params.items()))
                                        or "-"))
        lines.append("")
        lines.append("## 二、核心指标")
        lines.append("")
        lines.append("| 指标 | 数值 |")
        lines.append("| --- | --- |")
        for key, label in METRIC_LABELS:
            if key in metrics:
                lines.append("| %s | %s |" % (label, format_metric(key, metrics[key])))
        lines.append("")
        lines.append("## 三、交易明细")
        lines.append("")
        if result.trades:
            lines.append("| # | 标的 | 买入 | 卖出 | 数量 | 买入价 | 卖出价 | 盈亏 | 收益率 | 费用 | 持有天数 | 原因 |")
            lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
            for index, trade in enumerate(result.trades, 1):
                lines.append("| %d | %s | %s | %s | %d | %.2f | %.2f | %.2f | %s | %.2f | %d | %s |" % (
                    index, trade.symbol, trade.open_dt.date(), trade.close_dt.date(),
                    trade.qty, trade.entry_price, trade.exit_price, trade.pnl,
                    format_metric("win_rate", trade.pnl_pct), trade.fees,
                    trade.holding_days, trade.exit_reason or "-"))
        else:
            lines.append("（本次回测没有产生平仓交易）")
        lines.append("")
        lines.append("## 四、运行信息")
        lines.append("")
        lines.append("- 开始：%s" % result.started_at)
        lines.append("- 结束：%s" % result.finished_at)
        lines.append("- 用时：%.2f 秒" % result.duration_sec)
        lines.append("- K 线数量：%d" % len(result.equity_curve))
        if result.cancelled:
            lines.append("- 状态：用户取消")
        lines.append("")
        lines.append("## 五、重要提示")
        lines.append("")
        lines.append("1. 回测收益不代表未来表现，参数越多越容易过拟合。")
        lines.append("2. 请用滚动验证或模拟盘观察，再考虑是否用于实盘。")
        lines.append("3. 真实成交还受流动性、涨跌停与滑点影响。")
        lines.append("")
        return "\n".join(lines)

    def save_markdown(self, result: BacktestResult,
                      path: Optional[Path] = None) -> Path:
        target = Path(path) if path else safe_join(
            self.c.paths.exports, "%s.md" % result.run_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.build_markdown(result), encoding="utf-8")
        self.c.repos.audit.write("report", "save", detail=str(target))
        return target

    # ---------------------------------------------------------- CSV

    def export_trades_csv(self, result: BacktestResult,
                          path: Optional[Path] = None) -> Path:
        target = Path(path) if path else safe_join(
            self.c.paths.exports, "%s-trades.csv" % result.run_id)
        if not result.trades:
            raise DataError("这次回测没有交易记录可以导出")
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["序号", "标的", "买入时间", "卖出时间", "数量",
                             "买入价", "卖出价", "盈亏", "收益率", "费用",
                             "持有天数", "平仓原因"])
            for index, trade in enumerate(result.trades, 1):
                writer.writerow([index, sanitize_csv_cell(trade.symbol),
                                 trade.open_dt.strftime("%Y-%m-%d %H:%M"),
                                 trade.close_dt.strftime("%Y-%m-%d %H:%M"),
                                 trade.qty, round(trade.entry_price, 4),
                                 round(trade.exit_price, 4), round(trade.pnl, 2),
                                 round(trade.pnl_pct, 6), round(trade.fees, 2),
                                 trade.holding_days,
                                 sanitize_csv_cell(trade.exit_reason or "")])
        return target

    def export_equity_csv(self, result: BacktestResult,
                          path: Optional[Path] = None) -> Path:
        target = Path(path) if path else safe_join(
            self.c.paths.exports, "%s-equity.csv" % result.run_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["日期", "权益", "回撤"])
            drawdowns = dict(result.drawdown_curve or [])
            for moment, value in result.equity_curve:
                writer.writerow([moment.strftime("%Y-%m-%d"), round(value, 2),
                                 round(drawdowns.get(moment, 0.0), 6)])
        return target

    def export_all(self, result: BacktestResult) -> Dict[str, str]:
        folder = self.c.paths.exports
        folder.mkdir(parents=True, exist_ok=True)
        files = {
            "markdown": str(self.save_markdown(result)),
            "equity": str(self.export_equity_csv(result)),
        }
        try:
            files["trades"] = str(self.export_trades_csv(result))
        except DataError:
            pass
        return files
