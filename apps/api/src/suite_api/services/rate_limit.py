"""顾客通道限流（ADR 0033：按会话限流、每 IP 托底；不做无令牌狂刷）。

进程内滑动窗口：单 uvicorn 进程=compose 现状（ADR 0016 单店口径），多副本
分布式限流属部署演进，本刀不做中间件依赖。三道闸（spec Must 4）：

- 会话发问 ≤10/60s：令牌本身就是会话级身份（0021），同会话狂刷在此拦；
- IP 发问 ≤30/60s：托底——换会话（重签令牌）刷不过这道；
- IP 建会话 ≤5/60s：拦令牌工厂狂刷。

时钟与阈值可注入（测试窗口调小/假时钟推进）；命中即记一个时间戳，超限返回
剩余等待秒（Retry-After 头取整、至少 1s）。线程安全：FastAPI 同步路由跑
threadpool、async 路由跑事件循环，deque 剪枝与追加加一把 threading.Lock。
"""

import math
import threading
import time
from collections import deque
from collections.abc import Callable


class SlidingWindowLimiter:
    """单阈值滑动窗口：key -> 时间戳队列；check 通过即记账（check 与记录一体）。"""

    def __init__(
        self,
        limit: int,
        window_seconds: float,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limit = limit
        self._window = window_seconds
        self._clock = clock
        self._events: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> int | None:
        """通过返回 None 并记账；超限返回剩余等待秒（Retry-After 口径，向上取整）。"""
        now = self._clock()
        with self._lock:
            events = self._events.setdefault(key, deque())
            cutoff = now - self._window
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= self._limit:
                retry_after = self._window - (now - events[0])
                return max(1, math.ceil(retry_after))
            events.append(now)
            return None


# 默认阈值（spec Must 4）：窗口 60s；测试可用小阈值构造替换（app.state 注入）
SESSION_ASK_LIMIT = 10
IP_ASK_LIMIT = 30
IP_CREATE_LIMIT = 5
WINDOW_SECONDS = 60.0


class CustomerRateLimits:
    """顾客通道三道闸的组合（挂在 app.state，测试可整体替换或注入时钟）。"""

    def __init__(
        self,
        *,
        session_ask_limit: int = SESSION_ASK_LIMIT,
        ip_ask_limit: int = IP_ASK_LIMIT,
        ip_create_limit: int = IP_CREATE_LIMIT,
        window_seconds: float = WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._session_ask = SlidingWindowLimiter(session_ask_limit, window_seconds, clock=clock)
        self._ip_ask = SlidingWindowLimiter(ip_ask_limit, window_seconds, clock=clock)
        self._ip_create = SlidingWindowLimiter(ip_create_limit, window_seconds, clock=clock)

    def check_ask(self, session_key: str, ip: str) -> int | None:
        """发问双闸：会话级先查（更具体），未过再查 IP 托底；返回首个命中的等待秒。"""
        return self._session_ask.check(session_key) or self._ip_ask.check(ip)

    def check_create(self, ip: str) -> int | None:
        """建会话单闸：IP 托底（签发本身无鉴权，只按来源拦）。"""
        return self._ip_create.check(ip)
