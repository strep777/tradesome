"""行情/数据服务：界面上的"数据中心"与"行情"页都通过它取数。"""

from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from ...core.errors import DataError, ValidationError
from ...core.events import DATA_DELETED, DATA_GENERATED, DATA_IMPORTED
from ...core.instruments import normalize_symbol
from ...core.logging import get_logger
from ...core.types import Bar
from ...data.catalog import BENCHMARK, DEMO_END, DEMO_START, UNIVERSE, demo_universe
from ...data.providers.csv_provider import export_bars_csv, read_csv_bars
from ...data.providers.synthetic_provider import SyntheticProvider
from ...data.quality import check_bars
from ...data.repository import MarketDataRepository
from ...security.validation import safe_join, validate_symbol

LOGGER = get_logger("app.market")

# 固定版本避免 pip 在几百个 akshare 版本间回溯；该版本支持 Python >=3.9。
AKSHARE_REQUIREMENT = "akshare==1.18.88"


class MarketService:
    def __init__(self, container) -> None:
        self.c = container
        self.repo = MarketDataRepository(container.repos.bars)
        self.provider = SyntheticProvider(seed=container.config.data.synthetic_seed,
                                          registry=container.instruments)

    # ---------------------------------------------------------- 行情列表

    def quotes(self) -> List[dict]:
        rows: List[dict] = []
        for instrument in self.c.instruments.tradable():
            bars = self.repo.get_bars(instrument.symbol, "1d", limit=2)
            latest = bars[-1] if bars else None
            previous = bars[-2] if len(bars) > 1 else None
            change = 0.0
            if latest and previous and previous.close > 0:
                change = latest.close / previous.close - 1.0
            rows.append({
                "symbol": instrument.symbol, "name": instrument.name,
                "kind": instrument.kind, "price": latest.close if latest else None,
                "change_pct": change, "dt": latest.dt if latest else None,
                "volume": latest.volume if latest else 0,
                "has_data": latest is not None,
            })
        rows.sort(key=lambda r: (not r["has_data"], r["symbol"]))
        return rows

    def universe(self) -> List[dict]:
        out = []
        for item in demo_universe():
            inst = self.c.instruments.get(item.symbol)
            out.append({"symbol": item.symbol, "name": item.name,
                        "sector": item.sector, "style": item.style,
                        "kind": inst.kind if inst else "stock"})
        return out

    def candles(self, symbol: str, timeframe: str = "1d",
                limit: int = 250) -> List[Bar]:
        return self.repo.get_bars(symbol, timeframe, limit=limit)

    def history(self, symbol: str, timeframe: str = "1d",
                start=None, end=None) -> List[Bar]:
        return self.repo.get_bars(symbol, timeframe, start=start, end=end)

    def last_price(self, symbol: str) -> Optional[float]:
        bars = self.repo.get_bars(symbol, "1d", limit=1)
        return bars[-1].close if bars else None

    # ---------------------------------------------------------- 覆盖率

    def coverage(self) -> List[dict]:
        rows = self.repo.symbols()
        for row in rows:
            inst = self.c.instruments.get(row["symbol"])
            row["name"] = inst.name if inst else row["symbol"]
        return rows

    def summary(self) -> dict:
        rows = self.coverage()
        total_bars = sum(r["count"] for r in rows)
        return {"symbols": len(rows), "bars": total_bars,
                "first": min((r["first"] for r in rows), default=None),
                "last": max((r["last"] for r in rows), default=None)}

    def quality(self, symbol: str, timeframe: str = "1d"):
        bars = self.repo.get_bars(symbol, timeframe)
        inst = self.c.instruments.get(symbol)
        limit = inst.price_limit_pct if inst else 0.10
        return check_bars(bars, symbol, price_limit_pct=limit)

    # ---------------------------------------------------------- 生成/导入/导出

    def generate_demo(self, force: bool = False,
                      start=None, end=None) -> Dict[str, int]:
        start = start or DEMO_START
        end = end or DEMO_END
        generated: Dict[str, int] = {}
        for item in UNIVERSE:
            bars = self.provider.fetch_bars(item.symbol, start, end)
            if not bars:
                continue
            if force:
                self.repo.delete_symbol(item.symbol)
            generated[item.symbol] = self.repo.upsert_bars(bars)
        total = sum(generated.values())
        self.c.bus.emit(DATA_GENERATED, symbols=len(generated), bars=total)
        self.c.repos.audit.write("data", "generate", detail="生成演示数据 %d 根" % total)
        LOGGER.info("生成演示数据：%d 个标的 %d 根", len(generated), total)
        return generated

    def ensure_demo(self) -> Dict[str, int]:
        if self.repo.count() > 0:
            return {}
        return self.generate_demo()

    def import_csv(self, path: Path) -> dict:
        source = Path(path)
        if not source.exists():
            raise DataError("文件不存在：%s" % source)
        stem = normalize_symbol(source.stem)
        symbol = validate_symbol(stem)
        bars = read_csv_bars(source, symbol)
        if not bars:
            raise DataError("CSV 中没有解析到有效行情：%s" % source.name)
        inst = self.c.instruments.get(symbol)
        report = check_bars(bars, symbol,
                            price_limit_pct=inst.price_limit_pct if inst else 0.10)
        saved = self.repo.upsert_bars(bars)
        self.c.bus.emit(DATA_IMPORTED, symbol=symbol, bars=saved)
        self.c.repos.audit.write("data", "import",
                                 detail="导入 %s：%d 根" % (symbol, saved))
        return {"symbol": symbol, "bars": saved, "report": report}

    # ---------------------------------------------------------- akshare 真实行情

    @property
    def _tool_path(self) -> Path:
        return Path(__file__).resolve().parents[3] / "tools" / "akshare_sync.py"

    @property
    def _vendor_dir(self) -> Path:
        return Path(__file__).resolve().parents[3] / "vendor" / "akshare"

    @staticmethod
    def akshare_installed() -> bool:
        import importlib.util

        try:
            return importlib.util.find_spec("akshare") is not None
        except (ImportError, ValueError):
            return False

    @staticmethod
    def _wheel_python_spec(wheel: Path) -> str:
        """读 wheel 的 Requires-Python；读不到时返回空串（按兼容处理）。"""
        try:
            with zipfile.ZipFile(str(wheel)) as archive:
                for name in archive.namelist():
                    if name.endswith(".dist-info/METADATA"):
                        for line in archive.read(name).decode(
                                "utf-8", "replace").splitlines():
                            if line.startswith("Requires-Python:"):
                                return line.split(":", 1)[1].strip()
        except Exception:  # noqa: BLE001 - 坏 wheel 交给 pip 报错
            return ""
        return ""

    @staticmethod
    def _version_tuple(text: str):
        parts = []
        for piece in str(text).split("."):
            digits = "".join(ch for ch in piece if ch.isdigit())
            if not digits:
                break
            parts.append(int(digits))
        return tuple(parts)

    @classmethod
    def _spec_allows_current_python(cls, spec: str) -> bool:
        if not spec:
            return True
        current = sys.version_info[:3]
        for clause in spec.strip().strip("()").split(","):
            clause = clause.strip()
            if not clause:
                continue
            for operator in (">=", "<=", "==", "!=", ">", "<", "~="):
                if clause.startswith(operator):
                    raw = clause[len(operator):].strip()
                    break
            else:
                continue
            parts = cls._version_tuple(raw.rstrip(".*"))
            if not parts:
                continue
            width = max(len(parts), 3)
            left = current + (0,) * (width - len(current))
            right = parts + (0,) * (width - len(parts))
            if operator == ">=" and not left >= right:
                return False
            if operator == ">" and not left > right:
                return False
            if operator == "<=" and not left <= right:
                return False
            if operator == "<" and not left < right:
                return False
            if operator == "==" and not left[:len(parts)] == right[:len(parts)]:
                return False
            if operator == "!=" and left[:len(parts)] == right[:len(parts)]:
                return False
            if operator == "~=" and not left >= right:
                return False
        return True

    def _install_command(self, wheels: List[Path], direct: bool = False) -> List[str]:
        cmd = [sys.executable, "-m", "pip", "install",
               "--disable-pip-version-check", "--no-input",
               "--progress-bar", "off", "--timeout", "20", "--retries", "1"]
        if direct:
            # 显式空代理：Windows 会从注册表读系统代理，仅清环境变量不够
            cmd += ["--proxy", ""]
        if wheels:
            # 只把本地目录作为额外的候选来源，不屏蔽在线索引：
            # 缺依赖时 pip 仍可联网补齐，不会被一个 wheel 卡死。
            cmd += ["--find-links", str(self._vendor_dir)]
        cmd.append(AKSHARE_REQUIREMENT)
        return cmd

    @staticmethod
    def _pip_env(direct: bool) -> dict:
        """直连时清掉代理；代理模式保留系统设置。"""
        env = dict(os.environ)
        env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
        if direct:
            for key in list(env.keys()):
                if key.upper() in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
                    env.pop(key, None)
            env["NO_PROXY"] = "*"
            env["no_proxy"] = "*"
        return env

    @staticmethod
    def _pip_reason(output: str) -> str:
        keys = ("ERROR:", "Requires-Python", "No matching distribution",
                "Could not find a version", "Could not build wheels")
        for line in reversed((output or "").splitlines()):
            text = line.strip()
            if text and any(key in text for key in keys):
                return text[:240]
        return ""

    def install_akshare(self, timeout: int = 900) -> dict:
        """自动把 akshare 装进当前 Python，必要时使用 vendor/akshare 的 wheel。

        只安装固定包名，不接受外部输入；失败返回可读原因，由界面重试。
        注意：不兼容当前 Python 的 wheel 会被跳过，避免 no-index 死锁。
        """
        root = self._tool_path.parent.parent
        vendor = self._vendor_dir
        all_wheels = sorted(vendor.glob("*.whl")) if vendor.is_dir() else []
        wheels = [w for w in all_wheels
                  if self._spec_allows_current_python(self._wheel_python_spec(w))]
        skipped = [w for w in all_wheels if w not in wheels]
        flags = 0
        if os.name == "nt":
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        attempts = [(True, timeout), (False, timeout)]
        output = ""
        failures: List[str] = []
        ok = False
        returncode = None
        for direct, budget in attempts:
            label = "直连" if direct else "代理"
            cmd = self._install_command(wheels, direct=direct)
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True,
                                      encoding="utf-8", errors="replace",
                                      timeout=budget, cwd=str(root),
                                      env=self._pip_env(direct),
                                      creationflags=flags)
            except subprocess.TimeoutExpired as exc:
                output = self._timeout_output(exc)
                failures.append("%s超时（%d 秒）" % (label, budget))
                # 直连都超时说明是下载慢，不必再用更慢的代理重来一遍
                if direct:
                    break
                continue
            except Exception as exc:  # noqa: BLE001
                failures.append("%s失败：%s" % (label, exc))
                continue
            output = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
            returncode = proc.returncode
            if proc.returncode == 0:
                ok = True
                break
            failures.append("%s：%s" % (label, self._pip_reason(output) or
                                        "pip 退出码 %s" % proc.returncode))
        if ok:
            message = "akshare 安装完成。"
        else:
            message = "自动安装失败：%s" % ("；".join(failures)[:260] or "未知原因")
            if skipped:
                message += "。vendor 中 %d 个 wheel 与 Python %d.%d 不兼容，已跳过" % (
                    len(skipped), sys.version_info[0], sys.version_info[1])
        try:
            self.c.repos.audit.write(
                "data", "akshare_install",
                detail="%s，可用包 %d 个，跳过 %d 个，尝试 %d 次" % (
                    "成功" if ok else "失败", len(wheels), len(skipped),
                    len(failures) + (1 if ok else 0)))
        except Exception:  # noqa: BLE001 - 审计失败不影响安装结果
            pass
        LOGGER.info("akshare 自动安装：ok=%s wheels=%d skipped=%d rc=%s attempts=%d",
                    ok, len(wheels), len(skipped), returncode, len(failures) + (1 if ok else 0))
        return {"ok": ok, "returncode": returncode, "message": message,
                "output": output[-4000:], "wheels": len(wheels),
                "skipped_wheels": len(skipped)}

    @staticmethod
    def _timeout_output(exc) -> str:
        partial = ""
        for chunk in (getattr(exc, "stdout", None), getattr(exc, "stderr", None)):
            if not chunk:
                continue
            if isinstance(chunk, bytes):
                chunk = chunk.decode("utf-8", "replace")
            partial += chunk
        return partial

    def ensure_akshare(self, timeout: int = 900) -> dict:
        """已安装则直接返回；否则自动安装并复查，供界面/启动时调用。"""
        if self.akshare_installed():
            return {"ok": True, "installed": False, "message": "akshare 已安装。",
                    "output": ""}
        result = self.install_akshare(timeout=timeout)
        installed = self.akshare_installed()
        result["installed"] = installed
        if result.get("ok") and not installed:
            result["ok"] = False
            result["message"] = ("安装命令返回成功，但当前解释器仍无法导入 akshare；"
                                 "可能装到了其它 Python。")
        return result

    def akshare_status(self) -> dict:
        """问一次抓取工具 akshare 是否可用（不联网，只检查本机安装）。"""
        tool = self._tool_path
        if not tool.exists():
            return {"available": False, "message": "缺少 tools/akshare_sync.py"}
        try:
            proc = subprocess.run(
                [sys.executable, str(tool), "--check"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=60, cwd=str(tool.parent.parent))
        except subprocess.TimeoutExpired:
            return {"available": False, "message": "检查 akshare 超时"}
        except Exception as exc:  # noqa: BLE001
            return {"available": False, "message": str(exc)}
        message = (proc.stdout or proc.stderr or "").strip()
        return {"available": proc.returncode == 0, "message": message}

    def sync_akshare(self, symbols: List[str], start=None, end=None,
                     adjust: str = "qfq", timeout: int = 1800) -> dict:
        """用 tools/akshare_sync.py 抓取并落 CSV，再逐份导入本库。"""
        cleaned = [validate_symbol(s.strip().upper()) for s in symbols if s and s.strip()]
        if not cleaned:
            raise ValidationError("至少选择一个标的")
        tool = self._tool_path
        if not tool.exists():
            raise DataError("缺少抓取工具：tools/akshare_sync.py")
        out_dir = Path(self.c.paths.data_dir) / "akshare"
        cmd = [sys.executable, str(tool), "--symbols", ",".join(cleaned),
               "--adjust", adjust, "--out", str(out_dir)]
        if start:
            cmd += ["--start", start.strftime("%Y-%m-%d") if hasattr(start, "strftime")
                    else str(start)]
        if end:
            cmd += ["--end", end.strftime("%Y-%m-%d") if hasattr(end, "strftime")
                    else str(end)]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=timeout, cwd=str(tool.parent.parent))
        except subprocess.TimeoutExpired as exc:
            raise DataError("同步超时（%d 秒）。请缩小日期区间或标的数量后重试。" % timeout) from exc

        summary = None
        for line in (proc.stdout or "").splitlines()[::-1]:
            line = line.strip()
            if line.startswith("{"):
                try:
                    summary = json.loads(line)
                    break
                except ValueError:
                    continue
        if proc.returncode != 0 and summary is None:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise DataError("akshare 同步失败：%s" % (detail[-500:] or "未知错误"))
        if summary is None:
            raise DataError("akshare 同步没有返回结果，请查看命令行输出")

        imported: Dict[str, int] = {}
        errors = list(summary.get("errors") or [])
        for item in summary.get("files") or []:
            path = Path(item.get("path", ""))
            symbol = item.get("symbol") or ""
            if not path.exists():
                errors.append({"symbol": symbol, "message": "CSV 未生成"})
                continue
            try:
                result = self.import_csv(path)
                imported[result["symbol"]] = result["bars"]
            except Exception as exc:  # noqa: BLE001 - 单份导入失败不拖垮整批
                errors.append({"symbol": symbol, "message": str(exc)})
        total = sum(imported.values())
        self.c.bus.emit(DATA_IMPORTED, symbol=",".join(imported), bars=total)
        self.c.repos.audit.write(
            "data", "akshare_sync",
            detail="同步 %d 个标的 %d 根（失败 %d）" % (len(imported), total, len(errors)))
        LOGGER.info("akshare 同步：%d 个标的 %d 根", len(imported), total)
        return {"symbols": len(imported), "bars": total, "imported": imported,
                "errors": errors, "files": summary.get("files") or []}

    def export_csv(self, symbol: str, target_dir: Optional[Path] = None) -> Path:
        bars = self.repo.get_bars(symbol)
        if not bars:
            raise DataError("该标的还没有数据可以导出")
        folder = Path(target_dir) if target_dir else self.c.paths.exports
        folder.mkdir(parents=True, exist_ok=True)
        path = safe_join(folder, "%s.csv" % symbol)
        export_bars_csv(bars, path)
        return path

    def delete(self, symbol: str) -> int:
        count = self.repo.delete_symbol(symbol)
        self.c.bus.emit(DATA_DELETED, symbol=symbol, bars=count)
        self.c.repos.audit.write("data", "delete", detail="删除 %s 的 %d 根数据"
                                 % (symbol, count))
        return count

    def clear_all(self) -> int:
        total = 0
        for row in self.repo.symbols():
            total += self.repo.delete_symbol(row["symbol"])
        return total

    def purge_all(self) -> dict:
        """清空演示/测试数据：行情、回测记录、模拟盘会话与订单成交。"""
        bars = self.clear_all()
        runs = self.c.repos.backtests.clear()
        orders = self.c.repos.orders.clear()
        fills = self.c.repos.fills.clear()
        equity = self.c.repos.equity.clear()
        sessions = self.c.repos.sessions.clear()
        self.c.bus.emit(DATA_DELETED, symbol="*", bars=bars)
        self.c.repos.audit.write(
            "data", "purge",
            detail="清空行情 %d 根/回测 %d 次/会话 %d 个" % (bars, runs, sessions))
        LOGGER.info("清空数据：行情 %d 根，回测 %d 次，会话 %d 个", bars, runs, sessions)
        return {"bars": bars, "backtests": runs, "sessions": sessions,
                "orders": orders, "fills": fills, "equity": equity}

