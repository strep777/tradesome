"""策略服务：策略库、参数、用户策略的保存与沙箱校验。"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

from ...core.errors import StrategyError, StrategyLoadError, ValidationError
from ...core.logging import get_logger
from ...security.validation import safe_join, validate_filename
from ...strategy.base import Strategy
from ...strategy.profile import (ProfileSpec, make_profile_class, make_rule_class,
                                 profile_from_dict, validate_profile)
from ...strategy.sandbox import check_source

LOGGER = get_logger("app.strategy")

TEMPLATE = '''"""自定义策略模板：复制后改名即可。

规则只有三条：
1. 必须定义 meta，key 用小写字母/数字/下划线；
2. 必须继承 Strategy，实现 on_bar（或 on_bars）；
3. 只能使用白名单模块，不能读写文件、不能联网。
"""

from tradesome.strategy.base import ParamSpec, Strategy, StrategyMeta


class MyStrategy(Strategy):
    meta = StrategyMeta(
        key="my_strategy",
        name="我的均线策略",
        category="趋势",
        risk_level="中",
        description="收盘价上穿均线买入，下穿卖出。",
        principle="趋势跟随：价格站上均线说明多头占优。",
        works_when="单边趋势明显的行情。",
        fails_when="横盘震荡时会被反复止损。",
    )

    def param_specs(self):
        return [
            ParamSpec("period", "均线周期", "int", 20, 5, 120, 1, help="越大越迟钝"),
            ParamSpec("weight", "单标的仓位", "float", 0.20, 0.05, 0.30, 0.05),
        ]

    def on_bar(self, bar):
        p = self.p
        closes = self.history(bar.symbol, p["period"] + 2)
        if len(closes) < p["period"] + 1:
            return
        average = sum(closes[-p["period"]:]) / p["period"]
        previous = sum(closes[-p["period"] - 1:-1]) / p["period"]
        position = self.position_of(bar.symbol)
        if previous <= average and bar.close > average and position is None:
            self.buy(bar.symbol, weight=p["weight"], reason="上穿均线")
        elif position is not None and bar.close < average:
            self.close(bar.symbol, reason="跌破均线")
'''


class StrategyService:
    def __init__(self, container) -> None:
        self.c = container
        self.registry = container.strategies
        self._profiles: Dict[str, ProfileSpec] = {}
        self.load_profiles()

    # ---------------------------------------------------------- 查询

    def metas(self) -> List[dict]:
        rows = []
        for meta in self.registry.metas():
            row = meta.to_dict()
            row["is_user"] = self.registry.is_user(meta.key)
            row["is_profile"] = self.registry.is_profile(meta.key)
            spec = self._profiles.get(meta.key)
            row["profile_id"] = spec.profile_id if spec else ""
            row["template"] = spec.template if spec else ""
            row["parameters"] = dict(spec.params) if spec else {}
            row["overlays"] = spec.overlay_values() if spec else {}
            row["kind"] = spec.kind if spec else "template"
            row["rules"] = dict(spec.rules) if spec else {}
            row["param_count"] = len(self.registry.params(meta.key))
            rows.append(row)
        rows.sort(key=lambda r: (0 if r["is_profile"] else 1,
                                 r["category"], r["name"]))
        return rows

    def meta(self, key: str) -> dict:
        meta = self.registry.meta(key)
        data = meta.to_dict()
        data["is_user"] = self.registry.is_user(key)
        data["is_profile"] = self.registry.is_profile(key)
        spec = self._profiles.get(key)
        data["profile_id"] = spec.profile_id if spec else ""
        data["template"] = spec.template if spec else ""
        data["parameters"] = dict(spec.params) if spec else {}
        data["overlays"] = spec.overlay_values() if spec else {}
        data["kind"] = spec.kind if spec else "template"
        data["rules"] = dict(spec.rules) if spec else {}
        data["param_count"] = len(self.registry.params(key))
        return data

    def params(self, key: str) -> List[dict]:
        return [spec.to_dict() for spec in self.registry.params(key)]

    def defaults(self, key: str) -> Dict[str, object]:
        spec = self._profiles.get(key)
        if spec is not None:
            if spec.kind == "rules":
                return {"weight": float((spec.rules or {}).get("weight") or 0.2)}
            values = dict(spec.params)
            values.update(spec.overlay_values())
            return values
        return self.registry.default_params(key)

    def create(self, key: str, params: Optional[Dict[str, object]] = None) -> Strategy:
        return self.registry.create(key, params or {})

    def errors(self) -> List[str]:
        return list(self.registry.errors)

    # ---------------------------------------------------------- 策略工坊

    def load_profiles(self) -> List[str]:
        """启动时把数据库里的档案注册成策略；坏档案跳过并记日志。"""
        problems: List[str] = []
        try:
            rows = self.c.repos.strategy_profiles.load_specs()
        except Exception as exc:  # noqa: BLE001 - 数据库损坏不应阻止启动
            LOGGER.warning("读取策略档案失败：%s", exc)
            return [str(exc)]
        for data in rows:
            try:
                spec = profile_from_dict(data)
                self._register_spec(spec)
            except Exception as exc:  # noqa: BLE001
                message = "%s：%s" % (data.get("name", "?"), exc)
                problems.append(message)
                LOGGER.warning("策略档案加载失败 %s", message)
        return problems

    def profiles(self) -> List[dict]:
        return [spec.to_dict() for spec in sorted(
            self._profiles.values(), key=lambda s: s.updated_at, reverse=True)]

    def template_choices(self) -> List[dict]:
        """可作为模板的内置策略（不含自定义策略）。"""
        return [m.to_dict() for m in self.registry.metas()
                if not self.registry.is_profile(m.key)]

    def profile_spec(self, key: str) -> Optional[dict]:
        spec = self._profiles.get(key)
        return spec.to_dict() if spec else None

    def validate_profile_payload(self, payload: Dict[str, object],
                                 profile_id: Optional[str] = None) -> Dict[str, object]:
        spec = validate_profile(payload, self.registry, profile_id)
        return spec.to_dict()

    def save_profile(self, payload: Dict[str, object],
                     profile_id: Optional[str] = None) -> dict:
        spec = validate_profile(payload, self.registry, profile_id)
        if profile_id and not any(p.profile_id == profile_id
                                  for p in self._profiles.values()):
            raise ValidationError("要修改的策略不存在：%s" % profile_id)
        old = self._profiles.get(spec.key)
        if old is not None:
            spec.created_at = old.created_at or spec.created_at
        self._register_spec(spec)
        self.c.repos.strategy_profiles.upsert(spec.to_dict())
        kind_text = "自由组合" if spec.kind == "rules" else "模板 %s" % spec.template
        self.c.repos.audit.write(
            "strategy", "profile_save",
            detail="保存策略工坊档案 %s（%s）" % (spec.name, kind_text))
        LOGGER.info("已保存策略档案 %s（%s）", spec.name, spec.key)
        return spec.to_dict()

    def delete_profile(self, profile_id: str) -> bool:
        spec = next((p for p in self._profiles.values()
                     if p.profile_id == profile_id), None)
        if spec is None:
            raise ValidationError("策略不存在：%s" % profile_id)
        self._profiles.pop(spec.key, None)
        self.registry.unregister_profile(spec.key)
        removed = self.c.repos.strategy_profiles.delete(profile_id)
        self.c.repos.audit.write("strategy", "profile_delete",
                                 detail="删除策略工坊档案 %s" % spec.name)
        return removed

    def _register_spec(self, spec: ProfileSpec) -> None:
        if spec.kind == "rules":
            cls = make_rule_class(spec)
        else:
            if spec.template not in self.registry or self.registry.is_profile(spec.template):
                raise ValidationError("模板不存在或已删除：%s" % spec.template)
            cls = make_profile_class(spec, self.registry._class(spec.template))  # noqa: SLF001
        old_key = spec.key
        if old_key in self.registry:
            self.registry.unregister_profile(old_key)
        self.registry.register_profile(cls)
        self._profiles[old_key] = spec

    # ---------------------------------------------------------- 用户策略

    def template(self) -> str:
        return TEMPLATE

    def user_source(self, key: str) -> str:
        if not self.registry.is_user(key):
            raise StrategyError("内置策略不提供源码编辑：%s" % key)
        path = self._path_of(key)
        if not path.exists():
            raise StrategyError("找不到策略文件：%s" % key)
        return path.read_text(encoding="utf-8")

    def save_user(self, key: str, code: str, filename: Optional[str] = None) -> str:
        key = (key or "").strip().lower()
        if not key or not key.replace("_", "").isalnum():
            raise StrategyLoadError("策略 key 只能包含小写字母、数字与下划线")
        problems = check_source(code)
        if problems:
            raise StrategyLoadError("策略未通过安全检查：\n- " + "\n- ".join(problems[:6]))
        name = filename or (key + ".py")
        validate_filename(name, ".py")
        target = safe_join(self.c.paths.strategies, name)
        fd, temp = tempfile.mkstemp(dir=str(self.c.paths.strategies), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(code)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, target)
        except Exception:
            try:
                os.unlink(temp)
            except OSError:
                pass
            raise
        errors = self.registry.reload_user()
        if key not in self.registry.keys():
            detail = "\n".join(errors[:6]) if errors else "未找到 Strategy 子类"
            raise StrategyLoadError("策略加载失败：\n" + detail)
        self.c.repos.audit.write("strategy", "save", detail="保存策略 %s" % key)
        LOGGER.info("已保存用户策略 %s", key)
        return key

    def delete_user(self, key: str) -> bool:
        removed = self.registry.remove_user(key, delete_file=True)
        if removed:
            self.c.repos.audit.write("strategy", "delete", detail="删除策略 %s" % key)
        return removed

    def reload(self) -> List[str]:
        return self.registry.reload_user()

    def _path_of(self, key: str) -> Path:
        for path in sorted(self.c.paths.strategies.glob("*.py")):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            if 'key="%s"' % key in text or "key='%s'" % key in text:
                return path
        return self.c.paths.strategies / (key + ".py")
