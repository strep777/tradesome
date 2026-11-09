"""进程内事件总线：同步发布、异常隔离、环形缓存最近事件。

设计取舍：
- 同步发布：调用线程即回调线程。UI 订阅者必须自己 after() 回主线程，
  这样总线本身无需知道 Tk 的存在（保持 core 纯净）。
- 异常隔离：一个订阅者抛错不影响其他订阅者，但会写日志。
- 环形缓存：只保留最近 N 条，供"状态栏/诊断"回看，不做持久化。
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Any, Callable, Deque, Dict, List, Optional

from .events import Event

Handler = Callable[[Event], None]
ALL = "*"

_log = logging.getLogger(__name__)


class EventBus:
    """线程安全的最小事件总线。"""

    def __init__(self, history: int = 300) -> None:
        self._lock = threading.RLock()
        self._handlers: Dict[str, List[Handler]] = {}
        self._recent: Deque[Event] = deque(maxlen=max(1, int(history)))
        self._seq = 0
        self._publishing = 0

    # ------------------------------------------------------------ 订阅

    def subscribe(self, topic: str, handler: Handler) -> Callable[[], None]:
        """订阅；返回退订函数（重复调用安全）。"""
        if not callable(handler):
            raise TypeError("handler 必须可调用")
        key = topic or ALL
        with self._lock:
            self._handlers.setdefault(key, []).append(handler)
            unsubscribed = False

        def unsubscribe() -> None:
            nonlocal unsubscribed
            with self._lock:
                if unsubscribed:
                    return
                unsubscribed = True
                items = self._handlers.get(key)
                if not items:
                    return
                try:
                    items.remove(handler)
                except ValueError:
                    pass
                if not items:
                    self._handlers.pop(key, None)

        return unsubscribe

    def unsubscribe_all(self) -> None:
        with self._lock:
            self._handlers.clear()

    def subscriber_count(self, topic: Optional[str] = None) -> int:
        with self._lock:
            if topic is None:
                return sum(len(v) for v in self._handlers.values())
            return len(self._handlers.get(topic, ()))

    # ------------------------------------------------------------ 发布

    def publish(self, topic: str, payload: Optional[Dict[str, Any]] = None) -> Event:
        """发布事件；返回已发布的事件（含 seq）。"""
        if not topic:
            raise ValueError("topic 不能为空")
        with self._lock:
            self._seq += 1
            event = Event(topic=topic, payload=dict(payload or {}), seq=self._seq)
            self._recent.append(event)
            handlers = list(self._handlers.get(topic, ())) + list(self._handlers.get(ALL, ()))
            self._publishing += 1
        try:
            for handler in handlers:
                try:
                    handler(event)
                except Exception:  # noqa: BLE001 - 订阅者异常不得中断发布
                    _log.exception("事件订阅者执行失败：topic=%s handler=%r", topic, handler)
        finally:
            with self._lock:
                self._publishing -= 1
        return event

    def emit(self, topic: str, **payload: Any) -> Event:
        """publish 的关键字快捷方式。"""
        return self.publish(topic, payload)

    # ------------------------------------------------------------ 查询

    def recent(self, limit: int = 20, topic: Optional[str] = None) -> List[Event]:
        with self._lock:
            items = list(self._recent)
        if topic:
            items = [e for e in items if e.topic == topic]
        if limit and limit > 0:
            items = items[-limit:]
        return items

    @property
    def last_seq(self) -> int:
        with self._lock:
            return self._seq

    @property
    def topic_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for event in self.recent(limit=0):
            counts[event.topic] = counts.get(event.topic, 0) + 1
        return counts
