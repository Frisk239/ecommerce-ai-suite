"""第 95 刀（widget 会话续接）：GET /api/customer/sessions/current/messages 集成测试（真 PG）。

覆盖：
- active：200 回会话状态 + 全量消息（升序），消息形状与操作者详情端点一致
  （content/citations/media_citations/kind/tool/handoff/created_at + id/role）；
- ended / registered：**照回 200**（已结束不复活对话流，但历史/评分反馈这些
  善后语义靠它成立——前端据此锁输入、显示历史+评分条，80 刀口径跨重载）；
- 401 三态（无令牌/令牌无效/**令牌过期**）与发问同口径（统一文案 +
  WWW-Authenticate: Bearer，自增 id 不可探测——本端点路径里根本没有会话 id，
  「current」即令牌所指，无令牌一律 401）；
- 回执的评分/工单锚：已评会话回 rating（71 刀改评跨重载回显）、有工单会话回
  ticket（id + contact_at，handoff 消息回放时挂联系方式表单用）。
"""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
from fastapi.testclient import TestClient
from httpx import Response

from suite_api.models import HandoffTicket, ServiceMessage, ServiceSession, SessionRating

ApiFixture = tuple[TestClient, Path]

_URL_ENV = "SUITE_TEST_DATABASE_URL"
_RESUME_PATH = "/api/customer/sessions/current/messages"


def _seed_session(
    client: TestClient,
    token: str,
    *,
    status: str = "active",
    expires_in: int = 3600,
) -> int:
    """直落一条顾客会话（绕过建会话 IP 闸；过期时刻必须写——NULL 视为不可用）。"""
    factory = client.app.state.session_factory
    with factory() as db:
        session = ServiceSession(
            status=status,
            customer_token=token,
            customer_token_expires_at=datetime.now(UTC) + timedelta(seconds=expires_in),
        )
        db.add(session)
        db.commit()
        return session.id


def _seed_conversation(client: TestClient, session_id: int) -> None:
    """直落一轮完整对话：顾客问句 + 带引用/媒体/工具的 agent 回答（形状钉子用）。"""
    factory = client.app.state.session_factory
    with factory() as db:
        db.add(
            ServiceMessage(
                session_id=session_id,
                role="customer",
                content="保温杯的净含量是多少？",
                citations=None,
                kind=None,
                handoff=False,
            )
        )
        db.add(
            ServiceMessage(
                session_id=session_id,
                role="agent",
                content="净含量 500ml。",
                citations=[{"asset_id": 1, "version_no": 1}],
                media_citations=[
                    {"asset_id": 1, "version_no": 1, "mime": "image/jpeg"},
                    {"asset_id": 2, "version_no": 3, "mime": "video/mp4"},
                ],
                kind="answer",
                handoff=False,
                tool={"name": "get_order_status", "arg": "SO-1001", "result": "已发货"},
            )
        )
        db.commit()


def _resume(client: TestClient, token: str | None) -> Response:
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    return client.get(_RESUME_PATH, headers=headers)


# ---------- active：恢复会话态 ----------


def test_active_session_replays_messages_in_operator_shape(api: ApiFixture) -> None:
    """active 200：状态 + 全量消息升序，形状=操作者详情端点（单一出处不漂移）。"""
    client, _ = api
    token = "resume-active-token"
    session_id = _seed_session(client, token)
    _seed_conversation(client, session_id)

    resp = _resume(client, token)
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == session_id
    assert body["status"] == "active"

    messages = body["messages"]
    assert [m["role"] for m in messages] == ["customer", "agent"]  # id 升序全量
    question, answer = messages
    assert question["content"] == "保温杯的净含量是多少？"
    assert question["citations"] is None and question["media_citations"] is None
    assert answer["content"] == "净含量 500ml。"
    assert answer["citations"] == [{"asset_id": 1, "version_no": 1}]
    # 媒体引用姊妹键原样回放（94b 刀落库形状，重载照样出图/出播放器）
    assert answer["media_citations"] == [
        {"asset_id": 1, "version_no": 1, "mime": "image/jpeg"},
        {"asset_id": 2, "version_no": 3, "mime": "video/mp4"},
    ]
    assert answer["kind"] == "answer"
    assert answer["handoff"] is False
    assert answer["tool"] == {"name": "get_order_status", "arg": "SO-1001", "result": "已发货"}
    assert answer["created_at"] is not None

    # 与操作者详情端点的消息形状一致（同一端点两通道取数对照，防将来漂移）
    assert (
        client.post(
            "/api/auth/login", json={"username": "operator", "password": "operator123"}
        ).status_code
        == 200
    )
    detail = client.get(f"/api/service/sessions/{session_id}").json()
    assert detail["messages"] == messages

    # 未评/无工单：两锚为 null（形态固定，前端不用猜缺键）
    assert body["rating"] is None
    assert body["ticket"] is None


def test_empty_active_session_returns_empty_messages(api: ApiFixture) -> None:
    """建了没问的会话也 200（空列表）——刷新不该因为没消息就走新会话。"""
    client, _ = api
    token = "resume-empty-token"
    session_id = _seed_session(client, token)
    resp = _resume(client, token)
    assert resp.status_code == 200
    assert resp.json() == {
        "session_id": session_id,
        "status": "active",
        "messages": [],
        "rating": None,
        "ticket": None,
    }


