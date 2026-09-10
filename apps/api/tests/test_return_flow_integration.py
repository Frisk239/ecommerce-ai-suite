"""两阶段写 + 忠实度闸 + 反馈分诊集成测试（第 40 刀，ADR 0044；真 PG）。

- 两阶段全链：单号+退货意图 -> 资格查询（token 进工具条）-> 操作者确认端点
  -> orders.events 追加确认事件 -> create_return 轨迹消息落库 -> 「退货进度」
  可见确认事件；二次确认 409 幂等。
- 令牌防线：篡改/过期/张冠李戴 -> 400；越窗订单 -> 资格查询即 ineligible、
  确认 409/400；确认卡缺失 -> 404；顾客调操作者确认端点 -> 401（无操作者
  登录态；0016 控制台必须登录，顾客身份不在本套件账号内）。
- 提议步接线（ADR 0043 延伸）：替身 LLM 提议 check_return_eligibility ->
  执行+模板回答；越狱提议 create_return -> 不在注册表被拒转人工。
- 忠实度闸（ADR 0044 §二）：擦边问句 -> 模板回退+fallback_reason；
  覆盖够高的单命中照旧生成（空 key 降级，无 fallback_reason 键）。
- 反馈分诊（ADR 0044 §四）：answer+citations 收「没有帮助」-> 逐 citation
  资产 last_verified_at=None -> 幂等 409；拒答消息 409；跨会话 404；
  无令牌 401；helpful=true 422。
"""

import os
import re
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from sse_helpers import parse_sse_events

ApiFixture = tuple[TestClient, Path]

_URL_ENV = "SUITE_TEST_DATABASE_URL"
_TOKEN_RE = re.compile(r"token=([0-9a-f]+)")


def _login(client: TestClient) -> None:
    assert (
        client.post(
            "/api/auth/login", json={"username": "operator", "password": "operator123"}
        ).status_code
        == 200
    )


def _ask(client: TestClient, session_id: int, question: str) -> list[tuple[str, dict]]:
    with client.stream(
        "POST",
        f"/api/service/sessions/{session_id}/messages",
        json={"content": question},
    ) as resp:
        assert resp.status_code == 200
        raw = "".join(resp.iter_text())
    return parse_sse_events(raw)


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
        raw = "".join(resp.iter_text())
    return parse_sse_events(raw)


def _publish(client: TestClient, content: bytes, title: str) -> int:
    # 幂等：同题资产已登记则复用（重复发布会让同一块以多个 asset_id 进索引，
    # 单命中假设被破坏、闸的触发判定随之漂移）
    existing = [a for a in client.get("/api/assets").json() if a.get("title") == title]
    if existing:
        return existing[0]["id"]
    resp = client.post(
        "/api/assets/register",
        files={"file": ("spec.txt", content, "text/plain")},
        data={"title": title},
    )
    assert resp.status_code == 201
    asset_id = resp.json()["id"]
    assert client.post(f"/api/assets/{asset_id}/publish").status_code == 200
    return asset_id


def _insert_order(url: str, order_no: str, days_ago: int, status: str = "已发货") -> None:
    with psycopg.connect(url, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO orders (order_no, status, items, events, placed_at)"
            " VALUES (%s, %s, '[]', '[]', now() - make_interval(days => %s))"
            " ON CONFLICT (order_no) DO NOTHING",
            (order_no, status, days_ago),
        )


