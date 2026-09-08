"""订单工具集成测试（真 PG；conftest 的 skip 与 LLM 清理口径）。

第 13 刀契约（ADR 0036/0018/0024）：
- 种子：orders ≥3（已发货/运输中/已签收）；seed_startup_data 重复跑不重复插。
- 订单问题全事件序：thinking(查询订单中…) -> tool -> delta* -> complete
  （kind=answer、citations=[]、complete.tool 形状）；模板内容含状态文案。
- 查无 SO-9999 -> kind=handoff + handoff=True + knowledge_gaps 计数不变
  （0024 Must 锚点：工具失败不产生缺口，不拿检索顶）。
- 顾客通道同形状（complete 有 tool、无 gap_id）。
- 工具记录随消息落库：会话详情回读还原工具条（回放完整性）。
- 空库（不发布任何资产）订单问题也走工具——不依赖资产。
"""

import os
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sse_helpers import parse_sse_events

from suite_api.db import to_sqlalchemy_url
from suite_api.services.seed import SEED_ORDERS, seed_startup_data

ApiFixture = tuple[TestClient, Path]

_URL_ENV = "SUITE_TEST_DATABASE_URL"


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


def _orders_count(url: str) -> int:
    with psycopg.connect(url) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM orders")
        return int(cur.fetchone()[0])


def _gaps_count(client: TestClient) -> int:
    _login(client)
    return len(client.get("/api/knowledge-gaps", params={"status": "open"}).json()) + len(
        client.get("/api/knowledge-gaps", params={"status": "resolved"}).json()
    )


# ---------- 种子 ----------


def test_seed_orders_present_and_cover_statuses(api: ApiFixture) -> None:
    url = os.environ[_URL_ENV]  # api fixture 已保证非空（否则整个模块 skip）
    assert _orders_count(url) >= 3
    assert {o["status"] for o in SEED_ORDERS} == {"已发货", "运输中", "已签收"}
    # 单号唯一索引生效：重复 order_no 插入被拒
    with pytest.raises(psycopg.errors.UniqueViolation):
        with psycopg.connect(url, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO orders (order_no, status, items, events, placed_at)"
                " VALUES ('SO-1001', '已发货', '[]', '[]', now())"
            )


def test_seed_startup_data_idempotent_for_orders(api: ApiFixture) -> None:
    """跑两次 seed（模拟重复 lifespan）：orders 行数不变。"""
    url = os.environ[_URL_ENV]
    before = _orders_count(url)
    engine = create_engine(to_sqlalchemy_url(url))
    try:
        seed_startup_data(engine, "operator123")
        seed_startup_data(engine, "operator123")
    finally:
        engine.dispose()
    assert _orders_count(url) == before


# ---------- 订单路径全事件序 ----------


