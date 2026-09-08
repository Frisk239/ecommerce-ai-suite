"""顾客通道集成测试（真 PG，LLM 空凭证不调外网；见 conftest 的清理口径）。

第 8 刀契约（ADR 0021/0033）+ 第 10 刀安全面收口：
- 签发（无登录）-> Bearer 发问 -> SSE 与操作者版同事件序，complete 不带
  gap_id；拒答照常落缺口（操作者治理台可见）-> 操作者列表见 origin=customer
  -> 操作者回流登记（0021：回流仍是操作者动作）-> 顾客再问 409。
- 无效/缺令牌与会话不存在统一 401 同文案（WWW-Authenticate: Bearer；自增
  session id 不可探测）；空问句 422。
- 限流闸序：IP 闸先于鉴权（坏令牌超 IP 闸也 429）；会话闸后于鉴权（无效
  令牌耗不了真会话配额）；IP 口径直连默认忽略 XFF，trust 模式信第一跳。
"""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sse_helpers import parse_sse_events

from suite_api.services.rate_limit import CustomerRateLimits

ApiFixture = tuple[TestClient, Path]

# 本文件独有关键词（module 独立库，不与其他文件已发布块串台）
_CHOPSTICK_DOC = "钛合金筷子产品说明\n筷长：26cm".encode()


def _login(client: TestClient) -> None:
    assert (
        client.post(
            "/api/auth/login", json={"username": "operator", "password": "operator123"}
        ).status_code
        == 200
    )


def _upload_and_publish(client: TestClient, content: bytes, title: str) -> int:
    resp = client.post(
        "/api/assets/register",
        files={"file": ("spec.txt", content, "text/plain")},
        data={"title": title},
    )
    assert resp.status_code == 201
    asset_id = resp.json()["id"]
    assert client.post(f"/api/assets/{asset_id}/publish").status_code == 200
    return asset_id


def _create_customer_session(client: TestClient) -> dict:
    resp = client.post("/api/customer/sessions")
    assert resp.status_code == 201
    return resp.json()