def _order_status(url: str, order_no: str) -> str | None:
    """直读订单状态（第 44 刀：状态迁移必须落库，不能只看消息渲染）。"""
    with psycopg.connect(url, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT status FROM orders WHERE order_no = %s", (order_no,))
        row = cur.fetchone()
    return None if row is None else str(row[0])


def _order_event_count(url: str, order_no: str) -> int:
    """事件条数（第 44 刀：拒绝路径必须「状态与事件都不动」，只断言状态不够）。"""
    with psycopg.connect(url, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT jsonb_array_length(events) FROM orders WHERE order_no = %s", (order_no,))
        row = cur.fetchone()
    return 0 if row is None else int(row[0])


def _agent_messages(client: TestClient, session_id: int) -> list[dict[str, Any]]:
    detail = client.get(f"/api/service/sessions/{session_id}").json()
    return [m for m in detail["messages"] if m["role"] == "agent"]


@pytest.fixture(autouse=True)
def _secret(monkeypatch: pytest.MonkeyPatch) -> None:
    # 令牌密钥 env 隔离：替身/伪造令牌与应用进程同密钥（测试进程内可见）
    monkeypatch.setenv("RETURN_TOKEN_SECRET", "integration-return-secret")


@pytest.fixture()
def gate_env(api: ApiFixture) -> TestClient:
    """本模块预置：一张 5 天前订单（可退）、一张 30 天前订单（超窗）、
    叉子与筷子两份已发布资产。"""
    client, _ = api
    url = os.environ[_URL_ENV]
    _login(client)  # 每次重登：上一用例可能清过 cookie（顾客面用例）
    _insert_order(url, "SO-2001", days_ago=5)
    _insert_order(url, "SO-2002", days_ago=30)
    _login(client)
    _publish(client, "叉子：不锈钢\n".encode(), "叉子说明")
    _publish(client, "钛合金筷子产品说明\n筷长：26cm\n".encode(), "筷子规格")
    return client


# ---------- 两阶段全链 ----------


def test_two_phase_return_full_chain(gate_env: TestClient) -> None:
    client = gate_env
    sid = client.post("/api/service/sessions").json()["id"]
    url = os.environ[_URL_ENV]

    # 第 44 刀：确认前状态是「已发货」（种子口径），退货还没发生
    assert _order_status(url, "SO-2001") == "已发货"

    # 阶段一：单号+退货意图 -> 资格查询（快路径，零 LLM）
    events = _ask(client, sid, "SO-2001 我想退货")
    kinds = [e for e, _ in events]
    assert kinds[0] == "thinking"
    assert events[0][1]["text"] == "查询退货资格中…"
    tool_event = next(d for e, d in events if e == "tool")
    assert tool_event["name"] == "check_return_eligibility"
    assert tool_event["arg"] == "SO-2001"
    match = _TOKEN_RE.search(tool_event["result"])
    assert match is not None  # 资格=可退：工具条带待确认令牌
    token = match.group(1)
    complete = events[-1][1]
    assert complete["kind"] == "answer"
    assert complete["citations"] == []
    streamed = "".join(d["text"] for e, d in events if e == "delta")
    assert "等待客服确认" in streamed
    assert token not in streamed  # 顾客可见文本不带令牌

    # 操作者会话回读：资格消息已落库（回放完整）
    eligible_messages = [
        m
        for m in _agent_messages(client, sid)
        if m["tool"] and m["tool"]["name"] == "check_return_eligibility"
    ]
    assert len(eligible_messages) == 1
    message_id = eligible_messages[0]["id"]

    # 阶段二：操作者确认 -> 事件追加 + create_return 轨迹消息
    resp = client.post(
        f"/api/service/sessions/{sid}/confirm-return",
        json={"message_id": message_id, "confirmation_token": token},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["order_no"] == "SO-2001"
    assert any("退货申请已确认" in e["text"] for e in body["events"])

    # 确认动作本身落一条工具轨迹消息（回放：两步工具动作都在会话里）
    tools = [m["tool"]["name"] for m in _agent_messages(client, sid) if m["tool"]]
    assert tools == ["check_return_eligibility", "create_return"]

    # 顾客问「退货进度」-> 订单工具 -> 确认事件可见（演示闭环）
    progress = _ask(client, sid, "SO-2001 退货进度")
    streamed = "".join(d["text"] for e, d in progress if e == "delta")
    assert "退货申请已确认" in streamed
    # 第 44 刀（钉测）：确认后状态真迁移到「退货中」——阶段一/进度问句都读同一字段，
    # 若不迁移，这里会答回「已发货」而当场穿帮（纸糊#3）
    assert _order_status(url, "SO-2001") == "退货中"
    assert "当前状态：退货中" in streamed

    # 幂等：同一确认卡二次确认 -> 409，且文案仍是「已确认过」（钉死判定顺序：
    # duplicate 先于 status_not_returnable——此时状态已是退货中，若顺序反了会
    # 答成「状态不可发起退货」，契约就悄悄变了）
    again = client.post(
        f"/api/service/sessions/{sid}/confirm-return",
        json={"message_id": message_id, "confirmation_token": token},
    )
    assert again.status_code == 409
    assert "已确认过" in again.json()["detail"]


def test_confirm_refuses_non_returnable_status(gate_env: TestClient) -> None:
    """第 44 刀状态闸：阶段一只看时间窗，所以一张「已退款」的单子在窗内仍能拿到
    确认令牌——状态闸必须把它挡成 409，而不是把状态改写回「退货中」（状态机不回退）。"""
    client = gate_env
    url = os.environ[_URL_ENV]
    _insert_order(url, "SO-2003", days_ago=3, status="已退款")
    sid = client.post("/api/service/sessions").json()["id"]

    events = _ask(client, sid, "SO-2003 我想退货")
    tool_event = next(d for e, d in events if e == "tool")
    match = _TOKEN_RE.search(tool_event["result"])
    assert match is not None  # 窗内 -> 阶段一照旧给令牌（它不看状态）
    token = match.group(1)

    message_id = next(
        m["id"]
        for m in _agent_messages(client, sid)
        if m["tool"] and m["tool"]["name"] == "check_return_eligibility"
    )
    denied = client.post(
        f"/api/service/sessions/{sid}/confirm-return",
        json={"message_id": message_id, "confirmation_token": token},
    )
    assert denied.status_code == 409
    assert "状态不可发起退货" in denied.json()["detail"]
    # 拒绝即不写：状态与事件时间轴都必须原样（闸在 append 与赋值之前 return）
    assert _order_status(url, "SO-2003") == "已退款"
    assert _order_event_count(url, "SO-2003") == 0


def test_order_status_sets_are_consistent() -> None:
    """第 44 刀状态集自检（纯函数，无 DB）：种子用到的状态必须都在合法集内；可退
    前置态是合法集的子集——防「合法值集」与「实际写入值」两边各写一份漂移。"""
    from suite_api.services.order_tools import ORDER_STATUSES, RETURNABLE_STATUSES
    from suite_api.services.seed import SEED_ORDERS

    seed_statuses = {str(order["status"]) for order in SEED_ORDERS}
    assert seed_statuses <= ORDER_STATUSES
    assert RETURNABLE_STATUSES <= ORDER_STATUSES
    assert "退货中" in ORDER_STATUSES  # 第 44 刀新纳入的状态机成员
    assert "退货中" not in RETURNABLE_STATUSES  # 不可重复退货


def test_confirm_rejects_tampered_and_expired_tokens(gate_env: TestClient) -> None:
    from suite_api.services import return_tools

    client = gate_env
    sid = client.post("/api/service/sessions").json()["id"]
    events = _ask(client, sid, "SO-2001 我想退货")
    tool_event = next(d for e, d in events if e == "tool")
    message = next(
        m
        for m in _agent_messages(client, sid)
        if m["tool"] and m["tool"]["name"] == "check_return_eligibility"
    )
    url = f"/api/service/sessions/{sid}/confirm-return"

    # 篡改（改签名一个字符）
    good = _TOKEN_RE.search(tool_event["result"]).group(1)
    bad = ("0" if good[0] != "0" else "1") + good[1:]
    resp = client.post(url, json={"message_id": message["id"], "confirmation_token": bad})
    assert resp.status_code == 400

    # 过期（替身签发一张已过期的合法签名令牌）
    from datetime import UTC, datetime, timedelta

    expired = return_tools.make_token(
        "SO-2001",
        True,
        now=datetime.now(UTC) - timedelta(seconds=return_tools.TOKEN_TTL_SECONDS + 5),
    )
    resp = client.post(url, json={"message_id": message["id"], "confirmation_token": expired})
    assert resp.status_code == 400

    # 张冠李戴（SO-2002 的资格卡配 SO-2001 的令牌）——单号绑定在签名里
    events2 = _ask(client, sid, "SO-2002 我想退货")
    tool2 = next(d for e, d in events2 if e == "tool")
    # SO-2002 超窗：ineligible，工具条无 token（确认按钮无从谈起）
    assert "token" not in tool2["result"]
    message2 = [
        m
        for m in _agent_messages(client, sid)
        if m["tool"] and m["tool"]["name"] == "check_return_eligibility"
    ][-1]
    resp = client.post(url, json={"message_id": message2["id"], "confirmation_token": good})
    assert resp.status_code == 400


def test_confirm_requires_matching_message_card(gate_env: TestClient) -> None:
    client = gate_env
    sid = client.post("/api/service/sessions").json()["id"]
    # 会话里没有资格消息：任意 message_id -> 404
    resp = client.post(
        f"/api/service/sessions/{sid}/confirm-return",
        json={"message_id": 999999, "confirmation_token": "a" * 42},
    )
    assert resp.status_code == 404


def test_customer_cannot_call_operator_confirm(gate_env: TestClient) -> None:
    # 顾客无确认面：清掉操作者登录态后调确认端点 -> 401（0016 控制台必须登录；
    # 两阶段确认是操作者动作，ADR 0044「确认在人」）
    client = gate_env
    sid = client.post("/api/service/sessions").json()["id"]
    client.cookies.clear()
    resp = client.post(
        f"/api/service/sessions/{sid}/confirm-return",
        json={"message_id": 1, "confirmation_token": "a" * 42},
    )
    assert resp.status_code == 401


# ---------- 提议步接线（ADR 0043 延伸：模型提议资格查询/越狱提议被拒） ----------


def test_proposed_eligibility_tool_executes_and_answers(
    gate_env: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_proposal(system: str, user: str) -> str:
        return 'TOOL: check_return_eligibility {"order_no": "SO-2001"}'

    monkeypatch.setattr("suite_api.services.chat_engine.llm.complete_tool_proposal", fake_proposal)
    client = gate_env
    sid = client.post("/api/service/sessions").json()["id"]
    # 无单号问句走提议步（替身提议资格查询）-> 执行 -> 模板回答
    events = _ask(client, sid, "我想退货")
    tool_event = next(d for e, d in events if e == "tool")
    assert tool_event["name"] == "check_return_eligibility"
    complete = events[-1][1]
    assert complete["kind"] == "answer"
    assert _TOKEN_RE.search(complete["tool"]["result"]) is not None


def test_jailbroken_create_return_proposal_is_rejected(
    gate_env: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def jailbroken(system: str, user: str) -> str:
        return 'TOOL: create_return {"order_no": "SO-2001"}'

    monkeypatch.setattr("suite_api.services.chat_engine.llm.complete_tool_proposal", jailbroken)
    client = gate_env
    sid = client.post("/api/service/sessions").json()["id"]
    events = _ask(client, sid, "帮我直接把退货办了")
    complete = events[-1][1]
    assert complete["kind"] == "handoff"
    assert complete["tool"]["rejected"] is True
    assert complete["tool"]["name"] == "create_return"
    # 不在注册表 -> 到不了任何执行层：订单事件无新增
    detail = client.get(f"/api/service/sessions/{sid}").json()
    assert not any(
        m["tool"] and m["tool"].get("name") == "check_return_eligibility"
        for m in detail["messages"]
    )


# ---------- 忠实度闸 ----------


def test_low_coverage_falls_back_with_reason(gate_env: TestClient) -> None:
    client = gate_env
    sid = client.post("/api/service/sessions").json()["id"]
    events = _ask(client, sid, "这个叉子能不能进洗碗机消毒")
    complete = events[-1][1]
    assert complete["kind"] == "answer"
    assert complete["fallback"] is True
    assert complete["fallback_reason"] == "coverage"
    streamed = "".join(d["text"] for e, d in events if e == "delta")
    assert "不锈钢" in streamed  # 模板=证据原文，不编造


def test_normal_coverage_has_no_fallback_reason_key(gate_env: TestClient) -> None:
    client = gate_env
    sid = client.post("/api/service/sessions").json()["id"]
    # 覆盖够高的单命中照旧走生成（空 key 降级模板：fallback=True 但无 reason 键）
    events = _ask(client, sid, "叉子是什么材质的？")
    complete = events[-1][1]
    assert complete["fallback"] is True  # 空 LLM 凭证降级（conftest 口径）
    assert "fallback_reason" not in complete
    assert complete["citations"] != []


# ---------- 反馈分诊 ----------


def test_feedback_triages_and_is_idempotent(gate_env: TestClient) -> None:
    client = gate_env
    client.cookies.clear()  # 顾客通道无登录态
    created = client.post("/api/customer/sessions").json()
    sid, token = created["session_id"], created["token"]

    events = _customer_ask(client, sid, token, "筷长是多少？")
    complete = events[-1][1]
    assert complete["citations"] != []  # answer+citations 才有「没有帮助」入口
    message_id = complete["message_id"]

    asset_id = complete["citations"][0]["asset_id"]
    _login(client)  # 治理面资产详情要登录（cookie 清空过：顾客面用例）
    before = client.get(f"/api/assets/{asset_id}").json()
    # 发布=验证快照：反馈前资产有验证时间
    assert before["last_verified_at"] is not None

    resp = client.post(
        f"/api/customer/sessions/{sid}/messages/{message_id}/feedback",
        json={"helpful": False},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["triaged_asset_ids"] == [asset_id]
    after = client.get(f"/api/assets/{asset_id}").json()
    assert after["last_verified_at"] is None  # 撤销验证（复审队列承接）

    # 幂等：同消息二次反馈 409
    again = client.post(
        f"/api/customer/sessions/{sid}/messages/{message_id}/feedback",
        json={"helpful": False},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert again.status_code == 409


def test_feedback_rejects_wrong_shapes(gate_env: TestClient) -> None:
    client = gate_env
    client.cookies.clear()
    created = client.post("/api/customer/sessions").json()
    sid, token = created["session_id"], created["token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 拒答消息不收反馈（citations 空 -> 分诊无从谈起）
    refusal = _customer_ask(client, sid, token, "会员生日礼怎么领？")
    refusal_id = refusal[-1][1]["message_id"]
    assert (
        client.post(
            f"/api/customer/sessions/{sid}/messages/{refusal_id}/feedback",
            json={"helpful": False},
            headers=headers,
        ).status_code
        == 409
    )

    # 顾客消息（非 agent）同样不收反馈——非可反馈回答一律 404（不暴露
    # 消息存在性；仅 kind=answer 且 citations 非空的 agent 消息进反馈态）
    _login(client)
    detail = client.get(f"/api/service/sessions/{sid}").json()
    customer_message = next(m for m in detail["messages"] if m["role"] == "customer")
    assert (
        client.post(
            f"/api/customer/sessions/{sid}/messages/{customer_message['id']}/feedback",
            json={"helpful": False},
            headers=headers,
        ).status_code
        == 404
    )

    # 跨会话 message_id -> 404；无令牌 -> 401；helpful=true -> 422
    other = client.post("/api/customer/sessions").json()
    assert (
        client.post(
            f"/api/customer/sessions/{other['session_id']}/messages/{refusal_id}/feedback",
            json={"helpful": False},
            headers={"Authorization": f"Bearer {other['token']}"},
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/customer/sessions/{sid}/messages/{refusal_id}/feedback",
            json={"helpful": False},
        ).status_code
        == 401
    )
    events = _customer_ask(client, sid, token, "筷长是多少？")
    answer_id = events[-1][1]["message_id"]
    assert (
        client.post(
            f"/api/customer/sessions/{sid}/messages/{answer_id}/feedback",
            json={"helpful": True},
            headers=headers,
        ).status_code
        == 422
    )


def test_customer_channel_never_sees_write_credential(gate_env: TestClient) -> None:
    """审计刀 8 P1：两阶段写的 `confirmation_token` 只在操作者面出现。

    它写进工具条 result（确认端点据此执行 create_return）。此前 SSE 两通道同形状
    地把工具条原样下发，顾客浏览器里能看到这份**写动作凭证**——ADR 0044 的边界是
    「顾客与模型都不掌握创建权」。本用例同时钉住两侧：操作者面必须有、顾客面必须无。
    """
    client = gate_env
    sid = client.post("/api/service/sessions").json()["id"]
    tool_event = next(d for e, d in _ask(client, sid, "SO-2001 我想退货") if e == "tool")
    assert "token=" in tool_event["result"]  # 操作者面：确认要用，必须有

    client.cookies.clear()
    created = client.post("/api/customer/sessions").json()
    with client.stream(
        "POST",
        f"/api/customer/sessions/{created['session_id']}/messages",
        json={"content": "SO-2001 我想退货"},
        headers={"Authorization": f"Bearer {created['token']}"},
    ) as resp:
        raw = "".join(resp.iter_text())
    events = parse_sse_events(raw)
    customer_tool = next(d for e, d in events if e == "tool")
    assert "token=" not in customer_tool["result"]
    assert "token=" not in str(events[-1][1].get("tool"))
    # 工具条本身仍在（只是没有凭证）——顾客仍能看到「已发生的动作」
    assert customer_tool["name"] == "check_return_eligibility"
