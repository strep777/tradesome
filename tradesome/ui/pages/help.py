"""帮助页：快速开始、快捷键、常见问题与风险提示。"""

from __future__ import annotations

import os
import tkinter as tk

from ... import RISK_DISCLAIMER, __version__
from .. import theme
from ..widgets import Button, Card, DataTable, SectionHeader
from .base import BasePage

STEPS = [
    ("1", "先有数据", "数据中心 →「同步真实行情」（需安装 akshare），或导入自己的 CSV。没有数据，回测与模拟盘都是空的。"),
    ("2", "选个策略跑回测", "回测页 → 选策略与标的 → 开始回测。先看最大回撤，再看收益。"),
    ("3", "调参并保存成自己的策略", "实验室 → 试跑参数；满意后到策略库 →「新建策略」：可以选模板改参数，也可以「自由组合」从零搭入场条件与退出规则，保存即可用，不用写文件。"),
    ("4", "模拟盘走一遍", "模拟盘 → 按真实节奏回放行情，看下单、拒单、持仓与风控是否符合预期。"),
    ("5", "（可选）实盘演练", "设置 → 实盘 → 启用；实盘页解锁后用「模拟柜台」走一遍。接真实券商需要 QMT/miniQMT，详见 docs/14。"),
]

FAQ = [
    ("为什么回测赚钱，实盘却不一定？",
     "回测是历史重放：它不知道你下单那一刻的真实盘口，也无法复现滑点、停牌与情绪。"
     "把回测当成筛掉坏想法的工具，而不是收益承诺。"),
    ("为什么我的买单被拒绝了？",
     "常见原因：可用资金不足、卖出时 T+1 未解禁、单笔仓位超过风控上限、"
     "当日亏损触发熔断、涨跌停无法成交。风控页的「查看拒单记录」有完整原因。"),
    ("为什么成交价和收盘价不一样？",
     "本软件按「下一根 K 线开盘价 + 滑点」成交，这是为了避免用当日收盘价下单造成的未来函数。"),
    ("新建策略要写代码或建文件吗？",
     "不需要。策略库 →「新建策略」是一个向导：选模板调参数，或切到「自由组合」从零搭 1–4 个入场条件 + 退出规则，保存进本地数据库。"
     "只有需要自定义指标时才手写策略类，属于进阶用法；界面上已经不提供写文件入口。"),
    ("策略里能用 pandas 吗？",
     "不能。策略运行在受限沙箱中，只允许 math/statistics/datetime 等标准库与内置指标库，"
     "目的是保证可复现与安全。"),
    ("数据从哪来？能接实时行情吗？",
     "历史行情来自 akshare 同步的真实日线，或你自己导入的 CSV；本版实盘网关只做交易、不订阅行情，"
     "报价来自本地日线或手动刷新。本项目不自带行情订阅服务，避免把账号或行情授权带进本地软件。"),
    ("实盘会不会偷偷下单？",
     "不会。实盘默认关闭，每次启动都要重新解锁并输入确认词；每笔真实委托还要二次确认；"
     "只有本机 miniQMT 已登录且你主动连接成功后，委托才可能到达柜台。所有动作都写审计。"),
    ("密钥存在哪里？",
     "var/secrets.dat，优先用 Windows DPAPI 按当前用户加密；换 Windows 用户或换机器后需要重新填写。"),
    ("没有数据怎么办？",
     "到数据中心点「同步全部 A 股」拿全市场快照（首次会自动安装 akshare，需要联网）；"
     "要回测某只股票，再点「同步真实行情」抓多年日线。没有网络时用「导入 CSV」。"
     "首次启动是空库，不会自动生成演示数据——那样回测出来的是假行情。"),
    ("怎么看到全部 A 股？",
     "数据中心 →「同步全部 A 股」，约 1 分钟写入 5000 多只当日快照；"
     "行情页会列出全部标的，用右上角搜索框按代码或名称过滤。选中某只时会自动补齐"
     "该股全部历史日线，图表可切换「全部历史 / 近 3 年 / 近 1 年」。"),
    ("误删了数据怎么办？",
     "删除只影响行情库。设置页可以清空回测历史；重要数据请定期把 var/ 目录整体备份。"),
]

SHORTCUTS = [
    {"keys": "Ctrl + 1 … 9", "action": "切换到第 1–9 个页面"},
    {"keys": "Ctrl + B", "action": "打开回测页"},
    {"keys": "F5", "action": "刷新当前页"},
    {"keys": "F1", "action": "打开本帮助页"},
    {"keys": "Esc", "action": "关闭弹窗 / 收起提示"},
    {"keys": "Ctrl + \\", "action": "折叠或展开侧边栏"},
]


