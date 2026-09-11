"""第 80 刀：顾客「结束会话」+ 会话终态语义集成测试（真 PG）。

覆盖：
- end 端点：active -> 200 且库内 status=ended + closed_at 落值（DB 钟）；重复
  end -> 200 幂等（closed_at 不变）；registered -> 409；无/错令牌 -> 401。
- 闸序：IP 闸先于 401（坏令牌超 IP 闸也 429，狂刷不碰库）。
- 终态语义「关对话流，不关善后通道」：ended 下发问 409（detail 含「已结束」），
  评分/反馈/联系方式全部放行；registered 下评分仍 409（既有行为钉子回归）。
- 操作者回流：ended 会话可 register 成 registered，且 closed_at 保持顾客结束
  时刻（不被回流时间改写）。
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from suite_api.models import HandoffTicket, ServiceMessage, ServiceSession
from suite_api.services.rate_limit import CustomerRateLimits

ApiFixture = tuple[TestClient, Path]


def _make_session(
    client: TestClient,
    token: str,
    *,
    status: str = "active",
    closed_at: datetime | None = None,
) -> int:
    """直落一条顾客会话（绕过建会话 IP 闸；第 45 刀：过期时刻必须写）。"""
    factory = client.app.state.session_factory
    with factory() as db:
        session = ServiceSession(
            status=status,
            customer_token=token,
            customer_token_expires_at=datetime.now(UTC) + timedelta(hours=1),
            closed_at=closed_at,
        )
        db.add(session)
        db.commit()
        return session.id


def _add_agent_answer(client: TestClient, session_id: int) -> int:
    """直落一条带引用的 agent 回答（反馈闸只认 kind=answer 且 citations 非空）。"""
    factory = client.app.state.session_factory
    with factory() as db:
        message = ServiceMessage(
            session_id=session_id,
            role="agent",
            content="保温杯保修一年。",
            citations=[{"asset_id": 1, "version_no": 1}],
            kind="answer",
            handoff=False,
        )
        db.add(message)
        db.commit()
        return message.id


def _end(client: TestClient, session_id: int, token: str):
    return client.post(
        f"/api/customer/sessions/{session_id}/end",
        headers={"Authorization": f"Bearer {token}"},
    )


def _round_compare(left: datetime | None, right: datetime | None) -> bool:
    """两侧均为库内回读的 timestamptz（微秒精度），直接相等比较。"""
    return left is not None and right is not None and left == right


# ---------- end 端点 ----------


def test_end_closes_active_session_with_closed_at(api: ApiFixture) -> None:
    client, _ = api
    token = "end-active-token"
    session_id = _make_session(client, token)

    resp = _end(client, session_id, token)
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == session_id
    assert body["status"] == "ended"
    assert body["closed_at"] is not None

    factory = client.app.state.session_factory
    with factory() as db:
        session = db.get(ServiceSession, session_id)
        assert session.status == "ended"
        assert session.closed_at is not None
        assert _round_compare(session.closed_at, datetime.fromisoformat(body["closed_at"]))


def test_end_is_idempotent_and_keeps_first_closed_at(api: ApiFixture) -> None:
    client, _ = api
    token = "end-idem-token"
    session_id = _make_session(client, token)

    first = _end(client, session_id, token)
    assert first.status_code == 200
    first_closed = first.json()["closed_at"]

    second = _end(client, session_id, token)
    assert second.status_code == 200
    assert second.json()["status"] == "ended"
    # 幂等：不重写终结时刻（以首次结束为准）
    assert second.json()["closed_at"] == first_closed

    factory = client.app.state.session_factory
    with factory() as db:
        assert _round_compare(
            db.get(ServiceSession, session_id).closed_at,
            datetime.fromisoformat(first_closed),
        )


def test_end_rejects_registered_session(api: ApiFixture) -> None:
    client, _ = api
    token = "end-registered-token"
    session_id = _make_session(client, token, status="registered")

    resp = _end(client, session_id, token)
    assert resp.status_code == 409
    assert "已回流" in resp.json()["detail"]


def test_end_requires_valid_token(api: ApiFixture) -> None:
    client, _ = api
    token = "end-auth-token"
    session_id = _make_session(client, token)

    # 无令牌
    assert client.post(f"/api/customer/sessions/{session_id}/end").status_code == 401
    # 错令牌
    bad = client.post(
        f"/api/customer/sessions/{session_id}/end",
        headers={"Authorization": "Bearer not-the-token"},
    )
    assert bad.status_code == 401
    assert bad.json()["detail"] == "会话不存在或令牌无效"
    # 未知会话：与令牌无效同 401 同文案（自增 id 不可探测）
    unknown = client.post(
        "/api/customer/sessions/999999/end",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert unknown.status_code == 401


def test_end_ip_gate_fronts_auth_for_bad_tokens(api: ApiFixture) -> None:
    """IP 闸前置：坏令牌把 IP 发问账打满后，坏令牌也 429（先于 401，狂刷不碰库）。"""
    client, _ = api
    session_id = _make_session(client, "end-ipgate-token")
    original = client.app.state.customer_rate_limits
    client.app.state.customer_rate_limits = CustomerRateLimits(
        session_ask_limit=50, ip_ask_limit=2, ip_create_limit=50
    )
    try:
        bad_headers = {"Authorization": "Bearer not-the-token"}
        for _ in range(2):  # 都 401，但 IP 账已记满 2 条
            resp = client.post(
                f"/api/customer/sessions/{session_id}/end", headers=bad_headers
            )
            assert resp.status_code == 401
        third = client.post(f"/api/customer/sessions/{session_id}/end", headers=bad_headers)
        assert third.status_code == 429
        assert int(third.headers["retry-after"]) >= 1
    finally:
        client.app.state.customer_rate_limits = original


# ---------- 终态语义：关对话流，不关善后通道 ----------


def test_ended_blocks_ask_but_allows_aftercare(api: ApiFixture) -> None:
    """本刀核心：ended 下发问 409，评分/反馈/联系方式全部放行。"""
    client, _ = api
    token = "end-aftercare-token"
    session_id = _make_session(client, token)
    assert _end(client, session_id, token).status_code == 200
    message_id = _add_agent_answer(client, session_id)

    # 发问：409，状态值给顾客可读中文
    ask = client.post(
        f"/api/customer/sessions/{session_id}/messages",
        json={"content": "结束以后再问一句"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert ask.status_code == 409
    assert "已结束" in ask.json()["detail"]

    # 评分：放行（结束后评分闭环，本刀产品点）
    rating = client.post(
        f"/api/customer/sessions/{session_id}/rating",
        json={"score": 5, "comment": "服务不错"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert rating.status_code == 200
    assert rating.json()["session_id"] == session_id

    # 反馈：放行（善后通道）
    feedback = client.post(
        f"/api/customer/sessions/{session_id}/messages/{message_id}/feedback",
        json={"helpful": True},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert feedback.status_code == 200
    assert feedback.json()["feedback"]["helpful"] is True

    # 联系方式：放行（工单仍可回访）
    factory = client.app.state.session_factory
    with factory() as db:
        ticket = HandoffTicket(session_id=session_id, status="pending")
        db.add(ticket)
        db.commit()
        ticket_id = ticket.id
    contact = client.post(
        f"/api/customer/sessions/{session_id}/handoff-tickets/{ticket_id}",
        json={"name": "张三", "note": "请回电", "phone": "13800138000"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert contact.status_code == 200
    assert contact.json()["ticket_no"] == f"H-{ticket_id:04d}"


def test_registered_session_still_blocks_rating(api: ApiFixture) -> None:
    """既有行为钉子回归：registered 下评分仍 409（第 71 刀的 active 闸语义不变）。"""
    client, _ = api
    token = "end-registered-rating"
    session_id = _make_session(client, token, status="registered")
    resp = client.post(
        f"/api/customer/sessions/{session_id}/rating",
        json={"score": 4},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409


# ---------- 操作者回流 ----------


def test_operator_can_register_ended_session_without_rewriting_closed_at(
    api: ApiFixture,
) -> None:
    client, _ = api
    token = "end-backflow-token"
    session_id = _make_session(client, token)
    closed = _end(client, session_id, token).json()["closed_at"]
    # 转写非空：至少一条消息（register 拒绝空转写）
    factory = client.app.state.session_factory
    with factory() as db:
        db.add(
            ServiceMessage(
                session_id=session_id,
                role="customer",
                content="保温杯保修多久？",
                citations=None,
                kind=None,
                handoff=False,
            )
        )
        db.commit()

    assert (
        client.post(
            "/api/auth/login", json={"username": "operator", "password": "operator123"}
        ).status_code
        == 200
    )
    resp = client.post(f"/api/service/sessions/{session_id}/register")
    assert resp.status_code == 201
    assert resp.json()["source_kind"] == "session_backflow"

    with factory() as db:
        session = db.get(ServiceSession, session_id)
        assert session.status == "registered"
        assert session.registered_asset_id is not None
        # closed_at 保持顾客结束时刻，不被回流时间改写
        assert _round_compare(session.closed_at, datetime.fromisoformat(closed))
