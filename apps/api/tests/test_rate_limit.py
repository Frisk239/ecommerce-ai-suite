"""限流器单元测试（无 DB）：滑动窗口记账、滑出、key 隔离、Retry-After 取整、三闸组合。"""

from suite_api.services.rate_limit import CustomerRateLimits, SlidingWindowLimiter


class FakeClock:
    """可推进的单调时钟（限流器构造注入；不碰系统时间）。"""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_allows_up_to_limit_then_rejects() -> None:
    clock = FakeClock()
    limiter = SlidingWindowLimiter(3, 60.0, clock=clock)
    assert limiter.check("k") is None
    assert limiter.check("k") is None
    assert limiter.check("k") is None
    # 三条都记在 t0：第四条超限，需等满整窗（Retry-After 口径，取整）
    assert limiter.check("k") == 60


def test_window_slides_old_events_out() -> None:
    clock = FakeClock()
    limiter = SlidingWindowLimiter(2, 60.0, clock=clock)
    assert limiter.check("k") is None
    clock.advance(30.0)
    assert limiter.check("k") is None
    assert limiter.check("k") is not None  # 窗内两条（t0/t30）
    clock.advance(31.0)  # t61：t0 已滑出窗（<= cutoff 剪枝，含边界）
    assert limiter.check("k") is None


def test_keys_are_isolated() -> None:
    limiter = SlidingWindowLimiter(1, 60.0, clock=FakeClock())
    assert limiter.check("a") is None
    assert limiter.check("a") is not None
    assert limiter.check("b") is None  # 各 key 各账


def test_retry_after_ceils_and_never_zero() -> None:
    clock = FakeClock()
    limiter = SlidingWindowLimiter(1, 60.0, clock=clock)
    assert limiter.check("k") is None
    clock.advance(0.5)
    assert limiter.check("k") == 60  # 59.5 -> ceil 60
    clock.advance(10.0)
    assert limiter.check("k") == 50  # 49.5 -> ceil 50


def test_rejected_check_does_not_extend_window() -> None:
    """超限的 check 不记账：不会靠狂刷被拒请求把窗口无限续期。"""
    clock = FakeClock()
    limiter = SlidingWindowLimiter(1, 60.0, clock=clock)
    limiter.check("k")
    clock.advance(10.0)
    for _ in range(5):
        assert limiter.check("k") == 50  # 剩余等待只随时间流逝减少
    clock.advance(50.0)
    assert limiter.check("k") is None


def test_customer_rate_limits_combo() -> None:
    """三闸组合：会话闸先拦（不记 IP 账）；换会话绕过会话闸后由 IP 托底拦。"""
    clock = FakeClock()
    limits = CustomerRateLimits(
        session_ask_limit=2, ip_ask_limit=3, ip_create_limit=1, window_seconds=60.0, clock=clock
    )
    # 建会话闸：同 IP 第二次即拒
    assert limits.check_create("ip") is None
    assert limits.check_create("ip") is not None
    assert limits.check_create("other") is None  # 不同 IP 各账

    # 会话闸：同会话第三问被拦（该次不落 IP 账）
    assert limits.check_ask("s1", "ip2") is None
    assert limits.check_ask("s1", "ip2") is None
    assert limits.check_ask("s1", "ip2") is not None
    # 换会话（s2/s3 各一问）绕过会话闸，IP 托底在第三条累计处拦截
    assert limits.check_ask("s2", "ip2") is None
    assert limits.check_ask("s3", "ip2") is not None
