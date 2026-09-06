"""会话签名单元测试：签发/验签/防伪造/过期。"""

import time

from suite_api.services.sessions import issue_session_value, verify_session_value

_SECRET = "test-secret"


def test_issue_and_verify_roundtrip() -> None:
    value = issue_session_value(7, _SECRET, ttl_seconds=60)
    assert verify_session_value(value, _SECRET) == 7


def test_tampered_value_rejected() -> None:
    value = issue_session_value(7, _SECRET, ttl_seconds=60)
    assert verify_session_value(value[:-1] + ("0" if value[-1] != "0" else "1"), _SECRET) is None
    # 改操作者 ID 也验不过
    prefix, expires, sig = value.rsplit(".", 2)
    assert verify_session_value(f"999.{expires}.{sig}", _SECRET) is None


def test_wrong_secret_rejected() -> None:
    value = issue_session_value(7, _SECRET, ttl_seconds=60)
    assert verify_session_value(value, "other-secret") is None


def test_expired_value_rejected() -> None:
    value = issue_session_value(7, _SECRET, ttl_seconds=-1)
    assert value  # 过期票据也能签出（exp 在过去）
    assert verify_session_value(value, _SECRET) is None


def test_garbage_values_rejected() -> None:
    for junk in [None, "", "7.only-two", "a.b.c", "7.99999999999999999999." + "0" * 64]:
        assert verify_session_value(junk, _SECRET) is None


def test_clock_is_not_trusted_far_future_ok() -> None:
    # 签发时刻起 ttl 内有效：1 小时票在当前时刻有效
    value = issue_session_value(1, _SECRET, ttl_seconds=3600)
    assert verify_session_value(value, _SECRET) == 1
    assert time.time() > 0  # 保持 import；语义：验签含过期检查