# ---------- ended / registered：照回 200（历史回放，不复活） ----------


def test_ended_session_returns_history_not_error(api: ApiFixture) -> None:
    """ended 也 200：前端据此显示历史+评分条、锁输入——不复活对话流是**前端**
    语义，端点只如实回状态；ended 下照常可续接回放是 80 刀善后口径的延续。"""
    client, _ = api
    token = "resume-ended-token"
    session_id = _seed_session(client, token, status="ended")
    _seed_conversation(client, session_id)

    resp = _resume(client, token)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ended"
    assert len(body["messages"]) == 2


def test_registered_session_returns_history(api: ApiFixture) -> None:
    """registered（操作者已回流登记）同样回放：对话流关了，历史还是顾客的。"""
    client, _ = api
    token = "resume-registered-token"
    _seed_session(client, token, status="registered")
    resp = _resume(client, token)
    assert resp.status_code == 200
    assert resp.json()["status"] == "registered"


# ---------- 401：与发问同口径 ----------


def test_resume_requires_bearer_token(api: ApiFixture) -> None:
    client, _ = api
    missing = _resume(client, None)
    assert missing.status_code == 401
    assert missing.json()["detail"] == "会话不存在或令牌无效"
    assert missing.headers.get("WWW-Authenticate") == "Bearer"


def test_resume_rejects_invalid_token_with_ask_copy(api: ApiFixture) -> None:
    """错令牌 -> 401，文案与发问逐字相同（统一口径，不区分错/过期/不存在）。"""
    client, _ = api
    bad = _resume(client, "not-a-real-token")
    assert bad.status_code == 401
    ask_copy = client.post(
        "/api/customer/sessions/999999/messages",
        json={"content": "口径对照"},
        headers={"Authorization": "Bearer not-a-real-token"},
    ).json()["detail"]
    assert bad.json()["detail"] == ask_copy


def test_resume_rejects_expired_token_same_as_invalid(api: ApiFixture) -> None:
    """过期令牌 -> 401 同文案（第 45 刀口径延伸到续接：前端清存档走新会话）。"""
    client, _ = api
    token = "resume-expired-token"
    _seed_session(client, token, expires_in=-60)  # 已过期
    expired = _resume(client, token)
    invalid = _resume(client, "not-a-real-token")
    assert expired.status_code == invalid.status_code == 401
    assert expired.json()["detail"] == invalid.json()["detail"]


def test_resume_rejects_operator_session_token_guess(api: ApiFixture) -> None:
    """操作者预览会话没有令牌（customer_token NULL）：等值查必空 -> 401。"""
    client, _ = api
    assert (
        client.post(
            "/api/auth/login", json={"username": "operator", "password": "operator123"}
        ).status_code
        == 200
    )
    assert client.post("/api/service/sessions").status_code == 201
    assert _resume(client, "whatever-guess").status_code == 401


# ---------- 评分 / 工单锚（回放保真） ----------


def test_resume_echoes_existing_rating(api: ApiFixture) -> None:
    """已评会话回 rating：前端回显星星+留言（71 刀改评语义跨重载不丢）。"""
    client, _ = api
    token = "resume-rated-token"
    session_id = _seed_session(client, token, status="ended")
    factory = client.app.state.session_factory
    with factory() as db:
        db.add(SessionRating(session_id=session_id, score=4, comment="回答挺快"))
        db.commit()

    body = _resume(client, token).json()
    assert body["rating"]["score"] == 4
    assert body["rating"]["comment"] == "回答挺快"
    assert body["rating"]["session_id"] == session_id


def test_resume_echoes_handoff_ticket_anchor(api: ApiFixture) -> None:
    """有工单的会话回 ticket 锚（id + contact_at）：handoff 消息重载后联系方式
    表单/「已记录联系方式」态的回放锚点（42 刀工单是会话级，一会话一单）。"""
    client, _ = api
    token = "resume-ticket-token"
    session_id = _seed_session(client, token, status="ended")
    factory = client.app.state.session_factory
    with factory() as db:
        ticket = HandoffTicket(
            session_id=session_id, status="pending", contact_at=datetime.now(UTC)
        )
        db.add(ticket)
        db.commit()
        ticket_id = ticket.id

    body = _resume(client, token).json()
    assert body["ticket"]["id"] == ticket_id
    assert body["ticket"]["contact_at"] is not None
    # 顾客面锚只给 id+contact_at：联系方式原文不随本端点回显
    assert set(body["ticket"].keys()) == {"id", "contact_at"}


# ---------- 令牌 TTL 的 NULL 严格口径 ----------


def test_resume_treats_null_expiry_as_unusable(api: ApiFixture) -> None:
    """NULL 过期时刻=不可用（严格口径与发问一致，不给静默放行的口子）。"""
    client, _ = api
    url = os.environ[_URL_ENV]
    token = "resume-null-expiry-token"
    session_id = _seed_session(client, token)
    with psycopg.connect(url, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE service_sessions SET customer_token_expires_at = NULL WHERE id = %s",
            (session_id,),
        )
    assert _resume(client, token).status_code == 401