def _customer_ask(
    client: TestClient, session_id: int, token: str, question: str
) -> list[tuple[str, dict]]:
    with client.stream(
        "POST",
        f"/api/customer/sessions/{session_id}/messages",
        json={"content": question},
        headers={"Authorization": f"Bearer {token}"},
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        raw = "".join(resp.iter_text())
    return parse_sse_events(raw)


# ---------- 全路径 ----------


def test_customer_channel_needs_no_login(api: ApiFixture) -> None:
    client, _ = api
    client.cookies.clear()  # 顾客不是操作者（0021）：无登录态也可签发
    created = _create_customer_session(client)
    assert isinstance(created["session_id"], int)
    # token_urlsafe(32) 输出 43 字符 URL-safe 字母表
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", created["token"])


def test_customer_full_flow_same_engine(api: ApiFixture) -> None:
    client, _ = api
    client.cookies.clear()
    _login(client)  # 操作者备证据（发布切块入索引）
    asset_id = _upload_and_publish(client, _CHOPSTICK_DOC, "筷子规格文档")
    client.cookies.clear()  # 之后全走顾客通道，不带操作者会话

    created = _create_customer_session(client)
    sid, token = created["session_id"], created["token"]

    # 顾客发问：事件序与操作者版同（thinking -> delta* -> complete）
    events = _customer_ask(client, sid, token, "筷长是多少？")
    kinds = [event for event, _ in events]
    assert kinds[0] == "thinking"
    assert events[0][1]["text"] == "正在检索已发布资产…"
    assert kinds[-1] == "complete"
    complete = events[-1][1]
    assert complete["kind"] == "answer"
    assert complete["citations"] == [{"asset_id": asset_id, "version_no": 1}]
    streamed = "".join(d["text"] for e, d in events if e == "delta")
    assert "26cm" in streamed

    # 顾客版 complete 是载荷白名单：不带 gap_id（内部缺口 id 不暴露给顾客）
    assert "gap_id" not in complete
    assert complete["fallback"] is True  # 空 LLM 凭证降级证据组装模板（conftest 口径）

    # 无证据拒答：顾客侧 refusal+handoff 且无 gap_id；缺口照常落库
    refusal_events = _customer_ask(client, sid, token, "会员生日礼怎么领？")
    refusal_complete = refusal_events[-1][1]
    assert refusal_complete["kind"] == "refusal"
    assert refusal_complete["handoff"] is True
    assert refusal_complete["citations"] == []
    assert "gap_id" not in refusal_complete
    # 第 27 刀：拒答交接摘要白名单延伸到消息文本——顾客通道带问句摘要、
    # **不带**「缺口：G-xxxx」段（操作者版全等断言见 test_service_integration）
    refusal_deltas = "".join(d["text"] for e, d in refusal_events if e == "delta")
    assert refusal_deltas == (
        "抱歉，已发布资产里没有能回答这个问题的证据。\n问句摘要：会员生日礼怎么领？"
    )
    assert "缺口" not in refusal_deltas
    assert "G-" not in refusal_deltas

    client.cookies.clear()
    _login(client)
    gaps = [g["question"] for g in client.get("/api/knowledge-gaps").json()]
    assert "会员生日礼怎么领？" in gaps

    # 操作者列表：顾客会话带 origin=customer（客服页「顾客」徽章的数据源），
    # 操作者自建会话 origin=operator
    listing = client.get("/api/service/sessions").json()
    row = next(r for r in listing if r["id"] == sid)
    assert row["origin"] == "customer"
    op_sid = client.post("/api/service/sessions").json()["id"]
    op_row = next(r for r in client.get("/api/service/sessions").json() if r["id"] == op_sid)
    assert op_row["origin"] == "operator"

    # 回流登记仍是操作者动作（0021）；顾客消息进转写，标题取顾客首问
    register_resp = client.post(f"/api/service/sessions/{sid}/register")
    assert register_resp.status_code == 201
    assert register_resp.json()["source_kind"] == "session_backflow"
    assert register_resp.json()["title"] == "筷长是多少？"

    # 登记后再问：会话 registered -> 409（令牌随之失去发问资格）
    client.cookies.clear()
    resp = client.post(
        f"/api/customer/sessions/{sid}/messages",
        json={"content": "再问一句"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409


# ---------- 令牌鉴权与输入校验 ----------


def test_customer_token_auth(api: ApiFixture) -> None:
    client, _ = api
    client.cookies.clear()
    created = _create_customer_session(client)
    sid, token = created["session_id"], created["token"]
    body = {"content": "你好"}

    # 缺 Authorization 头 -> 401 + WWW-Authenticate
    missing = client.post(f"/api/customer/sessions/{sid}/messages", json=body)
    assert missing.status_code == 401
    assert missing.headers.get("www-authenticate") == "Bearer"
    # 无效令牌 -> 401 + WWW-Authenticate
    bad = client.post(
        f"/api/customer/sessions/{sid}/messages",
        json=body,
        headers={"Authorization": "Bearer not-the-token"},
    )
    assert bad.status_code == 401
    assert bad.json()["detail"] == "会话不存在或令牌无效"
    assert bad.headers.get("www-authenticate") == "Bearer"
    # 非Bearer方案 -> 401
    weird = client.post(
        f"/api/customer/sessions/{sid}/messages", json=body, headers={"Authorization": token}
    )
    assert weird.status_code == 401
    # 未知会话 -> 401 与令牌无效同文案（自增 id 不可探测：404/401 双态是探测面）
    unknown = client.post(
        "/api/customer/sessions/999999/messages",
        json=body,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert unknown.status_code == 401
    assert unknown.json()["detail"] == "会话不存在或令牌无效"
    assert unknown.json()["detail"] == bad.json()["detail"]
    assert unknown.headers.get("www-authenticate") == "Bearer"


def test_customer_cannot_ask_operator_session(api: ApiFixture) -> None:
    """操作者预览会话无令牌（customer_token 恒 NULL）：拿任意 Bearer 打它 -> 401。"""
    client, _ = api
    client.cookies.clear()
    _login(client)
    op_sid = client.post("/api/service/sessions").json()["id"]
    client.cookies.clear()
    resp = client.post(
        f"/api/customer/sessions/{op_sid}/messages",
        json={"content": "你好"},
        headers={"Authorization": "Bearer whatever"},
    )
    assert resp.status_code == 401


def test_customer_input_validation(api: ApiFixture) -> None:
    client, _ = api
    client.cookies.clear()
    created = _create_customer_session(client)
    sid, token = created["session_id"], created["token"]
    blank = client.post(
        f"/api/customer/sessions/{sid}/messages",
        json={"content": "   "},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert blank.status_code == 422


def test_customer_rate_limit_429_with_retry_after(api: ApiFixture) -> None:
    """小阈值替换 app.state.customer_rate_limits：会话发问与 IP 建会话超限 -> 429+Retry-After。"""
    client, _ = api
    client.cookies.clear()
    original = client.app.state.customer_rate_limits
    client.app.state.customer_rate_limits = CustomerRateLimits(
        session_ask_limit=2, ip_ask_limit=50, ip_create_limit=2
    )
    try:
        # IP 建会话：第 3 次 -> 429
        first = client.post("/api/customer/sessions")
        second = client.post("/api/customer/sessions")
        third = client.post("/api/customer/sessions")
        assert first.status_code == 201
        assert second.status_code == 201
        assert third.status_code == 429
        retry_after = third.headers.get("retry-after")
        assert retry_after is not None and int(retry_after) >= 1

        # 会话发问：同会话第 3 问 -> 429（空 LLM 凭证降级模板，不调外网）
        created = first.json()
        sid, token = created["session_id"], created["token"]
        assert len(_customer_ask(client, sid, token, "第一问")) > 0
        assert len(_customer_ask(client, sid, token, "第二问")) > 0
        limited = client.post(
            f"/api/customer/sessions/{sid}/messages",
            json={"content": "第三问"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert limited.status_code == 429
        assert int(limited.headers["retry-after"]) >= 1
    finally:
        client.app.state.customer_rate_limits = original


def test_rate_limit_ip_key_direct_mode_ignores_xff(api: ApiFixture) -> None:
    """直连默认（XFF 信任模式收口）：完全忽略 X-Forwarded-For——伪造不同头的
    请求落同一真实 IP 账，换头刷不过建会话闸。"""
    client, _ = api
    client.cookies.clear()
    original = client.app.state.customer_rate_limits
    client.app.state.customer_rate_limits = CustomerRateLimits(
        session_ask_limit=50, ip_ask_limit=50, ip_create_limit=1
    )
    try:
        # 两次请求换了 XFF，仍同一真实对端记账 -> 第二次 429（换头无效）
        assert (
            client.post(
                "/api/customer/sessions", headers={"X-Forwarded-For": "203.0.113.7"}
            ).status_code
            == 201
        )
        blocked = client.post("/api/customer/sessions", headers={"X-Forwarded-For": "198.51.100.9"})
        assert blocked.status_code == 429
    finally:
        client.app.state.customer_rate_limits = original


def test_rate_limit_ip_key_trust_mode_uses_xff_first_hop(
    api: ApiFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """反代模式（monkeypatch settings）：信 XFF 第一跳——同第一跳连建第二个
    429，换第一跳各自记账又能建。"""
    client, _ = api
    client.cookies.clear()
    monkeypatch.setattr(client.app.state.settings, "customer_trust_proxy", True)
    original = client.app.state.customer_rate_limits
    client.app.state.customer_rate_limits = CustomerRateLimits(
        session_ask_limit=50, ip_ask_limit=50, ip_create_limit=1
    )
    try:
        assert (
            client.post(
                "/api/customer/sessions", headers={"X-Forwarded-For": "203.0.113.7, 10.0.0.1"}
            ).status_code
            == 201
        )
        blocked = client.post(
            "/api/customer/sessions", headers={"X-Forwarded-For": "203.0.113.7, 10.0.0.2"}
        )
        assert blocked.status_code == 429  # 第二跳变了不算数：仍同一第一跳
        other = client.post("/api/customer/sessions", headers={"X-Forwarded-For": "198.51.100.9"})
        assert other.status_code == 201  # 不同第一跳各账
    finally:
        client.app.state.customer_rate_limits = original


def test_bad_token_cannot_burn_session_quota(api: ApiFixture) -> None:
    """闸序重排：无效令牌在会话闸之前被 401 拦——连打超会话阈值次后，真令牌
    同会话仍可问（发问配额未被替耗）；好令牌超出阈值后仍照常 429。"""
    client, _ = api
    client.cookies.clear()
    original = client.app.state.customer_rate_limits
    client.app.state.customer_rate_limits = CustomerRateLimits(
        session_ask_limit=2, ip_ask_limit=20, ip_create_limit=5
    )
    try:
        created = _create_customer_session(client)
        sid, token = created["session_id"], created["token"]
        bad_headers = {"Authorization": "Bearer not-the-token"}
        for _ in range(5):  # 5 > 会话阈值 2：若会话闸仍在鉴权之前，这里会被替耗成 429
            resp = client.post(
                f"/api/customer/sessions/{sid}/messages",
                json={"content": "你好"},
                headers=bad_headers,
            )
            assert resp.status_code == 401
        assert len(_customer_ask(client, sid, token, "真顾客第一问")) > 0
        assert len(_customer_ask(client, sid, token, "真顾客第二问")) > 0
        limited = client.post(
            f"/api/customer/sessions/{sid}/messages",
            json={"content": "真顾客第三问"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert limited.status_code == 429  # 会话闸自身仍有效
        assert int(limited.headers["retry-after"]) >= 1
    finally:
        client.app.state.customer_rate_limits = original


def test_ip_gate_fronts_auth_for_bad_tokens(api: ApiFixture) -> None:
    """IP 闸前置：坏令牌把 IP 发问账打满后，坏令牌也 429（先于 401，狂刷不碰库）。"""
    client, _ = api
    client.cookies.clear()
    original = client.app.state.customer_rate_limits
    client.app.state.customer_rate_limits = CustomerRateLimits(
        session_ask_limit=50, ip_ask_limit=3, ip_create_limit=5
    )
    try:
        created = _create_customer_session(client)
        sid = created["session_id"]
        bad_headers = {"Authorization": "Bearer not-the-token"}
        for _ in range(3):  # 都 401，但 IP 账已记满 3 条
            resp = client.post(
                f"/api/customer/sessions/{sid}/messages",
                json={"content": "你好"},
                headers=bad_headers,
            )
            assert resp.status_code == 401
        fourth = client.post(
            f"/api/customer/sessions/{sid}/messages", json={"content": "你好"}, headers=bad_headers
        )
        assert fourth.status_code == 429
        assert int(fourth.headers["retry-after"]) >= 1
    finally:
        client.app.state.customer_rate_limits = original
