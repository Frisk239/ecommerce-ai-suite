"""第 42 刀（ADR 0046）：转人工真闭环集成测试（真 PG，LLM 空凭证不调外网）。

覆盖（对应 Owner 裁决与任务验收）：
- 显式「转人工」-> kind=handoff 消息（含 H-xxxx 回执）+ 库里一条 pending 工单；
- 同会话再转 -> 不新增工单、号码不变（一会话一单幂等）；
- 拒答也建工单，且拒答消息文本不变（REFUSAL_CONTENT 全等结构保持）；
- 顾客提交联系方式 -> contact_at 落值；操作者面出口掩电话/邮箱（钉测 0038），
  库内原文不动；
- 操作者结单 -> 幂等 409 + 会话列表 pending_ticket_count 归零；
- 「投诉」「举报」不再产生知识缺口（语义变化，现在是 handoff）；
- 纯函数 wants_human（含「人工智能」不命中）。

工单与缺口是两件事（0046 §3）：同一会话可并存，不合并。
"""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sse_helpers import parse_sse_events

from suite_api.models import HandoffTicket, KnowledgeGap, ServiceSession
from suite_api.services.answer import REFUSAL_CONTENT
from suite_api.services.chat_engine import REJECTED_CONTENT, run_ask
from suite_api.services.handoff_tickets import wants_human

ApiFixture = tuple[TestClient, Path]


def _login(client: TestClient) -> None:
    assert (
        client.post(
            "/api/auth/login", json={"username": "operator", "password": "operator123"}
        ).status_code
        == 200
    )


def _new_operator_session(client: TestClient) -> int:
    factory = client.app.state.session_factory
    with factory() as db:
        session = ServiceSession(status="active")
        db.add(session)
        db.commit()
        return session.id


def _ask_in_session(client: TestClient, session_id: int, question: str):
    factory = client.app.state.session_factory
    with factory() as db:
        session = db.get(ServiceSession, session_id)
        return asyncio.run(run_ask(db, session, question))


def _run_question(client: TestClient, question: str):
    session_id = _new_operator_session(client)
    return _ask_in_session(client, session_id, question), session_id


def _tickets(client: TestClient, session_id: int) -> list[HandoffTicket]:
    factory = client.app.state.session_factory
    with factory() as db:
        return list(
            db.scalars(select(HandoffTicket).where(HandoffTicket.session_id == session_id))
        )


def _gap_count(client: TestClient, session_id: int) -> int:
    factory = client.app.state.session_factory
    with factory() as db:
        return len(
            list(db.scalars(select(KnowledgeGap).where(KnowledgeGap.session_id == session_id)))
        )


def _customer_ask(client: TestClient, session_id: int, token: str, question: str) -> dict:
    with client.stream(
        "POST",
        f"/api/customer/sessions/{session_id}/messages",
        json={"content": question},
        headers={"Authorization": f"Bearer {token}"},
    ) as resp:
        assert resp.status_code == 200
        raw = "".join(resp.iter_text())
    events = parse_sse_events(raw)
    return next(data for event, data in events if event == "complete")


def _seed_customer_session(client: TestClient, token: str) -> int:
    """直接落一条顾客会话（绕过建会话 IP 闸）：本文件需多次建顾客会话做多形态
    掩码钉测，走 /customer/sessions 会撞 5/60s 的 IP 建会话闸。

    第 45 刀：绕过签发端点就得把签发时该写的字段写全——**过期时刻必须给**
    （鉴权侧把 NULL 视为不可用，只塞令牌不塞过期时刻会被判 401）。
    """
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


def _submit_contact(
    client: TestClient, session_id: int, token: str, ticket_id: int, payload: dict
) -> dict:
    resp = client.post(
        f"/api/customer/sessions/{session_id}/handoff-tickets/{ticket_id}",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    return resp.json()


# ---------- 纯函数：词表 ----------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "want"),
    [
        ("你们用的是人工智能吗", False),  # 裸「人工」不命中（防误伤）
        ("人工", False),
        ("我要转人工", True),
        ("找人工客服", True),
        ("要人工", True),
        ("有真人客服吗", True),
        ("我要投诉", True),
        ("举报这个卖家", True),
    ],
)
def test_wants_human_wordlist(question: str, want: bool) -> None:
    assert wants_human(question) is want


# ---------- 显式请求 --------------------------------------------------------------


