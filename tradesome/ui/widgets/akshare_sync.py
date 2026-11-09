"""「同步真实行情」弹窗：选标的与区间，后台调用 tools/akshare_sync.py。"""

from __future__ import annotations

import threading
import tkinter as tk
from datetime import date, timedelta
from tkinter import ttk
from typing import Dict, List, Optional

from .. import theme
from .button import Button
from .form import LabeledEntry
from .modal import Modal

PRESELECT = ("600519.SH", "000858.SZ", "601318.SH")
ADJUST_CHOICES = (("前复权（推荐）", "qfq"), ("后复权", "hfq"), ("不复权", ""))


class AkshareSyncModal(Modal):
    def __init__(self, parent, service, on_done=None) -> None:
        super().__init__(parent, "同步真实行情（akshare）", width=640, height=560)
        self.service = service
        self.on_done = on_done
        self._thread: Optional[threading.Thread] = None
        self._result: Optional[dict] = None
        self._error: Optional[str] = None
        self._running = False
        self._installing = False
        self._install_thread: Optional[threading.Thread] = None
        self._install_result: Optional[dict] = None

        surface = theme.color("surface")
        tk.Label(self.body, text="从 akshare 抓取真实日线，写入本地数据库。",
                 bg=surface, fg=theme.color("text"), font=theme.font("h2"),
                 anchor="w").pack(fill="x")
        tk.Label(self.body, text=("首次使用会自动安装 akshare（需要联网，约 2–5 分钟）；"
                                  "同步会按标的逐个请求，失败会自动重试一次。"),
                 bg=surface, fg=theme.color("muted"), font=theme.font("caption"),
                 anchor="w", justify="left", wraplength=theme.px(590)).pack(
                     fill="x", pady=(2, theme.px(10)))

        self.status_label = tk.Label(
            self.body, text="正在检查 akshare……", bg=theme.color("surface_alt"),
            fg=theme.color("muted"), font=theme.font("small"), anchor="w",
            justify="left", padx=theme.px(10), pady=theme.px(6),
            wraplength=theme.px(560))
        self.status_label.pack(fill="x", pady=(0, theme.px(10)))

        body_row = tk.Frame(self.body, bg=surface)
        body_row.pack(fill="both", expand=True)

        left = tk.Frame(body_row, bg=surface)
        left.pack(side="left", fill="both", expand=True)
        tk.Label(left, text="选择标的", bg=surface, fg=theme.color("muted"),
                 font=theme.font("caption"), anchor="w").pack(fill="x")
        list_frame = tk.Frame(left, bg=surface)
        list_frame.pack(fill="both", expand=True, pady=(theme.px(4), 0))
        self.listbox = tk.Listbox(
            list_frame, selectmode="extended", activestyle="none",
            bg=theme.color("surface"), fg=theme.color("text"), bd=0,
            highlightthickness=1, highlightbackground=theme.color("border"),
            selectbackground=theme.color("selection"),
            selectforeground=theme.color("text"),
            font=theme.font("body"), exportselection=False)
        bar = ttk.Scrollbar(list_frame, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=bar.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")

        right = tk.Frame(body_row, bg=surface)
        right.pack(side="left", fill="y", padx=(theme.px(14), 0))
        right.configure(width=theme.px(230))
        right.pack_propagate(False)
        today = date.today()
        self.start_entry = LabeledEntry(right, "开始日期", str(today - timedelta(days=365 * 3)),
                                        hint="YYYY-MM-DD", width=14)
        self.start_entry.pack(fill="x", pady=(0, theme.px(8)))
        self.end_entry = LabeledEntry(right, "结束日期", str(today),
                                      hint="YYYY-MM-DD", width=14)
        self.end_entry.pack(fill="x", pady=(0, theme.px(8)))
        tk.Label(right, text="复权方式", bg=surface, fg=theme.color("muted"),
                 font=theme.font("caption"), anchor="w").pack(fill="x")
        self.adjust_var = tk.StringVar(value=ADJUST_CHOICES[0][0])
        combo = ttk.Combobox(right, textvariable=self.adjust_var, state="readonly",
                             values=[label for label, _ in ADJUST_CHOICES],
                             font=theme.font("body"))
        combo.pack(fill="x", pady=(theme.px(2), theme.px(10)))
        tk.Label(right, text=("只抓日线；同步完成后可在列表里检查质量。"
                              "抓取结果同时保存到 var/data/akshare/。"),
                 bg=surface, fg=theme.color("faint"), font=theme.font("small"),
                 anchor="w", justify="left", wraplength=theme.px(210)).pack(fill="x")

        self.progress = ttk.Progressbar(self.body, mode="indeterminate")
        self.progress.pack(fill="x", pady=(theme.px(10), theme.px(6)))

        self.sync_button = Button(self.footer, "开始同步", variant="primary",
                                  command=self._start)
        self.sync_button.pack(side="right")
        self.close_button = Button(self.footer, "关闭", variant="secondary",
                                   command=self.close)
        self.close_button.pack(side="right", padx=(0, theme.px(8)))
        self.install_button = Button(self.footer, "重试安装", variant="secondary",
                                     command=self._install)
        self._install_visible = False
        self.all_button = Button(self.footer, "同步全部 A 股", variant="secondary",
                                 command=self._sync_all)
        self.all_button.pack(side="left")

        self._fill_symbols()
        self.after(60, self._check)

    # ---------------------------------------------------------- 准备

    def _fill_symbols(self) -> None:
        rows: List[dict] = []
        try:
            rows = self.service.quotes()
        except Exception:  # noqa: BLE001 - 列表失败仍然可以手工看状态
            rows = []
        self._symbols = [row["symbol"] for row in rows]
        if not self._symbols:
            self._symbols = list(PRESELECT)
        self.listbox.delete(0, "end")
        labels: Dict[str, str] = {row["symbol"]: row.get("name") or ""
                                  for row in rows}
        for symbol in self._symbols:
            name = labels.get(symbol, "")
            self.listbox.insert("end", ("%s  %s" % (symbol, name)).strip())
        for index, symbol in enumerate(self._symbols):
            if symbol in PRESELECT:
                self.listbox.selection_set(index)

    def _selected(self) -> List[str]:
        picked = [self._symbols[i] for i in self.listbox.curselection()]
        return picked or list(PRESELECT)

    def _adjust(self) -> str:
        label = self.adjust_var.get()
        for text, value in ADJUST_CHOICES:
            if text == label:
                return value
        return "qfq"

    # ---------------------------------------------------------- 状态

    def _check(self) -> None:
        self.status_label.configure(text="正在检查 akshare……", fg=theme.color("muted"))
        self.update_idletasks()
        try:
            status = self.service.akshare_status()
        except Exception as exc:  # noqa: BLE001
            status = {"available": False, "message": str(exc)}
        if status.get("available"):
            self.status_label.configure(
                text="akshare 可用。选好标的和区间后点「开始同步」。",
                fg=theme.color("success"))
        else:
            self.sync_button.set_enabled(False)
            self.status_label.configure(
                text="未安装 akshare，正在自动安装（需要联网，约 2–5 分钟）……",
                fg=theme.color("warning"))
            self.after(80, self._install)

    # ---------------------------------------------------------- 自动安装

    def _stop_progress(self) -> None:
        self.progress.stop()
        self.progress.configure(mode="determinate", value=0)

    def _show_install(self) -> None:
        if not self._install_visible:
            self.install_button.pack(side="right", padx=(0, theme.px(8)))
            self._install_visible = True

    def _hide_install(self) -> None:
        if self._install_visible:
            self.install_button.pack_forget()
            self._install_visible = False

    def _install(self) -> None:
        if self._installing:
            return
        self._installing = True
        self._install_result = None
        self.sync_button.set_enabled(False)
        self._hide_install()
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self.status_label.configure(
            text="正在自动安装 akshare（约 1–3 分钟，需要联网）……",
            fg=theme.color("muted"))
        self._install_thread = threading.Thread(
            target=self._install_worker, daemon=True, name="akshare-install-modal")
        self._install_thread.start()
        self.after(300, self._poll_install)

    def _install_worker(self) -> None:
        try:
            self._install_result = self.service.ensure_akshare()
        except Exception as exc:  # noqa: BLE001
            self._install_result = {"ok": False, "message": str(exc)}

    def _poll_install(self) -> None:
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return
        thread = self._install_thread
        if thread is not None and thread.is_alive():
            self.after(300, self._poll_install)
            return
        self._installing = False
        self._stop_progress()
        result = self._install_result or {"ok": False, "message": "未知原因"}
        self._install_result = None
        if result.get("ok"):
            self.status_label.configure(
                text="akshare 已准备好。选好标的和区间后点「开始同步」。",
                fg=theme.color("success"))
            self.sync_button.set_enabled(True)
            self._hide_install()
        else:
            message = str(result.get("message") or "未知原因")
            self.status_label.configure(
                text=("自动安装未完成：%s\n可点「重试安装」再试；"
                      "离线环境可把 wheel 放到 vendor/akshare/ 后重试。" % message),
                fg=theme.color("danger"))
            self._show_install()
            self.sync_button.set_enabled(False)

    # ---------------------------------------------------------- 同步

    def _sync_all(self) -> None:
        """一次抓取全市场当日快照（约 5500 只，1 分钟左右）。"""
        if self._running:
            return
        self._running = True
        self.sync_button.set_enabled(False)
        self.all_button.set_enabled(False)
        self.close_button.set_enabled(False)
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self.status_label.configure(
            text="正在抓取全部 A 股当日快照（约 5500 只，1 分钟左右）……",
            fg=theme.color("muted"))
        self._result = None
        self._error = None

        def worker() -> None:
            try:
                self._result = self.service.sync_a_share_list()
            except Exception as exc:  # noqa: BLE001
                self._error = str(exc)

        self._thread = threading.Thread(target=worker, daemon=True,
                                        name="akshare-all")
        self._thread.start()
        self.after(250, self._poll_all)

    def _poll_all(self) -> None:
        if not self._running:
            return
        if self._thread is not None and self._thread.is_alive():
            self.after(250, self._poll_all)
            return
        self._running = False
        self._stop_progress()
        self.close_button.set_enabled(True)
        self.all_button.set_enabled(True)
        result = self._result or {}
        if self._error or not result.get("ok"):
            message = self._error or result.get("message") or "未知原因"
            self.status_label.configure(text="同步全部 A 股失败：%s" % message,
                                        fg=theme.color("danger"))
            self.sync_button.set_enabled(True)
            return
        self.status_label.configure(
            text="完成：已写入 %s 只 A 股当日快照。行情页现在可以看到全部 A 股；"
                 "需要历史 K 线时再按标的同步。" % "{:,}".format(result.get("symbols", 0)),
            fg=theme.color("success"))
        self.sync_button.set_enabled(True)
        if self.on_done is not None:
            try:
                self.on_done(result)
            except Exception:  # noqa: BLE001
                pass

    def _start(self) -> None:
        if self._running:
            return
        symbols = self._selected()
        start_text = self.start_entry.get().strip()
        end_text = self.end_entry.get().strip()
        try:
            start = date.fromisoformat(start_text)
            end = date.fromisoformat(end_text)
        except ValueError:
            self.status_label.configure(text="日期格式应为 YYYY-MM-DD。",
                                        fg=theme.color("danger"))
            return
        if start >= end:
            self.status_label.configure(text="开始日期必须早于结束日期。",
                                        fg=theme.color("danger"))
            return
        self._running = True
        self._result = None
        self._error = None
        self.sync_button.set_enabled(False)
        self.close_button.set_enabled(False)
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self.status_label.configure(
            text="正在同步 %d 个标的（%s ~ %s），请不要关闭窗口……"
                 % (len(symbols), start_text, end_text), fg=theme.color("muted"))

        adjust = self._adjust()

        def worker() -> None:
            try:
                self._result = self.service.sync_akshare(
                    symbols, start=start, end=end, adjust=adjust)
            except Exception as exc:  # noqa: BLE001 - 结果交回主线程展示
                self._error = str(exc)

        self._thread = threading.Thread(target=worker, daemon=True,
                                        name="akshare-sync")
        self._thread.start()
        self.after(250, self._poll)

    def _poll(self) -> None:
        if not self._running:
            return
        if self._thread is not None and self._thread.is_alive():
            self.after(250, self._poll)
            return
        self._running = False
        self._stop_progress()
        self.close_button.set_enabled(True)
        if self._error:
            self.status_label.configure(text="同步失败：%s" % self._error,
                                        fg=theme.color("danger"))
            self.sync_button.set_enabled(True)
            return
        summary = self._result or {}
        errors = summary.get("errors") or []
        text = "完成：%d 个标的，共 %s 根 K 线。" % (
            summary.get("symbols", 0), "{:,}".format(summary.get("bars", 0)))
        if errors:
            text += "\n失败 %d 个：%s" % (
                len(errors), "；".join("%s %s" % (e.get("symbol", ""),
                                                 str(e.get("message", ""))[:60])
                                       for e in errors[:3]))
        self.status_label.configure(
            text=text, fg=theme.color("success") if not errors else theme.color("warning"))
        self.sync_button.set_enabled(True)
        self.sync_button.set_text("再次同步")
        if self.on_done is not None and summary.get("symbols"):
            try:
                self.on_done(summary)
            except Exception:  # noqa: BLE001 - 回调失败不影响弹窗
                pass

    def close(self) -> None:
        if self._running or self._installing:
            return
        super().close()