class HelpPage(BasePage):
    title = "帮助"
    subtitle = "五分钟上手；遇到问题时，先看这里的「常见问题」。"

    help_steps = [
        '快速开始按顺序走一遍全部主流程。',
        '遇到报错先查「常见问题」。',
        '需要更细的说明时打开文档目录（docs/00–17）。',
    ]

    def tour_targets(self) -> dict:
        return {"tutorial": getattr(self, "_tutorial_card", None)}

    def build(self) -> None:
        header = self.page_header()
        Button(header, "打开文档目录", command=self._open_docs, variant="secondary",
               size="sm").pack(side="right")
        body = self.scroll_body()
        inner = tk.Frame(body, bg=theme.color("bg"))
        inner.pack(fill="both", expand=True, padx=theme.px(24), pady=(0, theme.px(20)))

        self._build_tutorial(inner)
        self._build_steps(inner)
        self._build_shortcuts(inner)
        self._build_faq(inner)
        self._build_about(inner)

    def _build_tutorial(self, parent) -> None:
        card = Card(parent)
        card.pack(fill="x", pady=(0, theme.px(12)))
        self._tutorial_card = card
        SectionHeader(card.body, "新手引导与教程中心",
                      "不知道从哪开始时，跟着高亮走一遍；每一步只做一件事。").pack(fill="x")
        row = tk.Frame(card.body, bg=theme.color("surface"))
        row.pack(fill="x", pady=(theme.px(10), 0))
        Button(row, "重新观看 3 分钟引导", variant="primary", size="sm",
               command=lambda: self.shell.start_guide(0)).pack(side="left")
        Button(row, "怎么建策略", variant="secondary", size="sm",
               command=lambda: self.shell.navigate("strategies")).pack(
                   side="left", padx=(theme.px(8), 0))
        Button(row, "怎么看回测", variant="secondary", size="sm",
               command=lambda: self.shell.navigate("backtest")).pack(
                   side="left", padx=(theme.px(8), 0))

    def _build_steps(self, parent) -> None:
        card = Card(parent)
        card.pack(fill="x", pady=(0, theme.px(12)))
        SectionHeader(card.body, "快速开始",
                      "按顺序走一遍，你就掌握了这个软件的全部主流程。").pack(fill="x")
        for number, title, text in STEPS:
            row = tk.Frame(card.body, bg=theme.color("surface"))
            row.pack(fill="x", pady=(theme.px(8), 0))
            badge = tk.Label(row, text=number, bg=theme.color("accent_soft"),
                             fg=theme.color("accent"), font=theme.font("h2"),
                             width=3, height=1)
            badge.pack(side="left", anchor="n", padx=(0, theme.px(10)))
            holder = tk.Frame(row, bg=theme.color("surface"))
            holder.pack(side="left", fill="x", expand=True)
            tk.Label(holder, text=title, bg=theme.color("surface"),
                     fg=theme.color("text"), font=theme.font("body"),
                     anchor="w").pack(fill="x")
            tk.Label(holder, text=text, bg=theme.color("surface"),
                     fg=theme.color("muted"), font=theme.font("caption"),
                     anchor="w", justify="left",
                     wraplength=theme.px(860)).pack(fill="x", pady=(2, 0))
        actions = tk.Frame(card.body, bg=theme.color("surface"))
        actions.pack(fill="x", pady=(theme.px(12), 0))
        Button(actions, "去数据中心", command=lambda: self.shell.navigate("data"),
               variant="secondary", size="sm").pack(side="left", padx=(0, 6))
        Button(actions, "去回测", command=lambda: self.shell.navigate("backtest"),
               variant="secondary", size="sm").pack(side="left", padx=(0, 6))
        Button(actions, "去模拟盘", command=lambda: self.shell.navigate("live"),
               variant="secondary", size="sm").pack(side="left")

    def _build_shortcuts(self, parent) -> None:
        card = Card(parent)
        card.pack(fill="x", pady=(0, theme.px(12)))
        SectionHeader(card.body, "快捷键", "所有操作都能用鼠标完成，快捷键只是给熟手提速。").pack(fill="x")
        table = DataTable(card.body, [("keys", "按键", 160), ("action", "作用", 420)],
                          height=6)
        table.pack(fill="x", pady=(theme.px(8), 0))
        table.set_rows(SHORTCUTS)

    def _build_faq(self, parent) -> None:
        card = Card(parent)
        card.pack(fill="x", pady=(0, theme.px(12)))
        SectionHeader(card.body, "常见问题", "").pack(fill="x")
        for question, answer in FAQ:
            block = tk.Frame(card.body, bg=theme.color("surface"))
            block.pack(fill="x", pady=(theme.px(10), 0))
            tk.Label(block, text="Q  " + question, bg=theme.color("surface"),
                     fg=theme.color("text"), font=theme.font("body"),
                     anchor="w", justify="left",
                     wraplength=theme.px(900)).pack(fill="x")
            tk.Label(block, text=answer, bg=theme.color("surface"),
                     fg=theme.color("muted"), font=theme.font("caption"),
                     anchor="w", justify="left",
                     wraplength=theme.px(900)).pack(fill="x", pady=(2, 0), padx=(theme.px(18), 0))

    def _build_about(self, parent) -> None:
        card = Card(parent)
        card.pack(fill="x")
        SectionHeader(card.body, "关于 TradeSome", "v%s · 本地部署 · 零第三方依赖" % __version__).pack(fill="x")
        tk.Label(card.body, text=RISK_DISCLAIMER, bg=theme.color("surface"),
                 fg=theme.color("muted"), font=theme.font("caption"),
                 justify="left", anchor="w",
                 wraplength=theme.px(900)).pack(fill="x", pady=(theme.px(8), 0))
        tk.Label(card.body,
                 text=("文档位于安装目录的 docs/ 下：01 需求、02 架构、03 领域模型、04 模块设计、"
                       "05 策略开发、06 安全、07 性能、08 界面、09 测试、10 部署、11 规范、12 决策记录。"
                       "量化交易知识总结在 doc/量化交易知识总结.md。"),
                 bg=theme.color("surface"), fg=theme.color("faint"),
                 font=theme.font("small"), justify="left", anchor="w",
                 wraplength=theme.px(900)).pack(fill="x", pady=(theme.px(8), 0))

    def _open_docs(self) -> None:
        target = self.c.paths.docs
        if not target.exists():
            self.toast("未找到文档目录：%s" % target, "warning")
            return
        try:
            os.startfile(str(target))  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            self.toast("文档目录：%s" % target, "info")
