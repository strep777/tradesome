"""生成新手引导相关截图：欢迎框、跨页引导、自由组合向导。"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import ImageGrab  # noqa: E402

from tradesome.app.bootstrap import build_container, shutdown  # noqa: E402
from tradesome.ui import enable_dpi_awareness  # noqa: E402
from tradesome.ui.shell import Shell  # noqa: E402
from tradesome.ui.widgets.strategy_wizard import StrategyWizard  # noqa: E402
from tradesome.ui.widgets.tour import WelcomeModal  # noqa: E402


def main() -> int:
    enable_dpi_awareness()
    container = build_container(memory_db=True, with_demo=True)
    app = Shell(container, welcome=False)
    app.attributes("-topmost", True)
    app.update()
    for _ in range(16):
        app.update()
        time.sleep(0.06)

    full = ImageGrab.grab(all_screens=True)
    scale = full.width / float(app.winfo_screenwidth())

    def shot(name: str) -> None:
        for _ in range(6):
            app.update()
            time.sleep(0.05)
        x, y = app.winfo_rootx(), app.winfo_rooty()
        box = (int(x * scale), int(y * scale),
               int((x + app.winfo_width()) * scale),
               int((y + app.winfo_height()) * scale))
        image = ImageGrab.grab(bbox=box, all_screens=True)
        image.save(ROOT / "var" / ("ui_%s.png" % name))
        print("saved", name, image.size)

    app.show_welcome()
    app.update()
    for _ in range(6):
        app.update()
        time.sleep(0.08)
    shot("welcome")
    for child in app.winfo_children():
        if isinstance(child, WelcomeModal):
            child.close()
    app.update()

    app.start_guide(0)
    for _ in range(16):
        app.update()
        time.sleep(0.06)
    shot("tour")
    app.tour.close()
    app.update()

    app.navigate("strategies")
    for _ in range(10):
        app.update()
        time.sleep(0.06)
    page = app._pages["strategies"]
    page._new()
    for _ in range(10):
        app.update()
        time.sleep(0.06)
    wizards = [w for w in app.winfo_children() if isinstance(w, StrategyWizard)]
    wizard = wizards[-1]
    wizard.name_entry.set("我的突破组合")
    wizard._set_mode("rules")
    for _ in range(10):
        app.update()
        time.sleep(0.06)
    shot("wizard_rules")
    wizard.close()
    app.update()

    app.destroy()
    shutdown(container)
    print("errors: []")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
