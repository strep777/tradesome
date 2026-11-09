"""资源与打包相关文件测试：图标、示例数据、策略模板、启动脚本。"""

from __future__ import annotations

import shutil
import unittest
from pathlib import Path

from tradesome.app.bootstrap import build_container, shutdown
from tradesome.strategy.registry import StrategyRegistry
from tests.logging_utils import close_log_handlers
from tests.support import TempWorkspace

ROOT = Path(__file__).resolve().parents[2]


class AssetsTest(unittest.TestCase):
    def test_icon_and_scripts_exist(self) -> None:
        for name in ("assets/app.ico", "assets/app.png",
                     "assets/600519.SH.csv",
                     "assets/templates/my_dual_ma.py",
                     "start_tradesome.cmd", "tradesome_debug.cmd",
                     "README.md", "pyproject.toml", "requirements.txt"):
            self.assertTrue((ROOT / name).exists(), "缺少资源：%s" % name)

    def test_sample_csv_imports(self) -> None:
        with TempWorkspace() as ws:
            container = build_container(root=ws.root, memory_db=True,
                                        with_demo=False)
            try:
                result = container.services.market.import_csv(
                    ROOT / "assets" / "600519.SH.csv")
                self.assertEqual(result["symbol"], "600519.SH")
                self.assertEqual(result["bars"], 60)
                self.assertTrue(result["report"].ok)
            finally:
                shutdown(container)
                close_log_handlers()

    def test_template_loads_as_user_strategy(self) -> None:
        with TempWorkspace() as ws:
            target = Path(ws.root) / "strategies"
            target.mkdir(parents=True, exist_ok=True)
            shutil.copy(ROOT / "assets" / "templates" / "my_dual_ma.py", target)
            registry = StrategyRegistry(user_dir=str(target))
            self.assertIn("dual_ma_example", registry.keys())
            self.assertTrue(registry.is_user("dual_ma_example"))
            self.assertEqual(registry.errors(), [])
            strategy = registry.create("dual_ma_example", {"fast": 8, "slow": 34})
            self.assertEqual(strategy.p["fast"], 8)


if __name__ == "__main__":
    unittest.main()
