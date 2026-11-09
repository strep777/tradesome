"""策略注册表：内置策略 + 用户策略。

界面只跟注册表打交道：列出 meta、取参数定义、按 key 创建实例。
新增内置策略只需在 builtin/ 下放一个文件；用户策略放在 var/strategies/。
"""

from __future__ import annotations

import os
import pkgutil
from typing import Dict, List, Optional, Type

from ..core.errors import StrategyError, StrategyLoadError
from ..core.logging import get_logger
from .base import ParamSpec, Strategy, StrategyMeta, coerce_params, default_params
from .sandbox import load_user_strategy, validate_class

LOGGER = get_logger("strategy.registry")


class StrategyRegistry:
    def __init__(self, user_dir: Optional[str] = None, autodiscover: bool = True) -> None:
        self.user_dir = user_dir
        self._classes: Dict[str, Type[Strategy]] = {}
        self._user_keys: Dict[str, str] = {}
        self._user_files: Dict[str, str] = {}
        self._profile_keys: set = set()
        self._errors: List[str] = []
        if autodiscover:
            self.discover()
            # 用户目录里的策略也要在启动时自动加载，否则"保存后重启就消失"。
            self.reload_user()

    # ---------------------------------------------------------- 发现

    def discover(self) -> int:
        from . import builtin as builtin_pkg

        count = 0
        for info in pkgutil.iter_modules(builtin_pkg.__path__):
            if info.name.startswith("_"):
                continue
            module = __import__("tradesome.strategy.builtin.%s" % info.name,
                                fromlist=["*"])
            for value in vars(module).values():
                if (isinstance(value, type) and issubclass(value, Strategy)
                        and value is not Strategy and value.__module__ == module.__name__):
                    try:
                        self.register(value)
                        count += 1
                    except StrategyError as exc:
                        self._errors.append("%s: %s" % (info.name, exc))
        return count

    def register(self, cls: Type[Strategy], user: bool = False) -> str:
        validate_class(cls)
        key = cls.meta.key
        if key in self._classes and not user:
            raise StrategyError("策略 key 重复：%s" % key)
        if key in self._classes and user:
            raise StrategyError("用户策略 key 与已有策略冲突：%s" % key)
        self._classes[key] = cls
        if user:
            self._user_keys[key] = cls.__module__
        return key

    # ---------------------------------------------------------- 查询

    def keys(self) -> List[str]:
        return sorted(self._classes)

    def metas(self) -> List[StrategyMeta]:
        return [self._classes[key].meta for key in self.keys()]

    def meta(self, key: str) -> StrategyMeta:
        return self._class(key).meta

    def params(self, key: str) -> List[ParamSpec]:
        cls = self._class(key)
        if key in self._profile_keys:
            return list(cls().param_specs())
        # param_specs 是实例方法；这里不跑 __init__，避免构造副作用。
        return list(cls.__new__(cls).param_specs())

    def default_params(self, key: str) -> Dict[str, object]:
        return default_params(self.params(key))

    def is_user(self, key: str) -> bool:
        return key in self._user_keys

    # ---------------------------------------------------------- 策略工坊

    def register_profile(self, cls: Type[Strategy]) -> str:
        """注册由策略档案动态生成的类；与内置/用户策略共用一套创建接口。"""
        validate_class(cls)
        key = cls.meta.key
        if key in self._classes:
            raise StrategyError("策略 key 重复：%s" % key)
        self._classes[key] = cls
        self._profile_keys.add(key)
        return key

    def is_profile(self, key: str) -> bool:
        return key in self._profile_keys

    def unregister_profile(self, key: str) -> bool:
        if key not in self._profile_keys:
            return False
        self._classes.pop(key, None)
        self._profile_keys.discard(key)
        return True

    def profile_keys(self) -> List[str]:
        return sorted(self._profile_keys)

    def __contains__(self, key: str) -> bool:
        return key in self._classes

    def __len__(self) -> int:
        return len(self._classes)

    def errors(self) -> List[str]:
        return list(self._errors)

    def _class(self, key: str) -> Type[Strategy]:
        if key not in self._classes:
            raise StrategyError("未知策略：%s（可用：%s）" % (key, "、".join(self.keys())))
        return self._classes[key]

    # ---------------------------------------------------------- 创建

    def create(self, key: str, params: Optional[Dict[str, object]] = None) -> Strategy:
        cls = self._class(key)
        if key in self._profile_keys:
            # 档案里的参数就是用户保存的设置；只有显式传入的才覆盖，
            # 不能用参数声明的默认值把保存值冲掉。
            instance = cls()
            if params:
                instance.configure(params)
            return instance
        instance = cls()
        values = coerce_params(instance.param_specs(), params or {})
        instance.configure(values)
        return instance

    # ---------------------------------------------------------- 用户策略

    def reload_user(self) -> List[str]:
        """重新扫描用户目录；返回错误列表（成功为空）。"""
        for key in list(self._user_keys):
            self._classes.pop(key, None)
        self._user_keys.clear()
        self._user_files.clear()
        self._errors.clear()
        if not self.user_dir or not os.path.isdir(self.user_dir):
            return []
        for name in sorted(os.listdir(self.user_dir)):
            if not name.endswith(".py") or name.startswith("_"):
                continue
            path = os.path.join(self.user_dir, name)
            try:
                cls = load_user_strategy(path)
                key = self.register(cls, user=True)
                self._user_files[key] = path
            except (StrategyLoadError, StrategyError) as exc:
                message = "%s：%s" % (name, exc)
                self._errors.append(message)
                LOGGER.warning("用户策略加载失败 %s", message)
        return list(self._errors)

    def remove_user(self, key: str, delete_file: bool = True) -> None:
        cls = self._classes.get(key)
        if cls is None or key not in self._user_keys:
            raise StrategyError("不是用户策略：%s" % key)
        # 用户策略的模块名是加载时生成的固定名，不能靠 __module__ 反推文件，
        # 这里用加载时记录的真实路径。
        path = self._user_files.pop(key, None) or \
            os.path.join(self.user_dir or "", cls.__module__ + ".py")
        self._classes.pop(key, None)
        self._user_keys.pop(key, None)
        if delete_file and path and os.path.exists(path):
            os.remove(path)


_default_registry: Optional[StrategyRegistry] = None


def default_registry(user_dir: Optional[str] = None) -> StrategyRegistry:
    global _default_registry
    if _default_registry is None:
        _default_registry = StrategyRegistry(user_dir=user_dir)
        if user_dir:
            _default_registry.reload_user()
    return _default_registry
