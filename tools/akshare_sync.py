"""从 akshare 抓取真实日线，写成 TradeSome 可导入的 CSV。

akshare 是可选依赖，只在这个工具里使用；TradeSome 应用本体仍为零第三方依赖。
安装：  pip install akshare
用法：
  python tools\\akshare_sync.py --check
  python tools\\akshare_sync.py --symbols 600519.SH,000858.SZ --start 2022-01-01
结果：data\\akshare\\<代码>.csv，最后一行输出 JSON 供界面解析。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tradesome.core.types import KIND_ETF, KIND_INDEX  # noqa: E402
from tradesome.data.providers.akshare_csv import write_records  # noqa: E402


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _compact(day: date) -> str:
    return day.strftime("%Y%m%d")


def _import_akshare():
    try:
        import akshare as ak  # type: ignore
    except ImportError as exc:  # pragma: no cover - 取决于本机环境
        raise RuntimeError(
            "未安装 akshare。软件启动时会自动安装；也可将离线 wheel 放到 "
            "vendor/akshare/ 后重试。\n"
            "（akshare 依赖 pandas/requests，只用于这个同步工具，不影响软件本体。）"
        ) from exc
    return ak


def _kind_registry():
    from tradesome.core.instruments import InstrumentRegistry

    return InstrumentRegistry().with_defaults()


def _fetch(ak, symbol: str, kind: str, start: date, end: date,
           adjust: str):
    code = symbol.split(".")[0]
    kwargs = {
        "symbol": code,
        "period": "daily",
        "start_date": _compact(start),
        "end_date": _compact(end),
    }
    if kind == KIND_INDEX:
        frame = ak.index_zh_a_hist(**kwargs)
    elif kind == KIND_ETF:
        frame = ak.fund_etf_hist_em(**kwargs, adjust=adjust)
    else:
        frame = ak.stock_zh_a_hist(**kwargs, adjust=adjust)
    if frame is None or len(frame) == 0:
        raise RuntimeError("接口返回空数据（可能是代码不存在、区间无交易日或网络失败）")
    records = frame.to_dict("records")
    return records


def sync(symbols, start: date, end: date, out_dir: Path, adjust: str,
         sleep_seconds: float = 0.4) -> dict:
    ak = _import_akshare()
    registry = _kind_registry()
    files = []
    errors = []
    total = 0
    for index, symbol in enumerate(symbols):
        instrument = registry.get(symbol)
        kind = instrument.kind if instrument is not None else "STOCK"
        records = None
        last_error = None
        for attempt in range(2):
            try:
                records = _fetch(ak, symbol, kind, start, end, adjust)
                break
            except Exception as exc:  # noqa: BLE001 - 单个标的失败不拖垮整批
                last_error = exc
                if attempt == 0:
                    time.sleep(1.0)
        if records is None:
            message = "%s：%s" % (symbol, last_error)
            errors.append({"symbol": symbol, "message": str(last_error)})
            _log("失败 " + message)
            continue
        target = out_dir / ("%s.csv" % symbol)
        try:
            count, skipped = write_records(records, symbol, target)
        except Exception as exc:  # noqa: BLE001
            errors.append({"symbol": symbol, "message": str(exc)})
            _log("写入失败 %s：%s" % (symbol, exc))
            continue
        total += count
        files.append({"symbol": symbol, "path": str(target), "bars": count,
                      "skipped": skipped})
        _log("完成 %s：%d 根%s" % (
            symbol, count, ("，跳过 %d 行" % skipped) if skipped else ""))
        if index < len(symbols) - 1 and sleep_seconds > 0:
            time.sleep(sleep_seconds)
    return {"ok": bool(files), "files": files, "errors": errors,
            "bars": total, "start": start.isoformat(), "end": end.isoformat()}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="用 akshare 抓取 A 股/ETF/指数日线并写成 CSV")
    parser.add_argument("--symbols", default="", help="逗号分隔，如 600519.SH,000858.SZ")
    parser.add_argument("--start", default="", help="YYYY-MM-DD，默认三年前")
    parser.add_argument("--end", default="", help="YYYY-MM-DD，默认今天")
    parser.add_argument("--adjust", default="qfq", choices=["qfq", "hfq", ""],
                        help="复权方式，指数忽略")
    parser.add_argument("--out", default=str(ROOT / "data" / "akshare"))
    parser.add_argument("--sleep", type=float, default=0.4, help="每个标的间隔秒数")
    parser.add_argument("--check", action="store_true", help="只检查 akshare 是否可用")
    args = parser.parse_args(argv)

    if args.check:
        try:
            ak = _import_akshare()
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        version = getattr(ak, "__version__", "unknown")
        print("akshare 可用，版本 %s；Python %s" % (version, sys.version.split()[0]))
        return 0

    if not args.symbols.strip():
        print("请用 --symbols 指定至少一个标的，如 600519.SH", file=sys.stderr)
        return 3
    symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
    try:
        end = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end else date.today()
        start = (datetime.strptime(args.start, "%Y-%m-%d").date() if args.start
                 else date(end.year - 3, end.month, end.day))
    except ValueError as exc:
        print("日期格式应为 YYYY-MM-DD：%s" % exc, file=sys.stderr)
        return 3
    if start >= end:
        print("开始日期必须早于结束日期", file=sys.stderr)
        return 3

    try:
        summary = sync(symbols, start, end, Path(args.out), args.adjust, args.sleep)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print("同步失败：%s" % exc, file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
