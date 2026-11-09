"""命令行入口：`python -m tradesome` 启动界面，也可做数据/回测/自检。"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import List, Optional

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_SELFTEST = 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tradesome",
        description="TradeSome —— 本地量化交易研究桌面端（模拟盘 + 实盘）")
    parser.add_argument("--version", action="store_true", help="显示版本号后退出")
    parser.add_argument("--demo", action="store_true", help="启动时强制重新生成演示数据")
    parser.add_argument("--safe-mode", action="store_true", help="安全模式：不加载用户策略")
    parser.add_argument("--reset-ui", action="store_true", help="恢复默认外观设置")
    parser.add_argument("--log-level", default="", help="日志级别 DEBUG/INFO/WARNING/ERROR")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("demo-data", help="生成演示行情后退出").add_argument(
        "--force", action="store_true", help="先删除再生成")
    backtest = sub.add_parser("backtest", help="命令行跑一次回测")
    backtest.add_argument("--strategy", default="ma_cross", help="策略 key")
    backtest.add_argument("--symbols", default="600519.SH", help="逗号分隔的标的")
    backtest.add_argument("--start", default="", help="开始日期 YYYY-MM-DD")
    backtest.add_argument("--end", default="", help="结束日期 YYYY-MM-DD")
    backtest.add_argument("--cash", type=float, default=100000.0, help="初始资金")
    backtest.add_argument("--param", action="append", default=[],
                          help="策略参数 key=value，可重复")
    sub.add_parser("selftest", help="运行内置自检（数据库/策略/回测）")
    clear = sub.add_parser("clear-data", help="清空本机全部行情数据")
    clear.add_argument("--yes", action="store_true", help="确认清空，不做二次询问")
    sync = sub.add_parser("sync-data", help="用 akshare 抓取真实日线后导入")
    sync.add_argument("--symbols", default="600519.SH,000858.SZ,601318.SH",
                      help="逗号分隔的标的")
    sync.add_argument("--start", default="", help="开始日期 YYYY-MM-DD")
    sync.add_argument("--end", default="", help="结束日期 YYYY-MM-DD")
    sync.add_argument("--adjust", default="qfq", choices=["qfq", "hfq", ""],
                      help="复权方式")
    sync.add_argument("--check", action="store_true",
                      help="只检查 akshare 是否可用，不抓取")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.version:
        from . import __version__
        print("TradeSome %s" % __version__)
        return EXIT_OK
    if args.command == "demo-data":
        return _demo_data(args)
    if args.command == "backtest":
        return _backtest(args)
    if args.command == "selftest":
        return _selftest()
    if args.command == "clear-data":
        return _clear_data(args)
    if args.command == "sync-data":
        return _sync_data(args)
    return _gui(args)


# ---------------------------------------------------------------- 子命令

def _gui(args) -> int:
    from .app.bootstrap import build_container, shutdown
    from .ui.app import run
    with_demo = True if args.demo else None
    try:
        container = build_container(with_demo=with_demo)
    except Exception as exc:  # noqa: BLE001
        print("启动失败：%s" % exc, file=sys.stderr)
        return EXIT_ERROR
    try:
        if args.reset_ui:
            container.config.appearance.theme = "macos"
            container.config.appearance.font_scale = 1.0
            container.save_config()
        if args.log_level:
            container.config.advanced.log_level = args.log_level.upper()
        if args.safe_mode:
            _safe_mode(container)
        if args.demo:
            container.services.market.generate_demo(force=True)
        run(container)
        return EXIT_OK
    except Exception as exc:  # noqa: BLE001 - 顶层兜底，给出可读信息
        import traceback
        traceback.print_exc()
        print("程序异常退出：%s" % exc, file=sys.stderr)
        return EXIT_ERROR
    finally:
        try:
            shutdown(container)
        except Exception:  # noqa: BLE001
            pass


def _safe_mode(container) -> None:
    # 安全模式下只保留内置策略，用户策略文件不动，只是本次不加载
    registry = container.strategies
    for key in list(registry.keys()):
        if registry.is_user(key):
            registry.remove_user(key, delete_file=False)


def _demo_data(args) -> int:
    from .app.bootstrap import build_container, shutdown
    container = build_container(with_demo=False)
    try:
        generated = container.services.market.generate_demo(force=args.force)
        total = sum(generated.values())
        print("已生成 %d 个标的，共 %d 根 K 线。" % (len(generated), total))
        return EXIT_OK
    finally:
        shutdown(container)


def _backtest(args) -> int:
    from .app.bootstrap import build_container, shutdown
    from .backtest.metrics import format_metric
    from .core.types import BacktestConfig
    container = build_container(with_demo=False)
    try:
        if container.services.market.summary()["bars"] == 0:
            print("本机还没有行情数据。先运行 sync-data，或用数据中心导入 CSV。",
                  file=sys.stderr)
            return EXIT_USAGE
        service = container.services.backtest
        containers = container.services.market
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
        end = date.fromisoformat(args.end) if args.end else date.today()
        start = date.fromisoformat(args.start) if args.start else end - timedelta(days=365)
        params = {}
        for item in args.param:
            if "=" not in item:
                print("参数格式应为 key=value：%s" % item, file=sys.stderr)
                return EXIT_USAGE
            key, value = item.split("=", 1)
            params[key.strip()] = value.strip()
        config = BacktestConfig(symbols=tuple(symbols), start=start, end=end,
                                initial_cash=args.cash, strategy_key=args.strategy,
                                strategy_params=params)
        run_id = service.start(config)
        print("回测 %s 运行中……" % run_id)
        service.join(300.0)
        result = service.latest_result()
        if result is None:
            print("回测失败：%s" % (service.last_error or "未知原因"), file=sys.stderr)
            return EXIT_ERROR
        for key in ("total_return", "annual_return", "max_drawdown", "sharpe",
                    "win_rate", "trade_count"):
            print("%-14s %s" % (key, format_metric(key, result.metrics.get(key))))
        print("报告：%s" % container.services.report.save_markdown(result))
        return EXIT_OK
    finally:
        shutdown(container)


def _selftest() -> int:
    from .app.bootstrap import build_container, shutdown
    stamp = "[selftest]"
    try:
        container = build_container(memory_db=True, with_demo=False)
        try:
            keys = container.strategies.keys()
            print("%s 策略 %d 个：%s" % (stamp, len(keys), ", ".join(sorted(keys))))
            assert len(keys) >= 6, "内置策略数量不足"
            generated = container.services.market.generate_demo()
            assert sum(generated.values()) > 1000, "演示数据生成失败"
            from .core.types import BacktestConfig
            config = BacktestConfig(
                symbols=("600519.SH",), start=date(2022, 1, 4),
                end=date(2022, 12, 30), strategy_key="ma_cross")
            result = container.services.backtest.start(config)
            container.services.backtest.join(60.0)
            output = container.services.backtest.latest_result()
            assert output is not None, "回测没有结果"
            print("%s 回测完成：%.2f%% 收益，%d 笔交易" % (
                stamp, output.metrics["total_return"] * 100,
                output.metrics["trade_count"]))
            print("%s 全部通过" % stamp)
            return EXIT_OK
        finally:
            shutdown(container)
    except Exception as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        print("%s 失败：%s" % (stamp, exc), file=sys.stderr)
        return EXIT_SELFTEST


def _clear_data(args) -> int:
    from .app.bootstrap import build_container, shutdown
    container = build_container(with_demo=False)
    try:
        summary = container.services.market.summary()
        if not args.yes:
            print("将删除 %d 个标的、%s 根 K 线；追加 --yes 确认执行。"
                  % (summary["symbols"], "{:,}".format(summary["bars"])))
            return EXIT_USAGE
        result = container.services.market.purge_all()
        print("已清空：行情 %d 根、回测 %d 次、会话 %d 个。" % (
            result["bars"], result["backtests"], result["sessions"]))
        return EXIT_OK
    finally:
        shutdown(container)


def _sync_data(args) -> int:
    from .app.bootstrap import build_container, shutdown
    container = build_container(with_demo=False)
    try:
        if getattr(args, "check", False):
            status = container.services.market.akshare_status()
            print(status.get("message") or "akshare 可用。")
            return EXIT_OK if status.get("available") else EXIT_ERROR
        symbols = [item.strip() for item in args.symbols.split(",") if item.strip()]
        start = date.fromisoformat(args.start) if args.start else None
        end = date.fromisoformat(args.end) if args.end else None
        result = container.services.market.sync_akshare(
            symbols, start=start, end=end, adjust=args.adjust)
        print("已导入 %d 个标的，共 %s 根 K 线。" % (
            result["symbols"], "{:,}".format(result["bars"])))
        for item in result.get("errors") or []:
            print("失败 %s：%s" % (item.get("symbol"), item.get("message")),
                  file=sys.stderr)
        return EXIT_OK if result["symbols"] else EXIT_ERROR
    except Exception as exc:  # noqa: BLE001
        print("同步失败：%s" % exc, file=sys.stderr)
        return EXIT_ERROR
    finally:
        shutdown(container)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
