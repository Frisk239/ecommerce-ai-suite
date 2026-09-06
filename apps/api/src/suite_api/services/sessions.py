"""会话 cookie：HMAC 签名的无状态凭证（0016 最小版登录，防伪造）。

格式：``{operator_id}.{expires_ts}.{hex_sig}``，sig = HMAC-SHA256(secret,
``{operator_id}.{expires_ts}``)。验证时恒定时间比较 + 过期检查；
不依赖 sessions 表，换 SESSION_SECRET 即全体失效。
"""

import hashlib
import hmac
import time

_DELIMITER = "."
_ALPHABET_OK = set("0123456789")

SESSION_COOKIE_NAME = "suite_session"


def _signature(secret: str, payload: str) -> str:
    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def issue_session_value(operator_id: int, secret: str, ttl_seconds: int) -> str:
    expires_ts = int(time.time()) + ttl_seconds
    payload = f"{operator_id}{_DELIMITER}{expires_ts}"
    return f"{payload}{_DELIMITER}{_signature(secret, payload)}"


def verify_session_value(value: str | None, secret: str) -> int | None:
    """验签通过且未过期时返回 operator_id，否则 None（不抛、不区分失败原因）。"""
    if not value:
        return None
    parts = value.split(_DELIMITER)
    if len(parts) != 3:
        return None
    operator_id_raw, expires_raw, signature = parts
    if not (operator_id_raw and expires_raw and signature):
        return None
    if not set(operator_id_raw) <= _ALPHABET_OK or not set(expires_raw) <= _ALPHABET_OK:
        return None
    expected = _signature(secret, f"{operator_id_raw}{_DELIMITER}{expires_raw}")
    if not hmac.compare_digest(expected, signature):
        return None
    try:
        expires_ts = int(expires_raw)
        operator_id = int(operator_id_raw)
    except ValueError:
        return None
    if expires_ts < time.time():
        return None
    return operator_id