def test_order_question_full_event_sequence(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    sid = client.post("/api/service/sessions").json()["id"]

    events = _ask(client, sid, "我的订单 SO-1001 到哪了？")
    kinds = [event for event, _ in events]
    assert kinds[0] == "thinking"
    assert kinds[1] == "tool"
    assert kinds[-1] == "complete"
    assert "delta" in kinds

    assert events[0][1] == {"text": "查询订单中…"}
    tool = events[1][1]
    assert tool == {"name": "get_order_status", "arg": "SO-1001", "result": "已发货 · 2 个物流事件"}

    complete = events[-1][1]
    assert complete["kind"] == "answer"
    assert complete["handoff"] is False
    assert complete["citations"] == []  # 订单不是资产，不可引用（0002/0036）
    assert complete["tool"] == tool
    assert complete["fallback"] is False  # 模板组装是正式产出，非降级
    assert complete["gap_id"] is None

    streamed = "".join(d["text"] for e, d in events if e == "delta")
    assert "订单 SO-1001 当前状态：已发货。" in streamed
    assert "商品：瓶装水 ×2、钛钢保温杯 ×1。" in streamed
    assert "物流轨迹：" in streamed and "杭州转运中心" in streamed

    # 小写单号命中同一工具路径（大小写不敏感 + 归一）
    lower = _ask(client, sid, "so-1001 到哪了")
    assert lower[1][1] == tool
    assert lower[-1][1]["kind"] == "answer"

    # 回放完整性：工具条随消息落库，会话详情回读还原
    detail = client.get(f"/api/service/sessions/{sid}").json()
    agent_msgs = [m for m in detail["messages"] if m["role"] == "agent"]
    assert agent_msgs[0]["tool"] == tool
    assert agent_msgs[0]["kind"] == "answer"


def test_order_three_statuses_render(api: ApiFixture) -> None:
    """种子三单逐一命中：状态文案与摘要随订单变化，模板确定性。"""
    client, _ = api
    _login(client)
    sid = client.post("/api/service/sessions").json()["id"]
    expected = {
        "SO-1001": ("已发货 · 2 个物流事件", "已发货"),
        "SO-1002": ("运输中 · 2 个物流事件", "运输中"),
        "SO-1003": ("已签收 · 3 个物流事件", "已签收"),
    }
    for order_no, (summary, status) in expected.items():
        events = _ask(client, sid, f"订单 {order_no} 现在什么情况？")
        assert events[1][1]["result"] == summary
        assert events[-1][1]["kind"] == "answer"
        streamed = "".join(d["text"] for e, d in events if e == "delta")
        assert f"当前状态：{status}。" in streamed


# ---------- 查无 -> handoff，不产生缺口 ----------


def test_order_not_found_handoff_no_gap(api: ApiFixture) -> None:
    client, _ = api
    gaps_before = _gaps_count(client)
    sid = client.post("/api/service/sessions").json()["id"]

    events = _ask(client, sid, "订单 SO-9999 呢？")
    kinds = [event for event, _ in events]
    assert kinds[1] == "tool"
    assert events[1][1] == {"name": "get_order_status", "arg": "SO-9999", "result": "未找到"}
    complete = events[-1][1]
    assert complete["kind"] == "handoff"  # 新 kind：不撞 refusal 的缺口分支
    assert complete["handoff"] is True
    assert complete["citations"] == []
    assert complete["gap_id"] is None
    streamed = "".join(d["text"] for e, d in events if e == "delta")
    assert streamed == "订单 SO-9999 未找到，已转人工，请人工核实单号。"

    assert _gaps_count(client) == gaps_before  # 0024 Must 锚点：计数不变

    # 交接摘要随消息落库，回读 kind=handoff + 工具条
    detail = client.get(f"/api/service/sessions/{sid}").json()
    agent_msg = next(m for m in detail["messages"] if m["role"] == "agent")
    assert agent_msg["kind"] == "handoff"
    assert agent_msg["handoff"] is True
    assert agent_msg["tool"]["result"] == "未找到"


# ---------- 顾客通道同形状 ----------


def test_customer_channel_order_same_shape(api: ApiFixture) -> None:
    client, _ = api
    client.cookies.clear()  # 顾客无登录态
    created = client.post("/api/customer/sessions").json()
    sid, token = created["session_id"], created["token"]
    with client.stream(
        "POST",
        f"/api/customer/sessions/{sid}/messages",
        json={"content": "我的订单 SO-1001 到哪了？"},
        headers={"Authorization": f"Bearer {token}"},
    ) as resp:
        assert resp.status_code == 200
        events = parse_sse_events("".join(resp.iter_text()))

    kinds = [event for event, _ in events]
    assert kinds[0:2] == ["thinking", "tool"]
    assert kinds[-1] == "complete"
    assert events[1][1]["name"] == "get_order_status"
    complete = events[-1][1]
    assert complete["kind"] == "answer"
    assert complete["tool"] == events[1][1]  # 两通道同形状不裁剪
    assert "gap_id" not in complete  # 顾客白名单口径不回归


# ---------- 非订单问题零漂移（HTTP 层复核） ----------


def test_non_order_question_untouched(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    sid = client.post("/api/service/sessions").json()["id"]
    events = _ask(client, sid, "会员日有什么优惠？")  # 无单号、无证据 -> 既有拒答
    kinds = [event for event, _ in events]
    assert kinds[0] == "thinking"
    assert events[0][1] == {"text": "正在检索已发布资产…"}
    assert "tool" not in kinds
    complete = events[-1][1]
    assert complete["kind"] == "refusal"
    assert complete["tool"] is None
    assert complete["gap_id"] is not None  # 拒答照常落缺口：既有语义未动
