"""架构约束测试：分层依赖、零第三方依赖、UI 与内核隔离。

这些断言不验证业务行为，只保证"模块可以被独立替换"这条设计承诺没有在
迭代中被悄悄破坏。阈值是上限而非目标，见 docs/02 与 docs/09 §5.10。
"""

from __future__ import annotations

import ast
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
PKG = ROOT / "tradesome"
FORBIDDEN_THIRD_PARTY = {"pandas", "numpy", "matplotlib", "PySide6", "PyQt5"}
ALLOWED_PRINT = {"cli.py", "__main__.py"}
MAX_FILE_LINES = 800
MAX_FUNC_LINES = 150


def iter_modules():
    for path in PKG.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        yield path, path.relative_to(PKG).as_posix()


def imports_of(tree):
    """返回 (绝对模块名, 相对层级) 列表，相对导入按文件位置还原。"""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((alias.name, 0))
        elif isinstance(node, ast.ImportFrom):
            found.append((node.module or "", node.level or 0))
    return found


def absolute_name(rel_path, module, level):
    if level == 0:
        return module
    parts = rel_path.split("/")[:-1]
    keep = len(parts) - (level - 1)
    base = parts[:max(0, keep)]
    if module:
        base = base + module.split(".")
    return ".".join(["tradesome"] + base)


class ArchitectureTest(unittest.TestCase):
    def test_no_third_party_imports(self):
        offenders = []
        for path, rel in iter_modules():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for module, _level in imports_of(tree):
                root = module.split(".")[0]
                if root in FORBIDDEN_THIRD_PARTY:
                    offenders.append("%s → %s" % (rel, module))
        self.assertEqual(offenders, [], "核心不得依赖第三方库：%s" % offenders)

    def test_tkinter_only_in_ui(self):
        offenders = []
        for path, rel in iter_modules():
            if rel.startswith("ui/"):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for module, _level in imports_of(tree):
                if module.split(".")[0] in {"tkinter", "ttk"}:
                    offenders.append("%s → %s" % (rel, module))
        self.assertEqual(offenders, [], "只有 ui/ 可以 import tkinter：%s" % offenders)

    def test_core_never_imports_upper_layers(self):
        offenders = []
        for path, rel in iter_modules():
            if not rel.startswith("core/") or rel == "core/__init__.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for module, level in imports_of(tree):
                if level >= 2:
                    offenders.append("%s → 第 %d 层相对导入" % (rel, level))
                elif module.startswith("tradesome.") and not module.startswith(
                        "tradesome.core"):
                    offenders.append("%s → %s" % (rel, module))
        self.assertEqual(offenders, [], "core 只能依赖自身与标准库：%s" % offenders)

    def test_upper_layers_do_not_import_ui_or_app(self):
        offenders = []
        for path, rel in iter_modules():
            if rel.startswith(("ui/", "app/")) or rel == "cli.py":
                # cli 是组合根：它负责把 app 与 ui 接起来，允许依赖两者。
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for module, level in imports_of(tree):
                name = absolute_name(rel, module, level)
                if name.startswith(("tradesome.ui", "tradesome.app")):
                    offenders.append("%s → %s" % (rel, name))
        self.assertEqual(offenders, [],
                         "下层不得反向依赖 ui/app（换 UI 不应改领域层）：%s" % offenders)

    def test_no_print_outside_cli(self):
        offenders = []
        for path, rel in iter_modules():
            if rel in ALLOWED_PRINT:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                        and node.func.id == "print":
                    offenders.append("%s:%d" % (rel, node.lineno))
        self.assertEqual(offenders, [], "库代码用 logging，不用 print：%s" % offenders)

    def test_every_package_has_init(self):
        missing = []
        for directory in [PKG] + [p for p in PKG.rglob("*") if p.is_dir()]:
            if "__pycache__" in directory.parts:
                continue
            if not (directory / "__init__.py").exists():
                missing.append(str(directory.relative_to(ROOT)))
        self.assertEqual(missing, [], "每个包都需要 __init__.py：%s" % missing)

    def test_file_and_function_size_limits(self):
        too_long = []
        too_big = []
        for path, rel in iter_modules():
            source = path.read_text(encoding="utf-8")
            lines = source.count("\n") + 1
            if lines > MAX_FILE_LINES:
                too_long.append("%s (%d 行)" % (rel, lines))
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    size = (node.end_lineno or node.lineno) - node.lineno + 1
                    if size > MAX_FUNC_LINES:
                        too_big.append("%s:%s (%d 行)" % (rel, node.name, size))
        self.assertEqual(too_long, [], "单文件超过 %d 行：%s" % (MAX_FILE_LINES, too_long))
        self.assertEqual(too_big, [], "单函数超过 %d 行：%s" % (MAX_FUNC_LINES, too_big))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
