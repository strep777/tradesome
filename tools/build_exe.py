"""用 PyInstaller 打包成免安装的 Windows 桌面程序。

用法：
    python -m pip install pyinstaller
    python tools\\build_exe.py

产物：dist\\TradeSome\\TradeSome.exe（连同 _internal、assets、doc、docs 整个目录分发）。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME = "TradeSome"


def ensure_assets() -> None:
    icon = ROOT / "assets" / "app.ico"
    if not icon.exists():
        print("assets/app.ico 不存在，先生成图标……")
        subprocess.check_call([sys.executable, str(ROOT / "tools" / "build_assets.py")],
                              cwd=str(ROOT))


def build() -> Path:
    ensure_assets()
    dist = ROOT / "dist"
    build_dir = ROOT / "build"
    if dist.exists():
        shutil.rmtree(dist)
    command = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--windowed",
        "--name", NAME,
        "--icon", str(ROOT / "assets" / "app.ico"),
        "--add-data", "%s;assets" % (ROOT / "assets"),
        "--add-data", "%s;doc" % (ROOT / "doc"),
        "--add-data", "%s;docs" % (ROOT / "docs"),
        "--distpath", str(dist),
        "--workpath", str(build_dir),
        "--specpath", str(build_dir),
        "--paths", str(ROOT),
        str(ROOT / "tradesome" / "__main__.py"),
    ]
    print("执行：", " ".join(command))
    subprocess.check_call(command, cwd=str(ROOT))
    target = dist / NAME / ("%s.exe" % NAME)
    if not target.exists():
        raise SystemExit("打包失败：找不到 %s" % target)
    # 让 exe 旁边的目录结构与源码一致，便于程序定位 var/assets
    for folder in ("assets", "doc", "docs"):
        source = dist / NAME / folder
        if not source.exists():
            shutil.copytree(ROOT / folder, source)
    print("完成：", target)
    return target


if __name__ == "__main__":
    build()