def test_explicit_human_request_creates_pending_ticket(api: ApiFixture) -> None:
    """①显式「转人工」-> handoff 回执 + 一条 pending 工单，无缺口。"""
    client, _ = api
    _login(client)
    outcome, session_id = _run_question(client, "我要转人工")
    assert outcome.answer.kind == "handoff"
    assert outcome.answer.handoff is True
    assert outcome.answer.citations == []
    assert outcome.tool is not None and outcome.tool["name"] == "handoff"
    assert outcome.gap is None
    assert outcome.ticket is not None
    no = f"H-{outcome.ticket.id:04d}"
    assert no in outcome.answer.content
    assert "4 小时内回复" in outcome.answer.content

    tickets = _tickets(client, session_id)
    assert len(tickets) == 1
    assert tickets[0].status == "pending"
    assert tickets[0].contact_at is None
    assert tickets[0].message_id == outcome.agent_message.id  # 表单挂触发消息


def test_operator_channel_complete_carries_ticket_keys(api: ApiFixture) -> None:
    """操作者通道 complete 同形状带 ticket_id/ticket_no（不走 gap_id 白名单）。"""
    client, _ = api
    _login(client)
    session_id = client.post("/api/service/sessions").json()["id"]
    with client.stream(
        "POST",
        f"/api/service/sessions/{session_id}/messages",
        json={"content": "转人工"},
    ) as resp:
        assert resp.status_code == 200
        raw = "".join(resp.iter_text())
    complete = next(data for event, data in parse_sse_events(raw) if event == "complete")
    assert complete["kind"] == "handoff"
    assert complete["handoff"] is True
    assert complete["ticket_no"].startswith("H-")
    assert complete["ticket_id"] is not None
    assert complete["ticket_contact_at"] is None
    assert complete["tool"]["name"] == "handoff"
    assert complete["gap_id"] is None  # 转人工不落缺口


