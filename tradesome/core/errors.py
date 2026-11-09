"""异常体系。

约定：
1. 所有业务异常继承 TradeSomeError；
2. 消息给用户看（中文、含下一步建议），技术细节放 detail；
3. str(e) 不包含堆栈；to_user_text() 不包含英文类名。
"""

from __future__ import annotations

from typing import Optional


class TradeSomeError(Exception):
    """所有业务异常的基类。"""

    code = "E_UNKNOWN"
    default_hint = ""

    def __init__(self, message: str, *, detail: str = "", hint: Optional[str] = None) -> None:
        super().__init__(message)
        self.message = str(message)
        self.detail = str(detail)
        self.hint = self.default_hint if hint is None else str(hint)

    def __str__(self) -> str:
        return self.message

    def to_user_text(self) -> str:
        parts = [self.message]
        if self.hint:
            parts.append("建议：%s" % self.hint)
        return "\n".join(parts)

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "detail": self.detail,
            "hint": self.hint,
        }


class ValidationError(TradeSomeError):
    code = "E_VALIDATION"
    default_hint = "请检查输入的数值与格式。"


class ConfigError(TradeSomeError):
    code = "E_CONFIG"
    default_hint = "可在设置中恢复默认值，或删除 var/settings.json 后重启。"


class DataError(TradeSomeError):
    code = "E_DATA"
    default_hint = "请在数据中心导入行情数据，或生成示例数据。"


class DataQualityError(DataError):
    code = "E_DATA_QUALITY"

    def __init__(self, message: str, issues=None, **kwargs) -> None:
        super().__init__(message, **kwargs)
        self.issues = list(issues or [])


class StrategyError(TradeSomeError):
    code = "E_STRATEGY"
    default_hint = "请检查策略参数是否在允许范围内。"


class StrategyLoadError(StrategyError):
    code = "E_STRATEGY_LOAD"
    default_hint = "请检查策略文件语法与是否使用了被禁止的模块。"


class BacktestError(TradeSomeError):
    code = "E_BACKTEST"
    default_hint = "请缩短回测区间或减少标的数量后重试。"


class ExecutionError(TradeSomeError):
    code = "E_EXECUTION"
    default_hint = "请稍后重试；若持续失败请查看日志。"


class GatewayError(TradeSomeError):
    """网关通信异常。"""

    code = "E_GATEWAY"
    default_hint = "请确认本机 miniQMT 已启动并登录，目录、账号与账号类型正确。"


class GatewayAuthError(GatewayError):
    code = "E_GATEWAY_AUTH"
    default_hint = "资金账号未订阅或账号类型不符：请在 QMT 客户端检查后重试。"


class GatewayTimeout(GatewayError):
    code = "E_GATEWAY_TIMEOUT"
    default_hint = "柜台没有在 10 秒内响应；请检查网络与券商客户端是否在线。"


class GatewayDisconnected(GatewayError):
    code = "E_GATEWAY_DISCONNECTED"
    default_hint = "连接已断开，正在自动重连；恢复前请不要重复下单。"


class TradingError(TradeSomeError):
    """实盘交易被拒绝或无法执行。"""

    code = "E_TRADING"
    default_hint = "请检查委托价格、数量与交易时段；必要时联系券商。"


class PersistenceError(TradeSomeError):
    code = "E_DB"
    default_hint = "请确认程序目录可写，或用备份文件恢复 var/tradesome.db。"


class SecurityError(TradeSomeError):
    code = "E_SECURITY"
    default_hint = "请确认使用的是当前 Windows 用户账户。"


class RiskRejected(TradeSomeError):
    """风控拒绝（通常不抛出，而是作为订单拒绝原因返回）。"""

    code = "E_RISK"
    default_hint = "可在风控页调整阈值，或减小下单数量。"

    def __init__(self, message: str, *, rule_id: str = "", detail: str = "",
                 hint: Optional[str] = None) -> None:
        super().__init__(message, detail=detail, hint=hint)
        self.rule_id = rule_id
