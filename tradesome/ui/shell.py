"""主窗口：顶栏、导航、页面容器、状态栏、事件泵。

界面不直接调用领域对象，只通过 app.services。
"""

from __future__ import annotations

import importlib
import queue
import threading
import time
import tkinter as tk
from datetime import datetime
from typing import Dict, Optional

from .. import __version__
from . import theme
from ..core.bus import ALL
from ..core.calendar import phase_label, session_phase
from ..core.events import (BACKTEST_FINISHED, BACKTEST_PROGRESS, DATA_GENERATED,
                           DATA_IMPORTED, LIVE_STARTED, LIVE_STOPPED, LIVE_TICK,
                           RISK_TRIGGERED)
from .widgets import Button, ConfirmModal, MessageModal, Sidebar, ToastManager
from .widgets.shapes import rounded_rect
from .widgets.tour import (StepsModal, TOUR_STEPS, TourOverlay,
                           WelcomeModal)

PAGE_SPECS = [
    ("dashboard", "概览", "◉", "dashboard", "DashboardPage", "工作区"),
    ("market", "行情", "▤", "market", "MarketPage", "工作区"),
    ("strategies", "策略库", "◇", "strategies", "StrategiesPage", "工作区"),
    ("lab", "实验室", "⊞", "lab", "LabPage", "工作区"),
    ("backtest", "回测", "↻", "backtest", "BacktestPage", "工作区"),
    ("live", "模拟盘", "▶", "live", "LivePage", "工作区"),
    ("live_trade", "实盘", "⇄", "live_trade", "LiveTradePage", "工作区"),
    ("risk", "风控", "⚑", "risk", "RiskPage", "系统"),
    ("data", "数据中心", "▦", "data_center", "DataPage", "系统"),
    ("settings", "设置", "⚙", "settings", "SettingsPage", "系统"),
    ("help", "帮助", "?", "help", "HelpPage", "系统"),
]
PAGE_TITLES = {key: title for key, title, _g, _m, _c, _group in PAGE_SPECS}


