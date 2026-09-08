"""限流器单元测试（无 DB）：滑动窗口记账、滑出、key 隔离、Retry-After 取整、三闸组合、IP 口径两模式。"""

from types import SimpleNamespace

from starlette.requests import Request

from suite_api.routes.customer import client_ip
from suite_api.services.rate_limit import CustomerRateLimits, SlidingWindowLimiter
from suite_api.settings import Settings


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
    """三闸组合（路由按序分别调）：同会话第三问被会话闸拦；换会话绕过后由 IP 托底拦。"""
    clock = FakeClock()
    limits = CustomerRateLimits(
        session_ask_limit=2, ip_ask_limit=4, ip_create_limit=1, window_seconds=60.0, clock=clock
    )
    # 建会话闸：同 IP 第二次即拒
    assert limits.check_create("ip") is None
    assert limits.check_create("ip") is not None
    assert limits.check_create("other") is None  # 不同 IP 各账

    # 发问按路由序模拟（IP 闸先记、会话闸后记，两闸都过才到引擎）：
    # 同会话前两问双闸皆过；第三问 IP 闸仍过、会话闸拦
    for _ in range(2):
        assert limits.check_ask_ip("ip2") is None
        assert limits.check_ask_session("s1") is None
    assert limits.check_ask_ip("ip2") is None
    assert limits.check_ask_session("s1") is not None
    # 换会话 s2：会话闸空账可过，IP 托底在第四条累计处拦截
    assert limits.check_ask_ip("ip2") is None
    assert limits.check_ask_session("s2") is None
    assert limits.check_ask_ip("ip2") is not None


def test_session_gate_rejection_keeps_counting_ip() -> None:
    """IP 闸先于会话闸：被会话闸拒的请求已在 IP 闸记账——换会话刷不过 IP 托底。"""
    clock = FakeClock()
    limits = CustomerRateLimits(
        session_ask_limit=1, ip_ask_limit=3, ip_create_limit=5, window_seconds=60.0, clock=clock
    )
    assert limits.check_ask_ip("ip") is None
    assert limits.check_ask_session("s1") is None
    # s1 第二问：IP 闸先记账、会话闸拒（路由在此 429）
    assert limits.check_ask_ip("ip") is None
    assert limits.check_ask_session("s1") is not None
    # 换会话绕过会话闸，但 IP 账已累计到 3：下一条在 IP 闸被拦
    assert limits.check_ask_ip("ip") is None
    assert limits.check_ask_session("s2") is None
    assert limits.check_ask_ip("ip") is not None


# ---------- client_ip 两模式（IP 闸 key 口径；无 DB） ----------


def _request(xff: str | None, peer: str, *, trust_proxy: bool) -> Request:
    headers = [(b"x-forwarded-for", xff.encode())] if xff is not None else []
    fake_app = SimpleNamespace(state=SimpleNamespace(settings=Settings(customer_trust_proxy=trust_proxy)))
    scope = {"type": "http", "headers": headers, "client": (peer, 50000), "app": fake_app}
    return Request(scope)


def test_client_ip_direct_mode_ignores_xff() -> None:
    """直连默认（fail-closed）：XFF 完全忽略——多跳/单跳自报头都换不了 IP 闸 key。"""
    req = _request("203.0.113.7, 10.0.0.1", peer="192.0.2.1", trust_proxy=False)
    assert client_ip(req) == "192.0.2.1"


def test_client_ip_trust_mode_uses_first_hop() -> None:
    """反代模式：信 XFF 第一跳（其余跳丢弃）；无头时回落 TCP 对端。"""
    req = _request("203.0.113.7, 10.0.0.1", peer="192.0.2.1", trust_proxy=True)
    assert client_ip(req) == "203.0.113.7"
    assert client_ip(_request(None, peer="192.0.2.1", trust_proxy=True)) == "192.0.2.1"
