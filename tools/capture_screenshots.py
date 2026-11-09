"""把 11 个页面各截一张图，用于验收与文档配图。

用法：在仓库根目录执行 `python tools/capture_screenshots.py`。
输出：`var/ui_<page>.png`。需要 Pillow（`pip install pillow`）与可交互的桌面会话。
截图前会先构建一个内存库容器并生成演示数据，因此不会污染 `var/tradesome.db`。
"""

from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tradesome.app.bootstrap import build_container, shutdown  # noqa: E402
from tradesome.ui import enable_dpi_awareness  # noqa: E402
from tradesome.ui.shell import Shell  # noqa: E402

OUT = ROOT / "var"
PAGES = ["dashboard", "market", "strategies", "lab", "backtest",
         "live", "live_trade", "risk", "data", "settings", "help"]


def main() -> int:
    from PIL import ImageGrab

    OUT.mkdir(parents=True, exist_ok=True)
    # 必须先声明 DPI 感知再创建 Tk，否则窗口会被系统位图拉伸、截图发虚。
    enable_dpi_awareness()
    container = build_container(memory_db=True, with_demo=True)
    app = Shell(container, welcome=False)
    errors = []
    app.show_error = lambda title, message="", hint="": errors.append((title, message))
    app.update()
    app.deiconify()
    app.lift()
    app.attributes("-topmost", True)
    app.focus_force()
    for _ in range(6):
        app.update()
        time.sleep(0.1)

    full = ImageGrab.grab(all_screens=True)
    scale = full.width / float(app.winfo_screenwidth())
    print("scale", scale, "full", full.size)
    for key in PAGES:
        try:
            app.navigate(key)
            for _ in range(4):
                app.update()
                time.sleep(0.12)
            # 把焦点移出输入框，避免中文输入法的候选窗盖在截图里。
            try:
                app.focus_force()
                app.sidebar.focus_set()
                app.update()
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.15)
            x, y = app.winfo_rootx(), app.winfo_rooty()
            w, h = app.winfo_width(), app.winfo_height()
            box = (int(x * scale), int(y * scale),
                   int((x + w) * scale), int((y + h) * scale))
            img = ImageGrab.grab(bbox=box, all_screens=True)
            img.save(OUT / ("ui_%s.png" % key))
            print("shot", key, img.size)
        except Exception:  # noqa: BLE001
            print("FAIL page", key)
            traceback.print_exc()
    print("errors:", errors)
    app.destroy()
    shutdown(container)
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
