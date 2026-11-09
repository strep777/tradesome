"""策略库：内置与用户策略的说明、参数与入口。"""

from __future__ import annotations

import tkinter as tk

from .. import theme
from ..widgets import Button, Card, DataTable
from ..widgets.strategy_wizard import StrategyWizard
from .base import BasePage

TYPE_LABELS = {"int": "整数", "float": "小数", "bool": "开关", "str": "文本",
               "choice": "选项"}


class StrategiesPage(BasePage):
    title = "策略库"
    subtitle = "每个策略都声明了自己的参数与适用场景；参数由界面自动生成，新增策略无需改 UI。"

    help_steps = [
        '点「新建策略」开始，全程不需要写文件。',
        '「模板」适合快速改参数；「自由组合」可以从零搭入场与退出条件。',
        '保存后立即出现在回测、模拟盘和实盘的下拉框里；选中后可编辑或删除。',
    ]

    def tour_targets(self) -> dict:
        return {"new": getattr(self, "_new_button", None)}

    def build(self) -> None:
        self._key = None
        header = self.page_header()
        bar = tk.Frame(header, bg=theme.color("bg"))
        bar.pack(side="right")
        self.search_var = tk.StringVar()
        box = tk.Frame(bar, bg=theme.color("surface_alt"), highlightthickness=1,
                       highlightbackground=theme.color("border"))
        box.pack(side="left", padx=(0, theme.px(8)))
        tk.Label(box, text="搜索", bg=theme.color("surface_alt"),
                 fg=theme.color("faint"), font=theme.font("caption")).pack(
            side="left", padx=(theme.px(8), 0))
        entry = tk.Entry(box, textvariable=self.search_var, bd=0, relief="flat",
                         bg=theme.color("surface_alt"), fg=theme.color("text"),
                         insertbackground=theme.color("text"), font=theme.font("body"),
                         width=16)
        entry.pack(padx=(theme.px(5), theme.px(8)), pady=theme.px(5))
        self.search_var.trace_add("write", lambda *_: self._refresh_list())
        self._new_button = Button(bar, "新建策略", command=self._new,
                                  variant="primary", size="sm")
        self._new_button.pack(side="left")

        body = tk.Frame(self, bg=theme.color("bg"))
        body.pack(fill="both", expand=True, padx=theme.px(24), pady=(0, theme.px(16)))

        left = Card(body, padding=12)
        left.configure(width=theme.px(520))
        left.pack(side="left", fill="both")
        left.pack_propagate(False)
        self.table = DataTable(left.body, [
            ("name", "策略", 140), ("category", "类别", 74),
            ("risk", "风险", 46, "center"), ("source", "来源", 56, "center"),
            ("params", "参数", 42, "center"),
        ], height=18, on_select=self._on_select)
        self.table.pack(fill="both", expand=True)

        right = Card(body)
        right.pack(side="left", fill="both", expand=True, padx=(theme.px(12), 0))
        self.detail = tk.Frame(right.body, bg=theme.color("surface"))
        self.detail.pack(fill="both", expand=True)
        self._show_placeholder()

    # ---------------------------------------------------------- 列表

    def refresh(self) -> None:
        self._refresh_list()
        if self._key:
            self._render(self._key)

    def _metas(self) -> list:
        keyword = (self.search_var.get() or "").strip().lower()
        metas = self.c.services.strategy.metas()
        if keyword:
            metas = [m for m in metas if keyword in (m.get("name") or "").lower()
                     or keyword in (m.get("key") or "").lower()
                     or keyword in (m.get("category") or "")]
        return metas

    def _refresh_list(self) -> None:
        rows = []
        for meta in self._metas():
            rows.append({
                "key": meta["key"], "name": meta["name"],
                "category": meta.get("category", ""),
                "risk": meta.get("risk_level", ""),
                "source": "自定义" if meta.get("is_profile") else
                          ("自定义" if meta.get("is_user") else "内置"),
                "params": meta.get("param_count", 0),
            })
        self.table.set_rows(rows)
        if self._key is None and rows:
            first = self.table.select_first()
            if first:
                self._key = first["key"]
                self._render(self._key)

    def _on_select(self, row) -> None:
        if row and row.get("key"):
            self._key = row["key"]
            self._render(self._key)

    # ---------------------------------------------------------- 详情

    def _clear_detail(self) -> None:
        for child in self.detail.winfo_children():
            child.destroy()

    def _show_placeholder(self) -> None:
        self._clear_detail()
        tk.Label(self.detail, text="← 选择左侧策略查看说明",
                 bg=theme.color("surface"), fg=theme.color("faint"),
                 font=theme.font("body")).pack(pady=80)

    def _render(self, key: str) -> None:
        try:
            meta = self.c.services.strategy.meta(key)
            params = self.c.services.strategy.params(key)
        except Exception as exc:  # noqa: BLE001
            self._clear_detail()
            self.error("读取策略失败", str(exc))
            return
        self._clear_detail()
        top = tk.Frame(self.detail, bg=theme.color("surface"))
        top.pack(fill="x")
        tk.Label(top, text=meta["name"], bg=theme.color("surface"),
                 fg=theme.color("text"), font=theme.font("h1"),
                 anchor="w").pack(side="left")
        tk.Label(top, text="%s · %s风险 · v%s" % (meta.get("category", ""),
                                                  meta.get("risk_level", ""),
                                                  meta.get("version", "")),
                 bg=theme.color("surface"), fg=theme.color("muted"),
                 font=theme.font("small")).pack(side="left", padx=theme.px(10))
        tk.Label(top, text=key, bg=theme.color("surface"), fg=theme.color("faint"),
                 font=theme.font("mono")).pack(side="right")

        if meta.get("description"):
            tk.Label(self.detail, text=meta["description"], bg=theme.color("surface"),
                     fg=theme.color("muted"), font=theme.font("body"), justify="left",
                     anchor="w", wraplength=theme.px(640)).pack(fill="x", pady=(6, 0))

        for title, field in (("原理", "principle"), ("适用行情", "works_when"),
                             ("失效场景", "fails_when")):
            text = meta.get(field)
            if not text:
                continue
            tk.Label(self.detail, text=title, bg=theme.color("surface"),
                     fg=theme.color("accent"), font=theme.font("caption"),
                     anchor="w").pack(fill="x", pady=(theme.px(12), 2))
            tk.Label(self.detail, text=text, bg=theme.color("surface"),
                     fg=theme.color("muted"), font=theme.font("caption"),
                     justify="left", anchor="w",
                     wraplength=theme.px(640)).pack(fill="x")

        if meta.get("tags"):
            tk.Label(self.detail, text="标签：" + " · ".join(meta["tags"]),
                     bg=theme.color("surface"), fg=theme.color("faint"),
                     font=theme.font("small"), anchor="w").pack(fill="x",
                                                              pady=(theme.px(10), 0))

        tk.Label(self.detail, text="参数", bg=theme.color("surface"),
                 fg=theme.color("accent"), font=theme.font("caption"),
                 anchor="w").pack(fill="x", pady=(theme.px(14), 4))
        table = DataTable(self.detail, [
            ("label", "名称", 150, "w", False), ("type", "类型", 70, "w", False),
            ("default", "默认", 110, "center", False), ("range", "范围", 180, "w", True),
        ], height=min(4, max(3, len(params))), bg=theme.color("surface"))
        table.pack(fill="x")
        table.set_rows([{
            "label": p.get("label") or p["name"],
            "type": TYPE_LABELS.get(p.get("type"), p.get("type")),
            "default": p.get("default"),
            "range": "%s ~ %s" % (p.get("min", "-"), p.get("max", "-"))
            if p.get("min") is not None or p.get("max") is not None else
            "、".join(map(str, p.get("choices") or [])) or "—",
        } for p in params])

        actions = tk.Frame(self.detail, bg=theme.color("surface"))
        actions.pack(fill="x", pady=(theme.px(8), 0))
        Button(actions, "用此策略回测", variant="primary", size="sm",
               command=lambda: self._backtest(key)).pack(side="left")
        Button(actions, "在实验室调参", variant="secondary", size="sm",
               command=lambda: self._lab(key)).pack(side="left", padx=(6, 0))
        if meta.get("is_profile"):
            Button(actions, "编辑", variant="secondary", size="sm",
                   command=lambda: self._edit(meta)).pack(side="left", padx=(6, 0))
        if meta.get("is_profile") or meta.get("is_user"):
            Button(actions, "删除", variant="danger", size="sm",
                   command=lambda: self._delete(key)).pack(side="right")

    # ---------------------------------------------------------- 动作

    def _new(self) -> None:
        def saved(spec: dict) -> None:
            self.toast("已创建策略「%s」" % spec.get("name", ""), "success")
            self._key = spec.get("key")
            self.refresh()

        StrategyWizard(self.shell, self.c.services.strategy, saved).show()

    def _edit(self, meta: dict) -> None:
        def saved(spec: dict) -> None:
            self.toast("已更新策略「%s」" % spec.get("name", ""), "success")
            self._key = spec.get("key")
            self.refresh()

        StrategyWizard(self.shell, self.c.services.strategy, saved,
                       meta=meta).show()

    def _backtest(self, key: str) -> None:
        self.shell.navigate("backtest")
        page = getattr(self.shell, "_pages", {}).get("backtest")
        if page is not None and hasattr(page, "preset_strategy"):
            page.preset_strategy(key)

    def _lab(self, key: str) -> None:
        self.shell.navigate("lab")
        page = getattr(self.shell, "_pages", {}).get("lab")
        if page is not None and hasattr(page, "load_strategy"):
            page.load_strategy(key)

    def _delete(self, key: str) -> None:
        meta = self.c.services.strategy.meta(key)
        if meta.get("is_profile"):
            message = "将删除这条策略档案，回测与实盘的历史记录不受影响。确定删除「%s」吗？" % meta["name"]
        else:
            message = "将删除 var/strategies 下的策略文件，历史回测记录不受影响。确定删除「%s」吗？" % key
        self.shell.confirm(
            "删除自定义策略", message,
            lambda: self._do_delete(key), confirm_text="删除", danger=True)

    def _do_delete(self, key: str) -> None:
        try:
            meta = self.c.services.strategy.meta(key)
            if meta.get("is_profile"):
                self.c.services.strategy.delete_profile(meta["profile_id"])
            else:
                self.c.services.strategy.delete_user(key)
            self.toast("已删除策略 %s" % key, "success")
        except Exception as exc:  # noqa: BLE001
            self.error("删除失败", str(exc))
        self._key = None
        self.refresh()

