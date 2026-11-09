"""表单控件：参数表单由 ParamSpec 自动生成，新增策略不需要改界面。"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Dict, List, Optional

from .. import theme
from .shapes import rounded_rect


class _Field(tk.Canvas):
    """圆角输入框：Canvas 画边框，Entry 作为窗口嵌入。"""

    def __init__(self, parent, value: str = "", width: int = 18,
                 bg: Optional[str] = None) -> None:
        background = bg or theme.color("surface")
        super().__init__(parent, bg=background, height=theme.px(36),
                         width=theme.px(40 + max(6, width) * 7),
                         highlightthickness=0, bd=0)
        self._background = background
        self._focused = False
        self.entry = tk.Entry(self, bd=0, relief="flat",
                              bg=theme.color("surface"), fg=theme.color("text"),
                              insertbackground=theme.color("text"),
                              font=theme.font("body"), width=width,
                              highlightthickness=0)
        self._win = self.create_window(theme.px(9), theme.px(18), anchor="w",
                                       window=self.entry)
        self.entry.insert(0, str(value))
        self.entry.bind("<FocusIn>", self._on_focus_in)
        self.entry.bind("<FocusOut>", self._on_focus_out)
        self.bind("<Configure>", self._redraw)

    def _on_focus_in(self, _event=None) -> None:
        self._focused = True
        self._redraw()

    def _on_focus_out(self, _event=None) -> None:
        self._focused = False
        self._redraw()

    def _redraw(self, event=None) -> None:
        width = event.width if event is not None else self.winfo_width()
        height = event.height if event is not None else self.winfo_height()
        if width < 4 or height < 4:
            return
        outline = theme.color("accent") if self._focused else theme.color("border")
        self.delete("field")
        rounded_rect(self, 1, 1, width - 1, height - 1, theme.px(6),
                     fill=theme.color("surface"), outline=outline, width=1,
                     tags="field")
        self.tag_lower("field")
        self.itemconfigure(self._win, width=max(theme.px(20), width - theme.px(18)),
                           height=theme.px(20))


class LabeledEntry(tk.Frame):
    def __init__(self, parent, label: str, value: str = "",
                 hint: str = "", width: int = 18, bg: Optional[str] = None) -> None:
        background = bg or theme.color("surface")
        super().__init__(parent, bg=background)
        tk.Label(self, text=label, bg=background, fg=theme.color("muted"),
                 font=theme.font("caption"), anchor="w").pack(anchor="w")
        self.field = _Field(self, value, width=width, bg=background)
        self.field.pack(fill="x", pady=(theme.px(4), 0))
        self.entry = self.field.entry
        if hint:
            tk.Label(self, text=hint, bg=background, fg=theme.color("faint"),
                     font=theme.font("small"), anchor="w").pack(anchor="w",
                                                                pady=(2, 0))

    def _focus_in(self, _event=None) -> None:
        self.field._on_focus_in()

    def _focus_out(self, _event=None) -> None:
        self.field._on_focus_out()

    def get(self) -> str:
        return self.entry.get().strip()

    def set(self, value) -> None:
        self.entry.delete(0, "end")
        self.entry.insert(0, "" if value is None else str(value))


class Toggle(tk.Canvas):
    """macOS 风格滑动开关：44×24 胶囊 + 圆形滑块。"""

    def __init__(self, parent, text: str, variable: tk.BooleanVar,
                 bg: Optional[str] = None, command=None) -> None:
        self._background = bg or theme.color("surface")
        self.variable = variable
        self._command = command
        self._text = text
        self._switch_w = theme.px(44)
        self._switch_h = theme.px(24)
        font = theme.font("body")
        from tkinter import font as tkfont
        text_width = tkfont.Font(font=font).measure(text) if text else 0
        width = self._switch_w + (theme.px(10) + text_width if text else 0)
        super().__init__(parent, bg=self._background, highlightthickness=0, bd=0,
                         width=width, height=self._switch_h, cursor="hand2",
                         takefocus=1)
        self._font = font
        self._trace = variable.trace_add("write", lambda *_: self._draw())
        self.bind("<Button-1>", self._on_click)
        self.bind("<space>", self._on_click)
        self.bind("<Return>", self._on_click)
        self.bind("<Destroy>", self._on_destroy)
        self._draw()

    def _on_destroy(self, _event=None) -> None:
        try:
            self.variable.trace_remove("write", self._trace)
        except (tk.TclError, KeyError):
            pass

    def _draw(self) -> None:
        self.delete("all")
        h = self._switch_h
        r = h / 2
        on = bool(self.variable.get())
        track = theme.color("accent") if on else theme.color("border")
        if not on:
            track = "#D1D1D6" if theme.theme_name() == "macos" else theme.color("border")
        self.create_oval(0, 0, h, h, fill=track, outline=track)
        self.create_oval(self._switch_w - h, 0, self._switch_w, h,
                         fill=track, outline=track)
        self.create_rectangle(r, 0, self._switch_w - r, h, fill=track, outline=track)
        knob = h - theme.px(4)
        x = self._switch_w - h + theme.px(2) if on else theme.px(2)
        self.create_oval(x, theme.px(2), x + knob, theme.px(2) + knob,
                         fill="#FFFFFF", outline="#FFFFFF")
        if self._text:
            self.create_text(self._switch_w + theme.px(10), h / 2,
                             text=self._text, anchor="w",
                             fill=theme.color("text"), font=self._font)

    def _on_click(self, _event=None) -> None:
        self.variable.set(not bool(self.variable.get()))
        if self._command is not None:
            self._command()
        self.focus_set()



class ParamForm(tk.Frame):
    """根据策略的 ParamSpec 列表生成输入控件。"""

    def __init__(self, parent, specs: List[dict], values: Optional[Dict] = None,
                 bg: Optional[str] = None, columns: int = 2) -> None:
        background = bg or theme.color("surface")
        super().__init__(parent, bg=background)
        self.specs = specs
        self._vars: Dict[str, object] = {}
        self._widgets: Dict[str, tk.Widget] = {}
        values = values or {}
        current_row = None
        for index, spec in enumerate(specs):
            if index % columns == 0:
                current_row = tk.Frame(self, bg=background)
                current_row.pack(fill="x", pady=(0, theme.px(10)))
                for col in range(columns):
                    current_row.grid_columnconfigure(col, weight=1, uniform="param")
            holder = tk.Frame(current_row, bg=background)
            holder.grid(row=0, column=index % columns, sticky="ew",
                        padx=(0, theme.px(12) if index % columns == 0 else 0))
            self._build_field(holder, spec, values.get(spec["name"],
                                                       spec.get("default")))

    def _build_field(self, parent, spec: dict, value) -> None:
        name = spec["name"]
        label = spec.get("label") or name
        unit = spec.get("unit") or ""
        hint_parts = []
        if spec.get("min") is not None or spec.get("max") is not None:
            hint_parts.append("范围 %s ~ %s" % (spec.get("min", "-"),
                                               spec.get("max", "-")))
        if unit:
            hint_parts.append(unit)
        if spec.get("help"):
            hint_parts.append(spec["help"])
        hint = " · ".join(hint_parts)
        kind = spec.get("type", "int")
        if kind == "bool":
            variable = tk.BooleanVar(value=bool(value))
            widget = Toggle(parent, label, variable, bg=theme.color("surface"))
            widget.pack(anchor="w", pady=(theme.px(6), 0))
            self._vars[name] = variable
            self._widgets[name] = widget
            return
        tk.Label(parent, text=label, bg=theme.color("surface"),
                 fg=theme.color("muted"), font=theme.font("caption"),
                 anchor="w").pack(anchor="w")
        if kind == "choice":
            choices = list(spec.get("choices") or [])
            variable = tk.StringVar(value=str(value if value is not None else
                                              (choices[0] if choices else "")))
            widget = ttk.Combobox(parent, textvariable=variable, values=choices,
                                  state="readonly", font=theme.font("body"))
            widget.pack(fill="x", pady=(theme.px(4), 0))
            self._vars[name] = variable
            self._widgets[name] = widget
        else:
            field = _Field(parent, "" if value is None else str(value))
            field.pack(fill="x", pady=(theme.px(4), 0))
            self._vars[name] = field.entry
            self._widgets[name] = field
            if spec.get("step") and kind in ("int", "float"):
                pass
        if hint:
            tk.Label(parent, text=hint, bg=theme.color("surface"),
                     fg=theme.color("faint"), font=theme.font("small"),
                     anchor="w", justify="left", wraplength=theme.px(260)).pack(
                anchor="w", pady=(2, 0))

    def values(self) -> Dict[str, object]:
        out: Dict[str, object] = {}
        for name, variable in self._vars.items():
            if isinstance(variable, (tk.BooleanVar, tk.StringVar)):
                out[name] = variable.get()
            elif isinstance(variable, tk.Entry):
                out[name] = variable.get().strip()
        return out

    def set_values(self, values: Dict[str, object]) -> None:
        for name, value in values.items():
            variable = self._vars.get(name)
            if isinstance(variable, tk.BooleanVar):
                variable.set(bool(value))
            elif isinstance(variable, tk.StringVar):
                variable.set(str(value))
            elif isinstance(variable, tk.Entry):
                variable.delete(0, "end")
                variable.insert(0, "" if value is None else str(value))

    def reset_defaults(self) -> None:
        for spec in self.specs:
            self.set_values({spec["name"]: spec.get("default")})
