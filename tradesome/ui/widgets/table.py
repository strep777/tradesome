"""数据表格：Treeview 的统一封装（表头、斑马纹、选中回调）。"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .. import theme


class DataTable(tk.Frame):
    def __init__(self, parent, columns: Sequence[Tuple],
                 height: int = 10, on_select: Optional[Callable] = None,
                 on_double: Optional[Callable] = None, selectmode: str = "browse",
                 bg: Optional[str] = None) -> None:
        background = bg or theme.color("surface")
        super().__init__(parent, bg=background)
        self.columns = list(columns)  # (key, title, width, anchor) 或 (key,title,width)
        keys = [c[0] for c in self.columns]
        self.tree = ttk.Treeview(self, columns=keys, show="headings",
                                 height=height, selectmode=selectmode)
        for column in self.columns:
            key, title = column[0], column[1]
            width = column[2] if len(column) > 2 else 100
            anchor = column[3] if len(column) > 3 else "w"
            stretch = column[4] if len(column) > 4 else False
            self.tree.heading(key, text=title, anchor=anchor)
            self.tree.column(key, width=theme.px(width), minwidth=theme.px(40),
                             anchor=anchor, stretch=stretch)
        self.scroll = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=self.scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        self.scroll.pack(side="right", fill="y")
        # macOS 列表不用斑马纹：统一白底，靠行高和留白区分。
        self.tree.tag_configure("odd", background=theme.color("surface"))
        self.tree.tag_configure("even", background=theme.color("surface"))
        self.tree.tag_configure("muted", foreground=theme.color("muted"))
        self.tree.tag_configure("up", foreground=theme.color("up"))
        self.tree.tag_configure("down", foreground=theme.color("down"))
        self._rows: Dict[str, dict] = {}
        self._on_select = on_select
        if on_select:
            self.tree.bind("<<TreeviewSelect>>", self._selected)
        if on_double:
            self.tree.bind("<Double-1>", lambda e: on_double(self.selected()))

    # ---------------------------------------------------------- 数据

    def set_rows(self, rows: List[dict]) -> None:
        selected = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        self._rows.clear()
        for index, row in enumerate(rows):
            values = [self._cell(row.get(key)) for key, _ in
                      [(c[0], c[1]) for c in self.columns]]
            iid = self.tree.insert("", "end", values=values,
                                   tags=("even" if index % 2 else "odd",))
            self._rows[iid] = row
        if selected:
            for iid in selected:
                if iid in self._rows:
                    self.tree.selection_set(iid)
                    break

    def set_rows_colored(self, rows: List[dict],
                         tone_key: Optional[str] = None) -> None:
        """按某一列的数值给整行上色（涨红跌绿），用于交易/订单列表。"""
        self.tree.delete(*self.tree.get_children())
        self._rows.clear()
        for index, row in enumerate(rows):
            tags = ["even" if index % 2 else "odd"]
            if tone_key is not None:
                value = row.get(tone_key)
                if isinstance(value, (int, float)):
                    tags.append("up" if value > 0 else "down" if value < 0 else "muted")
            values = [self._cell(row.get(column[0])) for column in self.columns]
            iid = self.tree.insert("", "end", values=values, tags=tuple(tags))
            self._rows[iid] = row

    @staticmethod
    def _cell(value) -> str:
        if value is None:
            return "—"
        if isinstance(value, bool):
            return "是" if value else "否"
        if isinstance(value, float):
            return "{:,.2f}".format(value)
        return str(value)

    def clear(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self._rows.clear()

    def count(self) -> int:
        return len(self._rows)

    def selected(self) -> Optional[dict]:
        selection = self.tree.selection()
        if not selection:
            return None
        return self._rows.get(selection[0])

    def select_first(self) -> Optional[dict]:
        children = self.tree.get_children()
        if not children:
            return None
        self.tree.selection_set(children[0])
        self.tree.focus(children[0])
        return self._rows.get(children[0])

    def _selected(self, _event=None) -> None:
        if self._on_select:
            self._on_select(self.selected())
