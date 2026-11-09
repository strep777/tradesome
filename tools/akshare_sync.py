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
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tradesome.core.types import KIND_ETF, KIND_INDEX  # noqa: E402
from tradesome.data.providers.akshare_csv import write_records  # noqa: E402


def _force_direct() -> None:
    """清掉系统代理环境变量。

    akshare 内部用 requests，会读取 HTTP(S)_PROXY；本机 Clash 代理时快时慢，
    直连 push2his.eastmoney.com 反而稳定。需要代理时用 --use-proxy 跳过。
    """
    for key in list(os.environ):
        if key.upper() in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
            os.environ.pop(key, None)
    os.environ["NO_PROXY"] = "*"
    os.environ["no_proxy"] = "*"


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
        for attempt in range(3):
            try:
                records = _fetch(ak, symbol, kind, start, end, adjust)
                break
            except Exception as exc:  # noqa: BLE001 - 单个标的失败不拖垮整批
                last_error = exc
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
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


def _fetch_spot(ak):
    frame = ak.stock_zh_a_spot_em()
    if frame is None or len(frame) == 0:
        raise RuntimeError("A 股列表接口返回空数据（可能是网络失败或接口变更）")
    return frame.to_dict("records")


def _write_spot(records, target: Path) -> int:
    import csv

    target.parent.mkdir(parents=True, exist_ok=True)
    rows = 0
    with open(str(target), "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["代码", "名称", "日期", "开盘", "最高", "最低",
                         "收盘", "成交量", "成交额", "涨跌幅"])
        for item in records:
            code = str(item.get("代码") or "").strip()
            if len(code) != 6 or not code.isdigit():
                continue
            try:
                close = float(item.get("最新价") or 0)
                open_ = float(item.get("今开") or close)
                high = float(item.get("最高") or close)
                low = float(item.get("最低") or close)
                volume = float(item.get("成交量") or 0)
                amount = float(item.get("成交额") or 0)
            except (TypeError, ValueError):
                continue
            values = (open_, high, low, close)
            if any(v != v for v in values) or close <= 0:
                continue
            if volume != volume:
                volume = 0
            if amount != amount:
                amount = 0
            writer.writerow([
                code, str(item.get("名称") or "").strip(),
                datetime.now().strftime("%Y-%m-%d"),
                "%.3f" % open_, "%.3f" % high, "%.3f" % low, "%.3f" % close,
                int(volume), "%.2f" % amount,
                item.get("涨跌幅") or "",
            ])
            rows += 1
    return rows


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
    parser.add_argument("--all", action="store_true",
                        help="抓取全部 A 股当日快照（约 1 分钟）")
    parser.add_argument("--use-proxy", action="store_true", help="保留系统代理")
    args = parser.parse_args(argv)

    if not args.use_proxy:
        _force_direct()

    if args.all:
        try:
            ak = _import_akshare()
            records = _fetch_spot(ak)
            target = Path(args.out) / "a_share_spot.csv"
            rows = _write_spot(records, target)
            print(json.dumps({"ok": rows > 0, "rows": rows,
                              "file": str(target)}, ensure_ascii=False))
            return 0 if rows else 1
        except Exception as exc:  # noqa: BLE001
            print(json.dumps({"ok": False, "rows": 0, "error": str(exc)},
                             ensure_ascii=False))
            print(str(exc), file=sys.stderr)
            return 1

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
