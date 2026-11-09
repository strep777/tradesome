"""生成策略工坊的两张验收截图：向导本身、保存后的自定义策略。

运行：python tools\\capture_wizard.py
输出：var/ui_wizard.png、var/ui_strategies_custom.png
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import ImageGrab

from tradesome.app.bootstrap import build_container, shutdown
from tradesome.ui import enable_dpi_awareness
from tradesome.ui.pages.strategies import StrategiesPage
from tradesome.ui.shell import Shell
from tradesome.ui.widgets.strategy_wizard import StrategyWizard


def grab(app: Shell, target: Path) -> None:
    app.update()
    x, y = app.winfo_rootx(), app.winfo_rooty()
    full = ImageGrab.grab(all_screens=True)
    scale = full.width / float(app.winfo_screenwidth())
    box = (int(x * scale), int(y * scale),
           int((x + app.winfo_width()) * scale),
           int((y + app.winfo_height()) * scale))
    ImageGrab.grab(bbox=box, all_screens=True).save(target)
    print("saved", target.name)


def main() -> int:
    enable_dpi_awareness()
    container = build_container(memory_db=True, with_demo=True)
    app = Shell(container, welcome=False)
    app.attributes("-topmost", True)
    app.update()
    for _ in range(24):
        app.update()
        time.sleep(0.05)

    app.navigate("strategies")
    app.update()
    page = app._pages["strategies"]
    assert isinstance(page, StrategiesPage)

    page._new()
    app.update()
    wizard = next((w for w in app.winfo_children()
                   if isinstance(w, StrategyWizard)), None)
    if wizard is None:
        raise RuntimeError("策略向导没有打开")
    wizard.name_entry.set("我的双均线")
    if "ma_cross" in [t["key"] for t in wizard._templates]:
        wizard.template_var.set(wizard._label_of("ma_cross"))
        wizard._on_template()
    wizard.show()
    for _ in range(6):
        app.update()
        time.sleep(0.1)

    grab(app, ROOT / "var" / "ui_wizard.png")

    wizard._save()
    for _ in range(6):
        app.update()
        time.sleep(0.1)
    # 等提示自动消失，避免盖住工具栏时间
    time.sleep(3.4)
    page.refresh()
    app.update()
    time.sleep(0.2)
    grab(app, ROOT / "var" / "ui_strategies_custom.png")

    app.destroy()
    shutdown(container)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
