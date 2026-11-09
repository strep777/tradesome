"""用户策略的静态检查与受限加载。

这不是真正的安全沙箱（Python 进程内做不到），而是一道"防手滑"的护栏：
拦截网络/进程/文件操作、动态执行与常见逃逸写法，并在加载时限制可导入模块。
策略仍然运行在本进程内，所以只加载你自己信任的文件。
"""

from __future__ import annotations

import ast
import os
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from ..core.errors import StrategyLoadError
from ..core.logging import get_logger
from .base import Strategy, StrategyMeta

LOGGER = get_logger("strategy.sandbox")

FORBIDDEN_MODULES: Set[str] = {
    "os", "sys", "subprocess", "socket", "shutil", "ctypes", "importlib",
    "pickle", "marshal", "builtins", "http", "urllib", "requests", "ftplib",
    "multiprocessing", "threading", "asyncio", "pathlib", "glob", "tempfile",
    "webbrowser", "tkinter", "sqlite3", "ssl", "pty", "signal",
}

ALLOWED_MODULES: Set[str] = {
    "math", "statistics", "datetime", "collections", "typing", "dataclasses",
    "functools", "itertools", "random", "re", "json",
    "tradesome.strategy.base", "tradesome.core.types",
    "tradesome.strategy.indicators",
}

FORBIDDEN_CALLS: Set[str] = {
    "eval", "exec", "compile", "__import__", "open", "input", "breakpoint",
    "exit", "quit", "globals", "locals", "vars", "getattr", "setattr",
    "delattr", "memoryview", "help",
}

FORBIDDEN_ATTRS: Set[str] = {
    "__globals__", "__subclasses__", "__builtins__", "__code__", "__class__",
    "__mro__", "__bases__", "__dict__", "__getattribute__", "__reduce__",
    "__loader__", "__spec__",
}

MAX_SOURCE_BYTES = 200 * 1024
KEY_PATTERN = r"^[a-z][a-z0-9_]{2,40}$"


