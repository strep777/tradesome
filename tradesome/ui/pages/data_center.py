"""数据中心：同步 akshare 真实行情、导入 CSV、检查质量、导出与删除。"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog

from .. import theme
from ..widgets import Button, Card, DataTable, EmptyState, SectionHeader
from ..widgets.akshare_sync import AkshareSyncModal
from .. import formatting as fmt
from .base import BasePage


class DataPage(BasePage):
    title = "数据中心"
    subtitle = "数据是回测的地基。全市场行情用 akshare 同步，也可以导入标准 CSV。"

    help_steps = [
        '点「同步全部 A 股」抓取 5000 多只当日快照，约 1 分钟；之后行情页可搜索全部 A 股。',
        '点「同步真实行情」给选中的标的抓多年日线，用于回测；需要联网。',
        '导入 CSV 时文件名用代码命名（如 600519.SH.csv）；选中标的后可检查质量、导出或删除。',
    ]

    def tour_targets(self) -> dict:
        return {"generate": getattr(self, "_sync_button", None)}

    def build(self) -> None:
        self._symbol = None
        header = self.page_header()
        bar = tk.Frame(header, bg=theme.color("bg"))
        bar.pack(side="right")
        self._sync_button = Button(bar, "同步真实行情", command=self._sync,
                                   variant="primary", size="sm")
        self._sync_button.pack(side="left", padx=(0, 6))
        Button(bar, "同步全部 A 股", command=self._sync_all, variant="secondary",
               size="sm").pack(side="left", padx=(0, 6))
        Button(bar, "导入 CSV", command=self._import, variant="secondary",
               size="sm").pack(side="left", padx=(0, 6))
        Button(bar, "导出选中", command=self._export, variant="ghost",
               size="sm").pack(side="left", padx=(0, 6))
        Button(bar, "删除选中", command=self._delete, variant="danger",
               size="sm").pack(side="left")

        body = tk.Frame(self, bg=theme.color("bg"))
        body.pack(fill="both", expand=True, padx=theme.px(24), pady=(0, theme.px(16)))

        left = Card(body, padding=12)
        left.configure(width=theme.px(640))
        left.pack(side="left", fill="both")
        left.pack_propagate(False)
        head = tk.Frame(left.body, bg=theme.color("surface"))
        head.pack(fill="x")
        self.summary_label = tk.Label(head, text="", bg=theme.color("surface"),
                                      fg=theme.color("muted"), font=theme.font("caption"),
                                      anchor="w")
        self.summary_label.pack(side="left")
        Button(head, "打开数据目录", command=self._open_dir, variant="ghost",
               size="sm").pack(side="right")
        self.table = DataTable(left.body, [
            ("symbol", "标的", 96), ("name", "名称", 100),
            ("timeframe", "周期", 50, "center"), ("count", "根数", 66, "e"),
            ("first", "起始", 96), ("last", "结束", 96),
        ], height=18, on_select=self._on_select,
            on_double=lambda r: self._check())
        self.table.pack(fill="both", expand=True, pady=(theme.px(8), 0))
        self.empty = EmptyState(left.body, "还没有任何行情数据",
                                "点右上角「同步真实行情」从 akshare 抓取，或导入自己的 CSV。",
                                "同步真实行情", self._sync)

        right = Card(body)
        right.pack(side="left", fill="both", expand=True, padx=(theme.px(12), 0))
        header2 = SectionHeader(right.body, "数据质量",
                                "导入后自动检查；有错误的数据不建议直接用于回测。")
        header2.pack(fill="x")
        Button(header2.actions, "检查选中", command=self._check, variant="secondary",
               size="sm").pack(side="right")
        self.quality_text = tk.Text(right.body, bg=theme.color("surface_alt"),
                                    fg=theme.color("text"), bd=0, relief="flat",
                                    font=(theme.MONO_FAMILY, 9), wrap="word",
                                    padx=theme.px(10), pady=theme.px(8),
                                    state="disabled", height=16)
        self.quality_text.pack(fill="both", expand=True, pady=(theme.px(8), theme.px(8)))
        tk.Label(right.body,
                 text=("CSV 约定：文件名用代码命名（如 600519.SH.csv 或 600519.csv），"
                       "表头支持中文（日期/开盘/最高/最低/收盘/成交量/成交额）或英文；"
                       "编码 utf-8 / gbk 均可，单文件不超过 100MB。"),
                 bg=theme.color("surface"), fg=theme.color("faint"),
                 font=theme.font("small"), justify="left", anchor="w",
                 wraplength=theme.px(420)).pack(fill="x")

    # ---------------------------------------------------------- 刷新

    def refresh(self) -> None:
        rows = []
        for row in self.c.services.market.coverage():
            rows.append({
                "symbol": row["symbol"], "name": row.get("name") or "",
                "timeframe": row.get("timeframe", "1d"), "count": row.get("count", 0),
                "first": fmt.date_text(row.get("first")),
                "last": fmt.date_text(row.get("last")),
            })
        self.table.set_rows(rows)
        if rows:
            self.empty.pack_forget()
            if not self.table.winfo_ismapped():
                self.table.pack(fill="both", expand=True, pady=(theme.px(8), 0))
        else:
            self.table.pack_forget()
            if not self.empty.winfo_ismapped():
                self.empty.pack(fill="both", expand=True)
        summary = self.c.services.market.summary()
        self.summary_label.configure(
            text="%d 个标的 · %s 根 K 线 · %s ~ %s" % (
                summary["symbols"], "{:,}".format(summary["bars"]),
                fmt.date_text(summary["first"]), fmt.date_text(summary["last"])))
        if self._symbol is None and rows:
            first = self.table.select_first()
            if first:
                self._symbol = first["symbol"]
        if rows and not self.quality_text.get("1.0", "end").strip():
            self._check()

    def _on_select(self, row) -> None:
        if row:
            self._symbol = row["symbol"]
            self._check()

    # ---------------------------------------------------------- 动作

    def _sync(self) -> None:
        def done(summary: dict) -> None:
            self._symbol = None
            self.quality_text.configure(state="normal")
            self.quality_text.delete("1.0", "end")
            self.quality_text.configure(state="disabled")
            self.refresh()
            self.shell.refresh_status()
            self.toast("已同步 %d 个标的，共 %s 根 K 线" % (
                summary.get("symbols", 0), "{:,}".format(summary.get("bars", 0))),
                "success")

        AkshareSyncModal(self, self.c.services.market, on_done=done).show()

    def _sync_all(self) -> None:
        """一次抓全市场当日快照，约 1 分钟。"""
        def done(summary: dict) -> None:
            self._symbol = None
            self.refresh()
            self.shell.refresh_status()
            self.toast("已同步 %s 只 A 股" % "{:,}".format(
                summary.get("symbols", 0)), "success")

        modal = AkshareSyncModal(self, self.c.services.market, on_done=done)
        modal.show()
        modal.after(120, modal._sync_all)

    def _import(self) -> None:
        paths = filedialog.askopenfilenames(
            parent=self, title="选择 CSV 文件",
            filetypes=[("CSV 文件", "*.csv"), ("所有文件", "*.*")])
        if not paths:
            return
        self.set_busy(True)
        imported = 0
        try:
            for path in paths:
                try:
                    result = self.c.services.market.import_csv(path)
                    imported += 1
                    report = result["report"]
                    self.toast("已导入 %s：%d 根，%d 个提醒" % (
                        result["symbol"], result["bars"], len(report.issues)), "success")
                except Exception as exc:  # noqa: BLE001
                    self.error("导入失败：%s" % os.path.basename(path), str(exc))
        finally:
            self.set_busy(False)
        if imported:
            self.refresh()
            self.shell.refresh_status()

    def _check(self) -> None:
        symbol = self._symbol
        if not symbol:
            return
        try:
            report = self.c.services.market.quality(symbol)
        except Exception as exc:  # noqa: BLE001
            self.error("检查失败", str(exc))
            return
        self.quality_text.configure(state="normal")
        self.quality_text.delete("1.0", "end")
        head = "%s · 检查 %d 根 · 错误 %d · 提醒 %d\n\n" % (
            symbol, report.checked, len(report.errors), len(report.warnings))
        self.quality_text.insert("end", head)
        if not report.issues:
            self.quality_text.insert("end", "没有发现问题。\n")
        for issue in report.issues[:60]:
            mark = "✕" if issue.severity == "error" else "!"
            self.quality_text.insert("end", "%s [%s] %s\n" % (
                mark, issue.code, issue.message))
        if len(report.issues) > 60:
            self.quality_text.insert("end", "…… 其余 %d 条已省略\n" % (
                len(report.issues) - 60))
        self.quality_text.configure(state="disabled")

    def _export(self) -> None:
        if not self._symbol:
            self.toast("先选择一只标的", "warning")
            return
        directory = filedialog.askdirectory(parent=self, title="选择导出目录")
        if not directory:
            return
        try:
            path = self.c.services.market.export_csv(self._symbol, directory)
            self.toast("已导出：%s" % path, "success")
        except Exception as exc:  # noqa: BLE001
            self.error("导出失败", str(exc))

    def _delete(self) -> None:
        if not self._symbol:
            self.toast("先选择一只标的", "warning")
            return
        symbol = self._symbol
        self.shell.confirm(
            "删除行情数据", "将删除 %s 的全部 K 线，历史回测记录会保留但无法重跑。"
            "确定删除吗？" % symbol, lambda: self._do_delete(symbol),
            confirm_text="删除", danger=True)

    def _do_delete(self, symbol: str) -> None:
        try:
            self.c.services.market.delete(symbol)
            self.toast("已删除 %s" % symbol, "success")
        except Exception as exc:  # noqa: BLE001
            self.error("删除失败", str(exc))
        self._symbol = None
        self.refresh()
        self.shell.refresh_status()

    def _open_dir(self) -> None:
        target = str(self.c.paths.data_dir)
        try:
            os.startfile(target)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            self.toast("数据目录：%s" % target, "info")

    def set_busy(self, busy: bool) -> None:
        try:
            self.configure(cursor="watch" if busy else "")
            self.update_idletasks()
        except tk.TclError:
            pass