def test_proposal_path_handoff_sentinel_creates_ticket(
    api: ApiFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """提议路径：模型输出 TOOL: handoff {} -> 同一 handoff 出口，非「未注册工具」拒。

    钉死 parse_tool_proposal 先识别 sentinel 再查 registry——若顺序反了会落
    REJECTED_CONTENT + rejected 工具条，语义全错。
    """
    client, _ = api
    _login(client)

    async def fake_complete_tool_proposal(_system: str, _user: str) -> str:
        return "TOOL: handoff {}"

    monkeypatch.setattr(
        "suite_api.services.llm.complete_tool_proposal", fake_complete_tool_proposal
    )
    # 问句无词表/订单号/库存词，才会走到提议步
    outcome, session_id = _run_question(client, "帮我看看这个怎么处理")
    assert outcome.answer.kind == "handoff"
    assert outcome.tool is not None and outcome.tool["name"] == "handoff"
    assert outcome.tool["arg"] == "提议"  # 来源=模型提议（非词表）
    assert outcome.tool.get("rejected") is not True
    assert REJECTED_CONTENT not in outcome.answer.content
    assert outcome.gap is None
    assert outcome.ticket is not None
    assert f"H-{outcome.ticket.id:04d}" in outcome.answer.content
    assert len(_tickets(client, session_id)) == 1
    assert _gap_count(client, session_id) == 0


def test_rejected_proposal_creates_ticket_and_keeps_text(
    api: ApiFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """提议被拒（越狱/坏参）也建工单：亮「已转人工」徽章的路径背后必须有工单。"""
    client, _ = api
    _login(client)

    async def fake_complete_tool_proposal(_system: str, _user: str) -> str:
        return 'TOOL: drop_all_tables {"all": "true"}'  # 未注册工具 -> 拒绝转人工

    monkeypatch.setattr(
        "suite_api.services.llm.complete_tool_proposal", fake_complete_tool_proposal
    )
    outcome, session_id = _run_question(client, "帮我看看这个怎么处理")
    assert outcome.answer.kind == "handoff"
    assert outcome.answer.content == REJECTED_CONTENT  # 既有文案一字不改
    assert outcome.tool is not None and outcome.tool.get("rejected") is True
    assert outcome.gap is None
    assert outcome.ticket is not None
    assert len(_tickets(client, session_id)) == 1


def test_second_handoff_same_session_reuses_ticket(api: ApiFixture) -> None:
    """②同会话再转 -> 不新增工单、号码不变（一会话一单幂等）。"""
    client, _ = api
    _login(client)
    session_id = _new_operator_session(client)
    first = _ask_in_session(client, session_id, "转人工")
    second = _ask_in_session(client, session_id, "再找人工客服")
    assert first.ticket is not None and second.ticket is not None
    assert first.ticket.id == second.ticket.id
    assert f"H-{first.ticket.id:04d}" in second.answer.content
    assert len(_tickets(client, session_id)) == 1


def test_refusal_creates_ticket_and_keeps_refusal_text(api: ApiFixture) -> None:
    """③拒答也建工单；拒答消息文本一个字符不改（REFUSAL_CONTENT 结构保持）。"""
    client, _ = api
    _login(client)
    outcome, session_id = _run_question(client, "会员积分怎么兑换礼品？")
    assert outcome.answer.kind == "refusal"
    assert outcome.gap is not None
    assert outcome.ticket is not None
    # 拒答消息文本口径不变：固定文案开头 + 问句摘要（第 27 刀结构）
    assert outcome.agent_message.content.startswith(REFUSAL_CONTENT)
    assert "问句摘要：会员积分怎么兑换礼品？" in outcome.agent_message.content
    tickets = _tickets(client, session_id)
    assert len(tickets) == 1
    assert tickets[0].message_id == outcome.agent_message.id  # 表单挂在拒答消息下


def test_tool_failure_handoffs_also_create_ticket(api: ApiFixture) -> None:
    """订单/退货资格查无与库存未设置的 handoff 也建工单（徽章背后必须有工单）。

    既有 order/stock/return 的精确 handoff 文案由各自测试继续钉（本测只补
    「工单存在」这一新契约）。
    """
    client, _ = api
    _login(client)
    # 订单查无 -> _run_order_ask handoff
    order_outcome, order_session = _run_question(client, "我的订单 SO-9999 到哪了？")
    assert order_outcome.answer.kind == "handoff"
    assert order_outcome.ticket is not None
    assert len(_tickets(client, order_session)) == 1
    # 退货资格查无 -> _run_return_eligibility_ask handoff
    return_outcome, return_session = _run_question(client, "SO-9999 我想退货")
    assert return_outcome.answer.kind == "handoff"
    assert return_outcome.ticket is not None
    assert len(_tickets(client, return_session)) == 1
    # 库存未设置（商品存在但 stock NULL）-> _run_stock_ask handoff
    created = client.post("/api/products", json={"name": "工单库存杯", "category": "器皿"})
    assert created.status_code == 201
    stock_outcome, stock_session = _run_question(client, "工单库存杯有货吗？")
    assert stock_outcome.answer.kind == "handoff"
    assert stock_outcome.ticket is not None
    assert len(_tickets(client, stock_session)) == 1


# ---------- 联系方式与出口掩 ------------------------------------------------------


def test_customer_contact_submit_then_operator_sees_masked(api: ApiFixture) -> None:
    """④顾客提交联系方式 -> contact_at 落值；操作者面掩码（库内原文不动）。"""
    client, _ = api
    client.cookies.clear()
    created = client.post("/api/customer/sessions").json()
    session_id, token = created["session_id"], created["token"]
    complete = _customer_ask(client, session_id, token, "我要转人工")
    assert complete["kind"] == "handoff"
    assert complete["ticket_no"].startswith("H-")
    assert complete["ticket_contact_at"] is None
    ticket_id = complete["ticket_id"]

    resp = client.post(
        f"/api/customer/sessions/{session_id}/handoff-tickets/{ticket_id}",
        json={
            "name": "李四",
            "note": "订单物流一直没更新",
            "email": "lisi@example.com",
            "phone": "13800138000",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    # 顾客回执只回工单号+提交时间（不借操作者掩码视图，也不回显原文）
    assert body["ticket_no"] == complete["ticket_no"]
    assert body["contact_at"] is not None
    assert "phone" not in body and "email" not in body

    _login(client)
    detail = client.get(f"/api/service/sessions/{session_id}").json()
    ticket = detail["ticket"]
    assert ticket["ticket_no"] == complete["ticket_no"]
    assert ticket["name"] == "李四"  # 姓名不掩（操作者要称呼对方）
    assert ticket["phone"] == "1********00"  # redact_contact 口径
    assert ticket["email"] == "****@example.com"

    # 库内原文不动（0038 字节不动）
    factory = client.app.state.session_factory
    with factory() as db:
        row = db.get(HandoffTicket, ticket_id)
        assert row.phone == "13800138000"
        assert row.email == "lisi@example.com"


@pytest.mark.parametrize(
    ("field", "raw", "masked"),
    [
        ("phone", "138-0013-8000", "1********00"),  # 带 - 分隔手机号
        ("phone", "010-12345678", "0********78"),  # 座机带区号（11 位同口径）
        ("phone", "021 6234 5678", "0********78"),  # 空格分组座机
        ("email", "ab@x.co", "****@x.co"),  # 短 local + 短 TLD
        ("email", "a@b.cn", "****@b.cn"),
    ],
)
def test_operator_outlet_masks_contact_forms(
    api: ApiFixture, field: str, raw: str, masked: str
) -> None:
    """P1 钉测：redact 漏掉的形态在操作者面也必须掩，原文不出现。

    入口轻校验接受这些形态（服务真实回访），出口用 redact_contact 收口——否则
    违反 ADR 0046 §6「出口必掩」。
    """
    client, _ = api
    client.cookies.clear()
    token = f"tok-mask-{field}-{raw}"
    session_id = _seed_customer_session(client, token)
    complete = _customer_ask(client, session_id, token, "我要转人工")
    payload = {"name": "李四", "note": "请回电", field: raw}
    ack = _submit_contact(client, session_id, token, complete["ticket_id"], payload)
    assert ack["ticket_no"] == complete["ticket_no"]

    _login(client)
    detail = client.get(f"/api/service/sessions/{session_id}").json()
    value = detail["ticket"][field]
    assert value == masked
    assert raw not in value


def test_operator_outlet_masks_note_pii(api: ApiFixture) -> None:
    """P1 钉测：留言（自由文本）里的电话/邮箱也必须掩，库内原文不动。"""
    client, _ = api
    client.cookies.clear()
    token = "tok-note-mask"
    session_id = _seed_customer_session(client, token)
    complete = _customer_ask(client, session_id, token, "转人工")
    note = "我的手机13800138000，邮箱 lisi@example.com，请回电"
    _submit_contact(
        client, session_id, token, complete["ticket_id"], {"name": "李四", "note": note}
    )

    _login(client)
    detail = client.get(f"/api/service/sessions/{session_id}").json()
    masked_note = detail["ticket"]["note"]
    assert "13800138000" not in masked_note
    assert "lisi@example.com" not in masked_note
    assert "1********00" in masked_note
    assert "****@example.com" in masked_note
    # 库内原文不动（0038 字节不动）
    factory = client.app.state.session_factory
    with factory() as db:
        row = db.get(HandoffTicket, complete["ticket_id"])
        assert row.note == note


def test_customer_contact_validation_and_ownership(api: ApiFixture) -> None:
    """联系方式校验：空姓名/乱格式 422；他人会话工单 404（不泄露存在性）。"""
    client, _ = api
    client.cookies.clear()
    created = client.post("/api/customer/sessions").json()
    session_id, token = created["session_id"], created["token"]
    complete = _customer_ask(client, session_id, token, "转人工")
    ticket_id = complete["ticket_id"]

    base = f"/api/customer/sessions/{session_id}/handoff-tickets/{ticket_id}"
    auth = {"Authorization": f"Bearer {token}"}
    assert (
        client.post(base, json={"name": "  ", "note": "x"}, headers=auth).status_code == 422
    )
    assert (
        client.post(
            base,
            json={"name": "李四", "note": "x", "email": "not-an-email"},
            headers=auth,
        ).status_code
        == 422
    )
    # 不属于本会话的工单：统一 404
    assert (
        client.post(
            f"/api/customer/sessions/{session_id}/handoff-tickets/999999",
            json={"name": "李四", "note": "x"},
            headers=auth,
        ).status_code
        == 404
    )


# ---------- 操作者结单 ------------------------------------------------------------


def test_resolve_is_idempotent_and_clears_pending_count(api: ApiFixture) -> None:
    """⑤结单 -> 幂等 409；会话列表 pending_ticket_count 归零。"""
    client, _ = api
    _login(client)
    outcome, session_id = _run_question(client, "我要投诉")
    assert outcome.ticket is not None
    ticket_id = outcome.ticket.id

    rows = client.get("/api/service/sessions").json()
    row = next(r for r in rows if r["id"] == session_id)
    assert row["pending_ticket_count"] == 1

    first = client.post(f"/api/service/handoff-tickets/{ticket_id}/resolve")
    assert first.status_code == 200
    assert first.json()["status"] == "resolved"
    assert first.json()["resolved_at"] is not None
    # 幂等：已结单再结 409
    assert client.post(f"/api/service/handoff-tickets/{ticket_id}/resolve").status_code == 409
    # 不存在的工单 404
    assert client.post("/api/service/handoff-tickets/999999/resolve").status_code == 404

    rows = client.get("/api/service/sessions").json()
    row = next(r for r in rows if r["id"] == session_id)
    assert row["pending_ticket_count"] == 0


# ---------- 语义变化：投诉/举报不再产生缺口 ---------------------------------------


@pytest.mark.parametrize("question", ["我要投诉", "举报这个卖家"])
def test_complaint_and_report_become_handoff_without_gap(api: ApiFixture, question: str) -> None:
    """⑥投诉/举报现在是 handoff（词表快路径），不再落知识缺口。"""
    client, _ = api
    _login(client)
    outcome, session_id = _run_question(client, question)
    assert outcome.answer.kind == "handoff"
    assert outcome.gap is None
    assert outcome.ticket is not None
    assert _gap_count(client, session_id) == 0
