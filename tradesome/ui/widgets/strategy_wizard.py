"""策略工坊向导：从模板开始，或从零自由组合，保存即可用——不写文件。"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any, Callable, Dict, List, Optional

from .. import theme
from ..widgets import LabeledEntry, ParamForm, ScrollableFrame
from ..widgets.button import Button
from ..widgets.modal import Modal
from ...strategy.profile import OVERLAY_SPECS
from ...strategy.rules import (BY_TYPE, EXIT_DEFAULTS, EXIT_SPECS, RULE_LABELS,
                               RULE_TYPES, default_rules, describe_partial)


class _RuleRow(tk.Frame):
    """一条入场条件：类型 + 参数 + 删除。"""

    def __init__(self, parent, rule: Dict[str, Any],
                 on_change: Callable[[], None], on_remove: Callable[["_RuleRow"], None]):
        super().__init__(parent, bg=theme.color("surface"))
        self._on_change = on_change
        self._fields: Dict[str, Any] = {}
        self._spec = BY_TYPE.get(str(rule.get("type") or "")) or RULE_TYPES[0]

        self.type_var = tk.StringVar(value=self._spec.label)
        combo = ttk.Combobox(self, textvariable=self.type_var, state="readonly",
                             values=[item.label for item in RULE_TYPES],
                             width=13, font=theme.font("body"))
        combo.pack(side="left")
        combo.bind("<<ComboboxSelected>>", lambda e: self._on_type())

        self._fields_host = tk.Frame(self, bg=theme.color("surface"))
        self._fields_host.pack(side="left", padx=(theme.px(8), 0))
        self._build_fields(self._spec, rule)

        tk.Label(self, text="✕", bg=theme.color("surface"),
                 fg=theme.color("muted"), font=theme.font("caption"),
                 cursor="hand2").pack(side="right", padx=(theme.px(6), 0))
        self._remove_label = self.winfo_children()[-1]
        self._remove_label.bind("<Button-1>", lambda e: on_remove(self))

    def _build_fields(self, spec, values: Dict[str, Any]) -> None:
        for child in self._fields_host.winfo_children():
            child.destroy()
        self._fields.clear()
        for field in spec.fields:
            holder = tk.Frame(self._fields_host, bg=theme.color("surface"))
            holder.pack(side="left", padx=(0, theme.px(8)))
            tk.Label(holder, text=field.label, bg=theme.color("surface"),
                     fg=theme.color("muted"), font=theme.font("small"),
                     anchor="w").pack(anchor="w")
            var = tk.StringVar(value=str(values.get(field.name, field.default)))
            entry = tk.Entry(holder, textvariable=var, width=6,
                             bg=theme.color("surface_alt"), fg=theme.color("text"),
                             insertbackground=theme.color("text"), relief="flat",
                             font=theme.font("body"), justify="center")
            entry.pack(ipady=theme.px(3))
            var.trace_add("write", lambda *_: self._on_change())
            self._fields[field.name] = var

    def _on_type(self) -> None:
        label = self.type_var.get()
        spec = next((item for item in RULE_TYPES if item.label == label), RULE_TYPES[0])
        self._spec = spec
        defaults = {field.name: field.default for field in spec.fields}
        self._build_fields(spec, defaults)
        self._on_change()

    def values(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {"type": self._spec.key}
        for name, var in self._fields.items():
            data[name] = var.get()
        return data


class StrategyWizard(Modal):
    """新建/编辑一条策略档案：模板参数或自由组合。"""

    def __init__(self, parent, service, on_saved: Callable[[dict], None],
                 meta: Optional[dict] = None) -> None:
        title = "编辑策略" if meta else "新建策略"
        super().__init__(parent, title, width=780, height=660)
        self.service = service
        self.on_saved = on_saved
        self.meta = meta or {}
        self._templates = service.template_choices()
        self._by_label = {self._label(t): t["key"] for t in self._templates}
        self._key = self.meta.get("template") or (
            self._templates[0]["key"] if self._templates else "")
        self._mode = "rules" if self.meta.get("kind") == "rules" else "template"
        self._rules = self._initial_rules()
        self._form = None
        self._rules_panel = None
        self._rule_rows: List[_RuleRow] = []
        self._error = None
        self._build()
        self._apply_mode()

    # ---------------------------------------------------------- 组装

    def _build(self) -> None:
        top = tk.Frame(self.body, bg=theme.color("surface"))
        top.pack(fill="x")
        name_value = self.meta.get("name") or (
            "我的组合策略" if self._mode == "rules" else "我的策略")
        self.name_entry = LabeledEntry(
            top, "策略名称", name_value,
            hint="保存后出现在策略库、回测和实盘的下拉框里", width=24,
            bg=theme.color("surface"))
        self.name_entry.pack(side="left", fill="x", expand=True)

        self.template_box = tk.Frame(top, bg=theme.color("surface"))
        self.template_box.pack(side="left", padx=(theme.px(14), 0))
        tk.Label(self.template_box, text="策略模板", bg=theme.color("surface"),
                 fg=theme.color("muted"), font=theme.font("caption"),
                 anchor="w").pack(anchor="w")
        self.template_var = tk.StringVar(value=self._label_of(self._key))
        combo = ttk.Combobox(self.template_box, textvariable=self.template_var,
                             state="readonly",
                             values=[self._label(t) for t in self._templates],
                             width=22, font=theme.font("body"))
        combo.pack(pady=(theme.px(4), 0))
        combo.bind("<<ComboboxSelected>>", lambda e: self._on_template())

        mode_bar = tk.Frame(self.body, bg=theme.color("surface"))
        mode_bar.pack(fill="x", pady=(theme.px(10), 0))
        self._mode_buttons: Dict[str, Button] = {}
        for key, text in (("template", "从模板开始"), ("rules", "自由组合")):
            button = Button(mode_bar, text, size="sm",
                            variant="primary" if key == self._mode else "secondary",
                            command=lambda k=key: self._set_mode(k))
            button.pack(side="left", padx=(0, theme.px(6)))
            self._mode_buttons[key] = button
        tk.Label(mode_bar, text="模板 = 改参数；自由组合 = 从零搭出入场与退出条件",
                 bg=theme.color("surface"), fg=theme.color("faint"),
                 font=theme.font("small")).pack(side="left", padx=(theme.px(8), 0))

        self.template_hint = tk.Label(
            self.body, text="", bg=theme.color("surface"),
            fg=theme.color("muted"), font=theme.font("caption"),
            anchor="w", justify="left", wraplength=theme.px(700))
        self.template_hint.pack(fill="x", pady=(theme.px(6), theme.px(4)))

        line = tk.Frame(self.body, bg=theme.color("border"), height=theme.px(1))
        line.pack(fill="x", pady=(theme.px(4), theme.px(10)))

        self.form_host = ScrollableFrame(self.body, bg=theme.color("surface"))
        self.form_host.pack(fill="both", expand=True)

        self._error = tk.Label(self.body, text="", bg=theme.color("surface"),
                               fg=theme.color("danger"), font=theme.font("caption"),
                               anchor="w", justify="left", wraplength=theme.px(700))
        self._error.pack(fill="x", pady=(theme.px(4), 0))

        Button(self.footer, "保存并可用", command=self._save,
               variant="primary").pack(side="right")
        Button(self.footer, "取消", command=self.close,
               variant="secondary").pack(side="right", padx=(0, theme.px(8)))
        tk.Label(self.footer, text="不需要写代码或文件；策略会存进本地数据库。",
                 bg=theme.color("surface"), fg=theme.color("faint"),
                 font=theme.font("small"), anchor="w").pack(side="left")

    def _label(self, template: dict) -> str:
        return "%s · %s" % (template.get("name", ""), template.get("category", ""))

    def _label_of(self, key: str) -> str:
        for template in self._templates:
            if template["key"] == key:
                return self._label(template)
        return ""

    # ---------------------------------------------------------- 模式

    def _initial_rules(self) -> Dict[str, Any]:
        if self.meta.get("kind") == "rules" and isinstance(self.meta.get("rules"), dict):
            merged = default_rules()
            saved = self.meta["rules"]
            merged.update({k: saved[k] for k in ("match", "entry", "weight") if k in saved})
            if isinstance(saved.get("exit"), dict):
                merged["exit"].update(saved["exit"])
            return merged
        return default_rules()

    def _set_mode(self, mode: str) -> None:
        if mode not in ("template", "rules"):
            return
        self._mode = mode
        self._apply_mode()

    def _apply_mode(self) -> None:
        for key, button in self._mode_buttons.items():
            button.set_variant("primary" if key == self._mode else "secondary")
        self._clear_form()
        if self._mode == "rules":
            self.template_box.pack_forget()
            self.template_hint.configure(text=(
                "从零组合：选 1–4 个入场条件，再设退出规则。系统把条件翻成一句话，"
                "保存后能直接回测、上模拟盘和实盘；条件本身可在策略库 → 编辑里修改。"))
            self._build_rules()
        else:
            if not self.template_box.winfo_ismapped():
                self.template_box.pack(side="left", padx=(theme.px(14), 0))
            self._update_hint()
            self._rebuild_form()

    def _clear_form(self) -> None:
        for widget in (self._form, self._rules_panel):
            if widget is not None:
                try:
                    widget.destroy()
                except tk.TclError:
                    pass
        self._form = None
        self._rules_panel = None
        self._rule_rows = []

    # ---------------------------------------------------------- 模板模式

    def _update_hint(self) -> None:
        meta = next((t for t in self._templates if t["key"] == self._key), None)
        desc = (meta.get("description", "") if meta else "").strip()
        if desc and desc[-1] not in "。！？.!?":
            desc += "。"
        extra = "可选叠加层：风控止损、风控止盈、趋势过滤，留 0 即关闭。"
        self.template_hint.configure(text=(desc + extra) if desc else extra)

    def _rebuild_form(self) -> None:
        if self._form is not None:
            self._form.destroy()
        base_specs = self.service.params(self._key) if self._key else []
        specs = list(base_specs) + [spec.to_dict() for spec in OVERLAY_SPECS]
        values = self._initial_values()
        self._form = ParamForm(self.form_host.body, specs, values,
                               bg=theme.color("surface"), columns=2)
        self._form.pack(fill="x", padx=(0, theme.px(4)))

    def _initial_values(self) -> Dict[str, object]:
        if self.meta.get("is_profile") and self.meta.get("template") == self._key:
            values = dict(self.meta.get("parameters") or {})
            values.update(self.meta.get("overlays") or {})
            return values
        return self.service.defaults(self._key) if self._key else {}

    def _on_template(self) -> None:
        new_key = self._by_label.get(self.template_var.get(), self._key)
        if new_key == self._key:
            return
        old_overlays = self._overlay_values()
        self._key = new_key
        self._update_hint()
        self._rebuild_form()
        if old_overlays and self._form is not None:
            self._form.set_values(old_overlays)

    def _overlay_values(self) -> Dict[str, object]:
        if self._form is None:
            return {}
        values = self._form.values()
        return {spec.name: values.get(spec.name) for spec in OVERLAY_SPECS
                if values.get(spec.name) is not None}

    # ---------------------------------------------------------- 组合模式

    def _build_rules(self) -> None:
        panel = tk.Frame(self.form_host.body, bg=theme.color("surface"))
        panel.pack(fill="x", padx=(0, theme.px(4)))
        self._rules_panel = panel

        match_row = tk.Frame(panel, bg=theme.color("surface"))
        match_row.pack(fill="x", pady=(0, theme.px(8)))
        tk.Label(match_row, text="组合方式", bg=theme.color("surface"),
                 fg=theme.color("muted"), font=theme.font("caption"),
                 anchor="w").pack(side="left")
        match_label = {"all": "全部满足（且）", "any": "任一满足（或）"}
        self._match_var = tk.StringVar(
            value=match_label.get(str(self._rules.get("match") or "all"), match_label["all"]))
        match_combo = ttk.Combobox(match_row, textvariable=self._match_var,
                                   state="readonly", values=list(match_label.values()),
                                   width=16, font=theme.font("body"))
        match_combo.pack(side="left", padx=(theme.px(8), 0))
        match_combo.bind("<<ComboboxSelected>>", lambda e: self._update_preview())

        head = tk.Frame(panel, bg=theme.color("surface"))
        head.pack(fill="x", pady=(theme.px(4), theme.px(2)))
        tk.Label(head, text="入场条件", bg=theme.color("surface"),
                 fg=theme.color("text"), font=theme.font("h2"),
                 anchor="w").pack(side="left")
        Button(head, "+ 添加条件", size="sm", variant="secondary",
               command=self._add_rule_row).pack(side="right")

        self._rows_host = tk.Frame(panel, bg=theme.color("surface"))
        self._rows_host.pack(fill="x", pady=(theme.px(4), theme.px(10)))
        for rule in list(self._rules.get("entry") or []):
            self._add_rule_row(rule=rule, preview=False)

        tk.Label(panel, text="退出规则（至少要开一条）", bg=theme.color("surface"),
                 fg=theme.color("text"), font=theme.font("h2"),
                 anchor="w").pack(fill="x", pady=(theme.px(4), theme.px(4)))
        exit_host = tk.Frame(panel, bg=theme.color("surface"))
        exit_host.pack(fill="x")
        self._exit_entries: Dict[str, LabeledEntry] = {}
        for index, spec in enumerate(EXIT_SPECS):
            entry = LabeledEntry(
                exit_host, spec.label, self._rules["exit"].get(spec.name, spec.default),
                hint=spec.help, width=10, bg=theme.color("surface"))
            entry.grid(row=index // 2, column=index % 2, sticky="ew",
                       padx=(0, theme.px(12)), pady=(0, theme.px(8)))
            entry.entry.bind("<KeyRelease>", lambda e: self._update_preview())
            self._exit_entries[spec.name] = entry
        exit_host.columnconfigure(0, weight=1)
        exit_host.columnconfigure(1, weight=1)

        weight_row = tk.Frame(panel, bg=theme.color("surface"))
        weight_row.pack(fill="x", pady=(theme.px(4), 0))
        self._weight_entry = LabeledEntry(
            weight_row, "单标的仓位", self._rules.get("weight", 0.2),
            hint="每次买入占总资产的比例，0.05–0.5", width=10,
            bg=theme.color("surface"))
        self._weight_entry.pack(side="left")
        self._weight_entry.entry.bind("<KeyRelease>", lambda e: self._update_preview())

        self._preview = tk.Label(
            panel, text="", bg=theme.color("surface_alt"),
            fg=theme.color("text"), font=theme.font("body"),
            anchor="w", justify="left", wraplength=theme.px(650),
            padx=theme.px(10), pady=theme.px(8))
        self._preview.pack(fill="x", pady=(theme.px(10), theme.px(4)))
        self._update_preview()

    def _add_rule_row(self, rule: Optional[Dict[str, Any]] = None,
                      preview: bool = True) -> None:
        if rule is None:
            if len(self._rule_rows) >= 4:
                self._error.configure(text="入场条件最多 4 条，条件越多越容易过拟合")
                return
            rule = {"type": "ma_cross", "fast": 5, "slow": 20}
        row = _RuleRow(self._rows_host, rule, self._update_preview,
                       self._remove_rule_row)
        row.pack(fill="x", pady=(0, theme.px(6)))
        self._rule_rows.append(row)
        if preview:
            self._update_preview()

    def _remove_rule_row(self, row: _RuleRow) -> None:
        if row in self._rule_rows:
            self._rule_rows.remove(row)
            row.destroy()
            self._update_preview()

    def _collect_rules(self) -> Dict[str, Any]:
        match_label = self._match_var.get()
        match = "any" if match_label.startswith("任一") else "all"
        exits = {}
        for name, entry in self._exit_entries.items():
            exits[name] = entry.get()
        return {
            "match": match,
            "entry": [row.values() for row in self._rule_rows],
            "exit": exits,
            "weight": self._weight_entry.get(),
        }

    def _update_preview(self) -> None:
        if self._rules_panel is None:
            return
        rules = self._collect_rules()
        text = describe_partial(rules["entry"], rules["match"],
                                rules["exit"], rules["weight"])
        self._preview.configure(text="预览：" + text)

    # ---------------------------------------------------------- 保存

    def _save(self) -> None:
        name = self.name_entry.get().strip()
        payload: Dict[str, Any]
        if self._mode == "rules":
            rules = self._collect_rules()
            payload = {"kind": "rules", "name": name, "rules": rules}
        else:
            if self._form is None:
                return
            values = self._form.values()
            overlay_names = {spec.name for spec in OVERLAY_SPECS}
            payload = {
                "name": name,
                "kind": "template",
                "template": self._key,
                "params": {k: v for k, v in values.items() if k not in overlay_names},
                "stop_loss_pct": values.get("risk_stop_pct", 0),
                "take_profit_pct": values.get("risk_take_pct", 0),
                "trend_ma": values.get("risk_trend_ma", 0),
            }
        try:
            saved = self.service.save_profile(
                payload, self.meta.get("profile_id") or None)
        except Exception as exc:  # noqa: BLE001 - 直接展示给用户
            self._error.configure(text=str(exc))
            return
        self._error.configure(text="")
        self.close()
        self.on_saved(saved)