class Shell(tk.Tk):
    def __init__(self, container, welcome: bool = True) -> None:
        super().__init__()
        self.container = container
        self.c = container
        self._welcome = welcome
        self._pages: Dict[str, object] = {}
        self._current: Optional[str] = None
        self._queue: "queue.Queue" = queue.Queue()
        self._closing = False
        self._pump_job: Optional[str] = None
        self._clock_job: Optional[str] = None
        self._page_specs = {key: (module, cls) for key, _t, _g, module, cls, _grp
                            in PAGE_SPECS}
        theme.set_theme(container.config.appearance.theme,
                        container.config.appearance.font_scale)
        # 进程若已声明 DPI 感知，Tk 报告物理像素；把实际 DPI 交给主题，
        # px() 据此放大布局，字体则由 Tk 的 scaling 负责，避免二次放大。
        try:
            actual_dpi = self.winfo_fpixels("1i")
            theme.set_dpi(actual_dpi / 96.0)
            self.tk.call("tk", "scaling", actual_dpi / 72.0)
        except tk.TclError:
            theme.set_dpi(1.0)
        theme.apply(self)
        self.title("TradeSome · 量化交易研究台")
        self._apply_geometry()
        try:
            # 小屏（1366×768 一类）上固定窗口会被任务栏切掉底部，默认最大化更稳妥。
            self.state("zoomed")
        except tk.TclError:
            pass
        self._load_icon()
        self._build_chrome()
        self.toasts = ToastManager(self)
        self.tour = TourOverlay(self)
        self._subscribe()
        self._pump()
        self._clock_job = self.after(200, self._tick_clock)
        self.after(300, self._deferred_start)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._bind_shortcuts()

    def _apply_geometry(self) -> None:
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        width = min(theme.px(1360), max(theme.px(1180), screen_w - 120))
        height = min(theme.px(860), max(theme.px(650), screen_h - 140))
        self.minsize(min(theme.px(1180), width), min(theme.px(650), height))
        self.geometry("%dx%d+%d+%d" % (width, height,
                                       max(0, (screen_w - width) // 2),
                                       max(0, (screen_h - height) // 2 - 12)))

    def destroy(self) -> None:
        """退出时先取消定时器，避免 Tk 已销毁还在回调。"""
        self._closing = True
        for job in (self._pump_job, self._clock_job):
            if job:
                try:
                    self.after_cancel(job)
                except tk.TclError:
                    pass
        self._pump_job = None
        self._deps_result = None
        self._clock_job = None
        try:
            super().destroy()
        except tk.TclError:
            pass

    # ---------------------------------------------------------- 骨架

    def _load_icon(self) -> None:
        """有图标就设置窗口图标，没有也不影响启动。"""
        path = getattr(self.c.paths, "assets", None)
        if path is None:
            return
        icon = path / "app.ico"
        try:
            if icon.exists():
                self.iconbitmap(default=str(icon))
        except Exception:  # noqa: BLE001 - 图标只是锦上添花
            pass

    def _build_chrome(self) -> None:
        self._build_header()
        # 先占住底部状态栏，避免页面内容过高时把它挤出窗口。
        self._build_status()
        body = tk.Frame(self, bg=theme.color("bg"))
        body.pack(fill="both", expand=True)
        self.sidebar = Sidebar(
            body, [("工作区", [(key, glyph, title) for key, title, glyph, _m, _c, group
                              in PAGE_SPECS if group == "工作区"]),
                   ("系统", [(key, glyph, title) for key, title, glyph, _m, _c, group
                             in PAGE_SPECS if group == "系统"])],
            on_select=self.navigate)
        self.sidebar.pack(side="left", fill="y")
        self.content = tk.Frame(body, bg=theme.color("bg"))
        self.content.pack(side="left", fill="both", expand=True)

    def _build_header(self) -> None:
        header = tk.Frame(self, bg=theme.color("surface"), height=theme.px(52))
        header.pack(fill="x")
        header.pack_propagate(False)
        logo = tk.Canvas(header, width=theme.px(26), height=theme.px(26),
                         bg=theme.color("surface"), highlightthickness=0)
        logo.pack(side="left", padx=(theme.px(18), theme.px(9)))
        rounded_rect(logo, 0, 0, theme.px(26), theme.px(26), theme.px(7),
                     fill=theme.color("accent"), outline=theme.color("accent"))
        logo.create_line(theme.px(13), theme.px(6), theme.px(13), theme.px(20),
                         fill="white", width=2)
        logo.create_rectangle(theme.px(8), theme.px(10), theme.px(11),
                              theme.px(17), fill="white", outline="")
        logo.create_rectangle(theme.px(15), theme.px(13), theme.px(18),
                              theme.px(19), fill="white", outline="")
        brand = tk.Frame(header, bg=theme.color("surface"))
        brand.pack(side="left")
        tk.Label(brand, text="TradeSome", bg=theme.color("surface"),
                 fg=theme.color("text"), font=theme.font("h2"),
                 anchor="w").pack(anchor="w")
        tk.Label(brand, text="本地量化交易研究台", bg=theme.color("surface"),
                 fg=theme.color("faint"), font=theme.font("small"),
                 anchor="w").pack(anchor="w")
        tk.Label(header, text="·", bg=theme.color("surface"),
                 fg=theme.color("faint"), font=theme.font("body")).pack(
            side="left", padx=(theme.px(12), theme.px(8)))
        self.page_title = tk.Label(header, text="概览", bg=theme.color("surface"),
                                   fg=theme.color("muted"), font=theme.font("body"))
        self.page_title.pack(side="left")
        self.phase_label = tk.Label(header, text="", bg=theme.color("surface_alt"),
                                    fg=theme.color("muted"), font=theme.font("small"),
                                    padx=theme.px(8), pady=theme.px(2))
        self.phase_label.pack(side="right", padx=theme.px(14))
        self.clock_label = tk.Label(header, text="", bg=theme.color("surface"),
                                    fg=theme.color("faint"), font=theme.font("mono"))
        self.clock_label.pack(side="right", padx=(0, theme.px(8)))
        self._theme_button = Button(header, self._theme_label(),
                                    command=self.toggle_theme, variant="ghost", size="sm")
        self._theme_button.pack(side="right", padx=theme.px(4))
        Button(header, "帮助", command=lambda: self.navigate("help"),
               variant="ghost", size="sm").pack(side="right")
        tk.Frame(self, bg=theme.color("border"), height=1).pack(fill="x")

    def _theme_label(self) -> str:
        names = {"macos": "外观：macOS", "dark": "外观：深色", "light": "外观：浅色"}
        return names.get(theme.theme_name(), "外观")

    def _build_status(self) -> None:
        tk.Frame(self, bg=theme.color("border"), height=1).pack(fill="x", side="bottom")
        bar = tk.Frame(self, bg=theme.color("surface"), height=theme.px(26))
        bar.pack(fill="x", side="bottom")
        bar.pack_propagate(False)
        self.status_left = tk.Label(bar, text="正在准备数据……", bg=theme.color("surface"),
                                    fg=theme.color("faint"), font=theme.font("small"),
                                    anchor="w")
        self.status_left.pack(side="left", padx=theme.px(16))
        self.status_right = tk.Label(bar, text="v%s · 仅用于研究与模拟" % __version__,
                                     bg=theme.color("surface"), fg=theme.color("faint"),
                                     font=theme.font("small"))
        self.status_right.pack(side="right", padx=theme.px(16))
        self.status_mid = tk.Label(bar, text="", bg=theme.color("surface"),
                                   fg=theme.color("faint"), font=theme.font("small"))
        self.status_mid.pack(side="right", padx=theme.px(12))

    # ---------------------------------------------------------- 页面

    def navigate(self, key: str) -> None:
        if key not in PAGE_TITLES or key == self._current:
            if key == self._current:
                return
        page = self._pages.get(key)
        if page is None:
            module_name, class_name = self._page_specs[key]
            try:
                module = importlib.import_module("." + module_name, __package__ + ".pages")
                page_class = getattr(module, class_name)
                page = page_class(self, self.container)
            except Exception as exc:  # noqa: BLE001 - 单页失败不应拖垮整个应用
                self._show_page_error(key, exc)
                return
            self._pages[key] = page
        if self._current and self._current in self._pages:
            try:
                self._pages[self._current].on_hide()
                self._pages[self._current].pack_forget()
            except Exception:  # noqa: BLE001
                pass
        self._current = key
        page.pack(fill="both", expand=True)
        try:
            page.on_show()
        except Exception as exc:  # noqa: BLE001
            self.show_error("页面加载失败", str(exc), "可尝试重启程序，或查看日志文件。")
        self.sidebar.set_active(key)
        self.page_title.configure(text=PAGE_TITLES.get(key, ""))
        self.refresh_status()

    def _show_page_error(self, key: str, exc: Exception) -> None:
        holder = tk.Frame(self.content, bg=theme.color("bg"))
        holder.pack(fill="both", expand=True)
        tk.Label(holder, text="页面「%s」加载失败" % PAGE_TITLES.get(key, key),
                 bg=theme.color("bg"), fg=theme.color("text"),
                 font=theme.font("h2")).pack(pady=(120, 8))
        tk.Label(holder, text=str(exc), bg=theme.color("bg"),
                 fg=theme.color("muted"), font=theme.font("caption"),
                 wraplength=theme.px(600)).pack()
        Button(holder, "返回概览", command=lambda: self.navigate("dashboard"),
               variant="primary").pack(pady=16)

    def refresh_page(self) -> None:
        page = self._pages.get(self._current or "")
        if page is not None:
            try:
                page.refresh()
            except Exception:  # noqa: BLE001
                pass

    # ---------------------------------------------------------- 事件

    def _subscribe(self) -> None:
        self.container.bus.subscribe(ALL, self._enqueue)

    def _enqueue(self, event) -> None:
        self._queue.put(event)

    def _pump(self) -> None:
        if self._closing:
            return
        for _ in range(100):
            try:
                event = self._queue.get_nowait()
            except queue.Empty:
                break
            self._dispatch(event)
        try:
            self._pump_job = self.after(80, self._pump)
        except tk.TclError:
            pass

    def _dispatch(self, event) -> None:
        topic, payload = event.topic, dict(event.payload or {})
        page = self._pages.get(self._current or "")
        if page is not None:
            try:
                page.on_event(topic, payload)
            except Exception:  # noqa: BLE001
                pass
        if topic == BACKTEST_FINISHED:
            metrics = payload.get("metrics") or {}
            self.toast("回测完成：收益 %s" % _pct(metrics.get("total_return")),
                       "success")
            self.refresh_status()
        elif topic == BACKTEST_PROGRESS:
            self.refresh_status()
        elif topic == LIVE_STARTED:
            self.toast("模拟盘已启动", "success")
        elif topic == LIVE_STOPPED:
            self.toast("模拟盘已停止", "info")
        elif topic == DATA_GENERATED:
            self.status_left.configure(text="数据已生成：%s 根 K 线" % payload.get("bars", 0))
        elif topic == DATA_IMPORTED:
            self.status_left.configure(text="已导入 %s" % payload.get("symbol", ""))
        elif topic == RISK_TRIGGERED:
            self.toast(str(payload.get("message") or "风控触发"), "warning",
                       title="风控提醒")

    # ---------------------------------------------------------- 顶栏/状态

    def _tick_clock(self) -> None:
        if self._closing:
            return
        now = datetime.now()
        self.clock_label.configure(text=now.strftime("%Y-%m-%d %H:%M:%S"))
        phase = session_phase(now)
        self.phase_label.configure(text="● " + phase_label(phase),
                                   fg=theme.color("success" if phase in
                                                  ("morning", "afternoon") else "muted"))
        try:
            self._clock_job = self.after(1000, self._tick_clock)
        except tk.TclError:
            pass

    def refresh_status(self) -> None:
        try:
            summary = self.c.services.market.summary()
            self.status_left.configure(
                text="数据：%d 个标的 · %s 根 K 线" % (summary["symbols"],
                                                      "{:,}".format(summary["bars"])))
            backtest = self.c.services.backtest
            live = self.c.services.live
            parts = ["策略 %d" % len(self.c.strategies.keys())]
            if backtest.is_running():
                parts.append("回测 %d%%" % int(backtest.progress))
            if live.is_running():
                parts.append("模拟盘运行中")
            self.status_mid.configure(text=" · ".join(parts))
        except Exception:  # noqa: BLE001
            pass

    def _deferred_start(self) -> None:
        start = self.c.config.appearance.start_page
        if start not in PAGE_TITLES:
            start = "dashboard"
        self.navigate(start)
        self.refresh_status()
        self._startup_tip()
        self._ensure_data_deps()
        if (self._welcome and self.c.config.appearance.show_tips
                and not self.guide_done()):
            self.after(450, self.show_welcome)

    # ---------------------------------------------------------- 自动依赖

    def _ensure_data_deps(self) -> None:
        """首次缺 akshare 时自动安装；失败不阻塞界面，6 小时内不反复重试。"""
        if getattr(self.c, "memory_db", False):
            return
        if not self.c.config.data.auto_install_akshare:
            return
        market = self.c.services.market
        if market.akshare_installed():
            return
        try:
            last = float(self.c.repos.kv.get("akshare.auto_install_at", 0) or 0)
        except Exception:  # noqa: BLE001
            last = 0.0
        if time.time() - last < 6 * 3600:
            return
        try:
            self.c.repos.kv.set("akshare.auto_install_at", time.time())
        except Exception:  # noqa: BLE001
            pass
        self.toast("正在自动安装行情组件 akshare（首次需要联网，约 2–5 分钟，慢时更久）…",
                   "info", title="准备真实行情")
        threading.Thread(target=self._deps_worker, daemon=True,
                         name="akshare-install").start()
        self.after(700, self._poll_deps)

    def _deps_worker(self) -> None:
        try:
            self._deps_result = self.c.services.market.ensure_akshare()
        except Exception as exc:  # noqa: BLE001
            self._deps_result = {"ok": False, "message": str(exc)}

    def _poll_deps(self) -> None:
        if self._closing:
            return
        if self._deps_result is None:
            self.after(700, self._poll_deps)
            return
        result, self._deps_result = self._deps_result, None
        if result.get("ok"):
            self.toast("akshare 已准备好，可以去数据中心同步真实行情。", "success")
        else:
            message = str(result.get("message") or "未知原因")
            self.toast("akshare 自动安装未完成：%s 可在数据中心重试。" % message[:80],
                       "warning")

    # ---------------------------------------------------------- 新手引导

    def guide_done(self) -> bool:
        try:
            return bool(self.c.repos.kv.get("onboarding.done", False))
        except Exception:  # noqa: BLE001
            return False

    def mark_guide_done(self) -> None:
        try:
            self.c.repos.kv.set("onboarding.done", True)
        except Exception:  # noqa: BLE001
            pass

    def show_welcome(self) -> None:
        WelcomeModal(self, on_start=lambda: self.start_guide(0),
                     on_skip=self.mark_guide_done).show()

    def start_guide(self, index: Optional[int] = None) -> None:
        if index is None:
            keys = [step["page"] for step in TOUR_STEPS]
            index = keys.index(self._current) if self._current in keys else 0
        self.mark_guide_done()
        self.tour.start(index, on_done=self._guide_finished)

    def _guide_finished(self) -> None:
        self.mark_guide_done()
        self.toast("引导结束。想重看时，帮助页 → 新手引导 → 重新观看。", "success")

    def show_help_steps(self, page) -> None:
        steps = list(getattr(page, "help_steps", []) or [])
        if not steps:
            self.toast("本页暂时没有分步说明，可以打开帮助页查看完整教程。", "info")
            return
        keys = [step["page"] for step in TOUR_STEPS]
        key = getattr(page, "key", "")
        on_start = (lambda: self.start_guide(keys.index(key))) if key in keys else None
        StepsModal(self, "%s · 本页怎么用" % (page.title or "本页"), steps,
                   on_start=on_start).show()

    def _startup_tip(self) -> None:
        if not self.c.config.appearance.show_tips:
            return
        if self.c.services.market.summary()["bars"] == 0:
            self.toast("还没有行情数据，去「数据中心」同步真实行情或导入 CSV。", "warning")

    # ---------------------------------------------------------- 外观/弹窗

    def toggle_theme(self) -> None:
        order = ("macos", "dark", "light")
        try:
            index = order.index(theme.theme_name())
        except ValueError:
            index = -1
        self.set_appearance(theme_name=order[(index + 1) % len(order)])

    def set_appearance(self, theme_name: Optional[str] = None,
                       font_scale: Optional[float] = None) -> None:
        appearance = self.c.config.appearance
        if theme_name:
            appearance.theme = theme_name
        if font_scale:
            appearance.font_scale = float(font_scale)
        theme.set_theme(appearance.theme, appearance.font_scale)
        try:
            self.c.save_config()
        except Exception:  # noqa: BLE001
            pass
        self._rebuild_ui()

    def _rebuild_ui(self) -> None:
        current = self._current
        for page in self._pages.values():
            try:
                page.destroy()
            except Exception:  # noqa: BLE001
                pass
        self._pages.clear()
        self.toasts.clear()
        for child in list(self.winfo_children()):
            child.destroy()
        theme.apply(self)
        self._apply_geometry()
        self._build_chrome()
        self.toasts = ToastManager(self)
        self.tour = TourOverlay(self)
        self._current = None
        self.navigate(current or "dashboard")

    def toast(self, message: str, kind: str = "info", title: str = "") -> None:
        self.toasts.show(message, kind=kind, title=title)

    def show_error(self, title: str, message: str, hint: str = "") -> None:
        MessageModal(self, title, message, hint=hint, tone="text").show()

    def confirm(self, title: str, message: str, on_confirm,
                confirm_text: str = "确认", danger: bool = False,
                require_phrase: str = "") -> None:
        ConfirmModal(self, title, message, on_confirm, confirm_text=confirm_text,
                     danger=danger, require_phrase=require_phrase).show()

    # ---------------------------------------------------------- 快捷键/退出

    def _bind_shortcuts(self) -> None:
        for index, (key, *_rest) in enumerate(PAGE_SPECS[:9], start=1):
            self.bind_all("<Control-Key-%d>" % index,
                          lambda e, k=key: self.navigate(k))
        self.bind_all("<F1>", lambda e: self.navigate("help"))
        self.bind_all("<F5>", lambda e: self.refresh_page())
        self.bind_all("<Control-b>", lambda e: self.navigate("backtest"))
        self.bind_all("<Escape>", lambda e: self.toasts.clear())

    def close(self) -> None:
        live = self.c.services.live
        if live.is_running():
            self.confirm("模拟盘还在运行", "关闭程序会停止当前模拟盘会话，确定退出吗？",
                         self._really_close, confirm_text="停止并退出", danger=True)
            return
        trade = self.c.services.live_trade
        if trade.running:
            self.confirm("实盘会话还在运行",
                         "关闭程序会先撤销未成交委托并停止实盘会话，确定退出吗？",
                         self._really_close, confirm_text="停止并退出", danger=True)
            return
        self._really_close()

    def _really_close(self) -> None:
        trade = self.c.services.live_trade
        if trade.running:
            try:
                trade.emergency_stop("关闭软件")
            except Exception:  # noqa: BLE001 - 退出流程不因柜台异常中断
                pass
        try:
            from ..app.bootstrap import shutdown
            shutdown(self.container)
        except Exception:  # noqa: BLE001
            pass
        try:
            self.destroy()
        except tk.TclError:
            pass


def _pct(value) -> str:
    if value is None:
        return "—"
    return "%+.2f%%" % (float(value) * 100.0)


def run(container) -> None:
    shell = Shell(container)
    shell.mainloop()