def check_source(source: str, filename: str = "<strategy>") -> List[str]:
    """返回问题列表；空列表表示通过。不做执行。"""
    problems: List[str] = []
    if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
        problems.append("文件过大（超过 200KB）")
        return problems
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as exc:
        return ["语法错误：第 %s 行 %s" % (exc.lineno, exc.msg)]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in FORBIDDEN_MODULES:
                    problems.append("第 %d 行：禁止导入模块 %s" % (node.lineno, alias.name))
                elif not _module_allowed(alias.name):
                    problems.append("第 %d 行：模块 %s 不在白名单内" % (node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            root = module.split(".")[0]
            if node.level:
                problems.append("第 %d 行：用户策略不支持相对导入" % node.lineno)
            elif root in FORBIDDEN_MODULES:
                problems.append("第 %d 行：禁止从 %s 导入" % (node.lineno, module))
            elif not _module_allowed(module):
                problems.append("第 %d 行：模块 %s 不在白名单内" % (node.lineno, module))
        elif isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name in FORBIDDEN_CALLS:
                problems.append("第 %d 行：禁止调用 %s" % (node.lineno, name))
            elif name.startswith("os.") or name.startswith("subprocess."):
                problems.append("第 %d 行：禁止调用 %s" % (node.lineno, name))
        elif isinstance(node, ast.Attribute):
            if node.attr in FORBIDDEN_ATTRS:
                problems.append("第 %d 行：禁止访问属性 %s" % (node.lineno, node.attr))
        elif isinstance(node, ast.With):
            problems.append("第 %d 行：用户策略中禁止 with（文件/锁）" % node.lineno)
        if len(problems) > 40:
            problems.append("问题过多，已停止检查")
            break
    return problems


def _module_allowed(name: str) -> bool:
    if name in ALLOWED_MODULES:
        return True
    for allowed in ALLOWED_MODULES:
        if name.startswith(allowed + "."):
            return True
    return False


def _call_name(func: ast.AST) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        base = _call_name(func.value)
        return ("%s.%s" % (base, func.attr)) if base else func.attr
    return ""


def _safe_import(name: str, globals_dict=None, locals_dict=None, fromlist=(), level=0):
    if level:
        raise ImportError("用户策略不支持相对导入")
    root = name.split(".")[0]
    if root in FORBIDDEN_MODULES or not _module_allowed(name):
        raise ImportError("模块 %s 不允许在策略中使用" % name)
    return __import__(name, globals_dict, locals_dict, fromlist, 0)


def _build_builtins() -> Dict[str, Any]:
    import builtins as real

    allowed = (
        "abs", "all", "any", "bool", "dict", "enumerate", "filter", "float",
        "int", "isinstance", "issubclass", "len", "list", "map", "max", "min",
        "range", "reversed", "round", "set", "slice", "sorted", "str", "sum",
        "tuple", "zip", "print", "Exception", "ValueError", "TypeError",
        "KeyError", "IndexError", "ZeroDivisionError", "RuntimeError",
        "ArithmeticError", "object", "super", "property", "staticmethod",
        "classmethod", "True", "False", "None",
    )
    safe: Dict[str, Any] = {name: getattr(real, name) for name in allowed if hasattr(real, name)}
    safe["__import__"] = _safe_import
    safe["__build_class__"] = real.__build_class__
    return safe


def execute_source(source: str, filename: str = "<strategy>") -> Dict[str, Any]:
    """静态检查后执行，返回模块命名空间。"""
    problems = check_source(source, filename)
    if problems:
        raise StrategyLoadError("策略未通过安全检查：\n" + "\n".join(problems[:10]),
                                detail="\n".join(problems))
    namespace: Dict[str, Any] = {
        "__name__": "tradesome_user_strategy",
        "__file__": filename,
        "__builtins__": _build_builtins(),
    }
    code = compile(source, filename, "exec")
    try:
        exec(code, namespace)  # noqa: S102 - 已做静态检查，见模块说明
    except Exception as exc:  # noqa: BLE001 - 用户代码什么错都可能抛
        raise StrategyLoadError("策略执行失败：%s" % exc, detail=repr(exc)) from exc
    return namespace


def extract_strategy(namespace: Dict[str, Any], filename: str) -> type:
    candidates = []
    for name, value in namespace.items():
        if isinstance(value, type) and issubclass(value, Strategy) and value is not Strategy:
            if not name.startswith("_"):
                candidates.append(value)
    if not candidates:
        raise StrategyLoadError("文件中没有找到 Strategy 子类：%s" % os.path.basename(filename))
    candidates.sort(key=lambda cls: 0 if cls.__name__ == "MyStrategy" else 1)
    cls = candidates[0]
    validate_class(cls)
    return cls


def validate_class(cls: type) -> None:
    meta = getattr(cls, "meta", None)
    if not isinstance(meta, StrategyMeta):
        raise StrategyLoadError("策略缺少 meta（StrategyMeta 实例）")
    import re

    if not re.match(KEY_PATTERN, meta.key):
        raise StrategyLoadError("策略 key 不合法：%r（小写字母开头，3–41 位）" % meta.key)
    if not meta.name.strip():
        raise StrategyLoadError("策略缺少名称")
    if meta.category not in ("趋势", "均值回归", "网格", "轮动", "情绪", "自定义"):
        raise StrategyLoadError("未知策略分类：%s" % meta.category)
    if meta.risk_level not in ("低", "中", "高"):
        raise StrategyLoadError("未知风险等级：%s" % meta.risk_level)


def load_user_strategy(path: str) -> type:
    if not os.path.exists(path):
        raise StrategyLoadError("策略文件不存在：%s" % path)
    with open(path, "r", encoding="utf-8") as handle:
        source = handle.read()
    namespace = execute_source(source, filename=os.path.basename(path))
    return extract_strategy(namespace, path)
