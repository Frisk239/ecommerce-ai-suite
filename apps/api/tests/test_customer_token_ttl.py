"""顾客令牌 TTL 集成测试（第 45 刀；真 PG，见 conftest 的 SUITE_TEST_DATABASE_URL）。

路线图对安全面变更的要求是「必有越权/过期钉测」，本文件逐个钉：

- 签发即带过期时刻（≈ now + customer_token_ttl_seconds）；
- 有效令牌照旧可用（控制组）；
- **过期 → 401，且与「令牌无效」逐字同文案**（不向调用方区分过期/无效——
  那是对攻击者的信息泄露）；
- `expires_at` 为 NULL 视为不可用（严格，不给静默放行的口子）；
- 三处校验点（发问 / 反馈 / 留联系方式）都受约束；
- 跨会话越权（拿 A 的令牌打 B）仍 401（既有契约回归）。
"""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
from fastapi.testclient import TestClient

from suite_api.models import ServiceSession

ApiFixture = tuple[TestClient, Path]

_URL_ENV = "SUITE_TEST_DATABASE_URL"


def _create_session(client: TestClient) -> dict:
    """走真签发端点（唯一一条：建会话有 IP 闸 5/60s，其他用例改用 `_seed_session`）。"""
    resp = client.post("/api/customer/sessions")
    assert resp.status_code == 201
    return resp.json()


def _seed_session(client: TestClient, token: str = "tok-ttl") -> int:
    """直接落一条顾客会话（绕过建会话 IP 闸）：TTL 用例要多次建会话，走端点会撞闸。
    签发时该写的字段照写——**过期时刻必须给**（鉴权把 NULL 视为不可用）。"""
    factory = client.app.state.session_factory
    with factory() as db:
        session = ServiceSession(
            status="active",
            customer_token=token,
            customer_token_expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        db.add(session)
        db.commit()
        return session.id


def _expires_at(url: str, session_id: int) -> str | None:
    with psycopg.connect(url, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT customer_token_expires_at::text FROM service_sessions WHERE id = %s",
            (session_id,),
        )
        row = cur.fetchone()
    return None if row is None else row[0]


def _set_expiry(url: str, session_id: int, *, seconds_from_now: int | None) -> None:
    """把令牌过期时刻挪到过去（负数）/未来（正数），或置 NULL（None）。"""
    with psycopg.connect(url, autocommit=True) as conn, conn.cursor() as cur:
        if seconds_from_now is None:
            cur.execute(
                "UPDATE service_sessions SET customer_token_expires_at = NULL WHERE id = %s",
                (session_id,),
            )
        else:
            cur.execute(
                "UPDATE service_sessions"
                " SET customer_token_expires_at = now() + make_interval(secs => %s)"
                " WHERE id = %s",
                (seconds_from_now, session_id),
            )


def _ask(client: TestClient, session_id: int, token: str, question: str = "怎么退货？"):
    return client.post(
        f"/api/customer/sessions/{session_id}/messages",
        json={"content": question},
        headers={"Authorization": f"Bearer {token}"},
    )


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------- 签发即带 TTL ----------


def test_session_creation_sets_token_expiry(api: ApiFixture) -> None:
    client, _ = api
    url = os.environ[_URL_ENV]
    created = _create_session(client)

    raw = _expires_at(url, created["session_id"])
    assert raw is not None, "签发即必须写过期时刻（NULL = 永不过期，正是本刀要消灭的）"
    # 默认 TTL 24h：只做量级断言（避免与 CI 时钟漂移较劲）
    with psycopg.connect(url, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT customer_token_expires_at > now() + interval '23 hours'"
            " AND customer_token_expires_at < now() + interval '25 hours'"
            " FROM service_sessions WHERE id = %s",
            (created["session_id"],),
        )
        assert cur.fetchone()[0] is True


def test_valid_token_still_works(api: ApiFixture) -> None:
    """控制组：TTL 不能把正常路径一起挡掉。"""
    client, _ = api
    session_id = _seed_session(client, token="tok-valid")
    assert _ask(client, session_id, "tok-valid").status_code == 200


# ---------- 过期（本刀的核心契约） ----------


def test_expired_token_is_401_with_same_copy_as_invalid(api: ApiFixture) -> None:
    """过期与无效**同 401 同文案**——不区分是对攻击者不泄露「令牌曾有效」。"""
    client, _ = api
    url = os.environ[_URL_ENV]
    session_id, token = _seed_session(client, token="tok-expire"), "tok-expire"

    # 无效令牌的控制组文案（拿另一条不存在会话的 id + 瞎令牌）
    invalid = _ask(client, 999_999, "not-a-real-token")

    _set_expiry(url, session_id, seconds_from_now=-60)  # 令牌已过期
    expired = _ask(client, session_id, token)

    assert invalid.status_code == 401
    assert expired.status_code == 401
    assert expired.json()["detail"] == invalid.json()["detail"]  # 逐字同文案
    assert expired.headers.get("WWW-Authenticate") == "Bearer"


def test_null_expiry_is_unusable(api: ApiFixture) -> None:
    """严格口径：NULL 不当作「永不过期」（迁移已回填，NULL 出现即异常）。"""
    client, _ = api
    url = os.environ[_URL_ENV]
    session_id = _seed_session(client, token="tok-null")
    _set_expiry(url, session_id, seconds_from_now=None)
    assert _ask(client, session_id, "tok-null").status_code == 401


def test_future_expiry_keeps_working(api: ApiFixture) -> None:
    """把过期时刻挪到未来仍在有效期内（边界另一侧，防「一律 401」蒙混过关）。"""
    client, _ = api
    url = os.environ[_URL_ENV]
    session_id = _seed_session(client, token="tok-future")
    _set_expiry(url, session_id, seconds_from_now=3600)
    assert _ask(client, session_id, "tok-future").status_code == 200


# ---------- 三处校验点都受约束 ----------


def test_feedback_and_handoff_endpoints_also_enforce_expiry(api: ApiFixture) -> None:
    """鉴权收敛到单一出处后，三处（发问/反馈/留联系方式）行为必须一致。"""
    client, _ = api
    url = os.environ[_URL_ENV]
    session_id, token = _seed_session(client, token="tok-2ep"), "tok-2ep"

    _set_expiry(url, session_id, seconds_from_now=-60)
    feedback = client.post(
        f"/api/customer/sessions/{session_id}/messages/1/feedback",
        json={"helpful": False},
        headers=_auth_header(token),
    )
    assert feedback.status_code == 401  # 闸在消息存在性之前（与既有闸序一致）

    handoff = client.post(
        f"/api/customer/sessions/{session_id}/handoff-tickets/1",
        json={"name": "张三", "note": "联系我"},
        headers=_auth_header(token),
    )
    assert handoff.status_code == 401

    # 三处同文案同响应头（裁决 4 不能只在发问端点成立）
    expired_copy = _ask(client, session_id, token).json()["detail"]
    assert feedback.json()["detail"] == expired_copy
    assert handoff.json()["detail"] == expired_copy
    assert feedback.headers.get("WWW-Authenticate") == "Bearer"
    assert handoff.headers.get("WWW-Authenticate") == "Bearer"


# ---------- 跨会话越权（既有契约回归） ----------


def test_token_of_other_session_is_rejected(api: ApiFixture) -> None:
    """A 会话的令牌打 B 会话 → 401（TTL 改造不得把这个口子放开）。"""
    client, _ = api
    _seed_session(client, token="tok-a")  # A 的令牌用来打 B
    b_id = _seed_session(client, token="tok-b")
    assert _ask(client, b_id, "tok-a").status_code == 401
