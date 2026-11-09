"""回测性能基准：固定种子，输出每次耗时与 P50/P95，超标时退出码非零。

用法：
    python tools/bench_backtest.py                # 默认 3 次，1 个标的
    python tools/bench_backtest.py --repeat 5 --symbols 3
    python tools/bench_backtest.py --memory       # 额外报告峰值内存
    python tools/bench_backtest.py --startup      # 额外测量冷启动

基准口径：内存库 + 内置合成行情（1167 根/标的），跑 2022-01-04..2026-10-30
的 ma_cross 回测；P95 超过 --limit（默认 2.0 秒）则退出码 1。
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
import tracemalloc
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tradesome.app.bootstrap import build_container, shutdown  # noqa: E402
from tradesome.core.types import BacktestConfig  # noqa: E402

START = date(2022, 1, 4)
END = date(2026, 10, 30)


def percentile(values, ratio: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, int(round(ratio * (len(ordered) - 1)))))
    return ordered[index]


def measure_startup() -> float:
    began = time.perf_counter()
    container = build_container(memory_db=True, with_demo=False)
    try:
        return time.perf_counter() - began
    finally:
        shutdown(container)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="TradeSome 回测性能基准")
    parser.add_argument("--repeat", type=int, default=3, help="重复次数（默认 3）")
    parser.add_argument("--symbols", type=int, default=1, help="标的数量（默认 1）")
    parser.add_argument("--strategy", default="ma_cross", help="策略 key")
    parser.add_argument("--limit", type=float, default=2.0, help="P95 上限（秒）")
    parser.add_argument("--memory", action="store_true", help="报告峰值内存")
    parser.add_argument("--startup", action="store_true", help="测量冷启动耗时")
    args = parser.parse_args(argv)

    if args.startup:
        seconds = measure_startup()
        print("startup      %.3fs（build_container，内存库）" % seconds)

    container = build_container(memory_db=True, with_demo=True)
    try:
        market = container.services.market
        symbols = [row["symbol"] for row in market.quotes()
                   if row.get("has_data")][:max(1, args.symbols)]
        if not symbols:
            print("没有可用行情，无法基准测试", file=sys.stderr)
            return 2
        config = BacktestConfig(symbols=tuple(symbols), start=START, end=END,
                                initial_cash=100000.0,
                                strategy_key=args.strategy, benchmark=True)
        service = container.services.backtest
        times = []
        peak = 0
        trades = 0
        for index in range(max(1, args.repeat)):
            if args.memory:
                tracemalloc.start()
            began = time.perf_counter()
            run_id = service.start(config)
            service.join(300.0)
            elapsed = time.perf_counter() - began
            if args.memory:
                current, top = tracemalloc.get_traced_memory()
                tracemalloc.stop()
                peak = max(peak, top)
            result = service.latest_result()
            if result is None or result.run_id != run_id:
                print("第 %d 次回测失败：%s" % (index + 1, service.last_error),
                      file=sys.stderr)
                return 3
            if index == 0:
                trades = int(result.metrics.get("trade_count") or 0)
            times.append(elapsed)
            print("run %-2d %-18s %.3fs" % (index + 1, run_id, elapsed))

        print("symbols      %s" % ", ".join(symbols))
        print("trades       %d" % trades)
        print("runs         %d" % len(times))
        print("P50          %.3fs" % statistics.median(times))
        print("P95          %.3fs（上限 %.2fs）" % (percentile(times, 0.95), args.limit))
        if args.memory:
            print("peak memory  %.1f MB" % (peak / (1024.0 * 1024.0)))
        return 0 if percentile(times, 0.95) <= args.limit else 1
    finally:
        shutdown(container)


if __name__ == "__main__":
    raise SystemExit(main())
