"""库存工具集成测试（真 PG；conftest 的 skip 与 LLM 清理口径）。

第 14 刀契约（ADR 0037/0018/0024）：
- 种子：products.stock 回填（钛钢保温杯 42、瓶装水 0）；seed 幂等（跑两次
  行数与值不变）；NULL 存量行由下一次 seed 回填。
- 有货问题全事件序：thinking(查询库存中…) -> tool(get_stock(钛钢保温杯) ->
  有货 · 42 件) -> delta* -> complete（kind=answer、citations=[]、tool 同形）。
- 无货 stock=0 ->「暂时无货」事实 answer；NULL -> handoff（不产生缺口）。
- 「小龙虾有货吗」商品未命中 -> handoff + knowledge_gaps 计数不变。
- 规格问题「保温杯的净含量是多少」零漂移：仍检索、引用已发布资产、无 tool。
- 订单问题不受影响（thinking 仍「查询订单中…」）。
- 顾客通道同形状（complete 有 tool、无 gap_id）。
"""

import os
from pathlib import Path
from typing import Any

import psycopg
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sse_helpers import parse_sse_events

from suite_api.db import to_sqlalchemy_url
from suite_api.services.seed import SEED_PRODUCTS, seed_startup_data

ApiFixture = tuple[TestClient, Path]

_URL_ENV = "SUITE_TEST_DATABASE_URL"

_CUP_DOC = "钛钢保温杯产品说明\n净含量：480ml\n材质牌号未标注，详见吊牌。".encode()


def _login(client: TestClient) -> None:
    assert client.post("/api/auth/login", json={"username": "operator", "password": "operator123"}).status_code == 200


def _ask(client: TestClient, session_id: int, question: str) -> list[tuple[str, dict]]:
    with client.stream(
        "POST",
        f"/api/service/sessions/{session_id}/messages",
        json={"content": question},
    ) as resp:
        assert resp.status_code == 200
        raw = "".join(resp.iter_text())
    return parse_sse_events(raw)


def _stock_of(url: str, name: str) -> int | None:
    with psycopg.connect(url) as conn, conn.cursor() as cur:
        cur.execute("SELECT stock FROM products WHERE name = %s", (name,))
        return cur.fetchone()[0]


def _products_count(url: str) -> int:
    with psycopg.connect(url) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM products")
        return int(cur.fetchone()[0])


def _set_stock(url: str, name: str, value: int | None) -> None:
    with psycopg.connect(url, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("UPDATE products SET stock = %s WHERE name = %s", (value, name))


def _gaps_count(client: TestClient) -> int:
    _login(client)
    return len(client.get("/api/knowledge-gaps", params={"status": "open"}).json()) + len(
        client.get("/api/knowledge-gaps", params={"status": "resolved"}).json()
    )


def _reseed(url: str) -> None:
    engine = create_engine(to_sqlalchemy_url(url))
    try:
        seed_startup_data(engine, "operator123")
    finally:
        engine.dispose()


# ---------- 种子 ----------


def test_seed_stock_backfilled(api: ApiFixture) -> None:
    url = os.environ[_URL_ENV]
    assert {p["name"]: p["stock"] for p in SEED_PRODUCTS} == {"瓶装水": 0, "钛钢保温杯": 42}
    assert _stock_of(url, "钛钢保温杯") == 42
    assert _stock_of(url, "瓶装水") == 0


def test_seed_startup_data_idempotent_for_stock(api: ApiFixture) -> None:
    """跑两次 seed（模拟重复 lifespan）：products 行数与 stock 值不变。"""
    url = os.environ[_URL_ENV]
    before = (_products_count(url), _stock_of(url, "钛钢保温杯"), _stock_of(url, "瓶装水"))
    _reseed(url)
    _reseed(url)
    assert (
        _products_count(url),
        _stock_of(url, "钛钢保温杯"),
        _stock_of(url, "瓶装水"),
    ) == before


def test_seed_backfills_null_stock_existing_row(api: ApiFixture) -> None:
    """迁移只加列（存量行 NULL）：手工清空后 seed 回填，且不覆盖已有值。"""
    url = os.environ[_URL_ENV]
    _set_stock(url, "钛钢保温杯", None)
    _set_stock(url, "瓶装水", 7)  # 演示中手改的值：seed 不覆盖
    _reseed(url)
    try:
        assert _stock_of(url, "钛钢保温杯") == 42
        assert _stock_of(url, "瓶装水") == 7
    finally:
        _set_stock(url, "瓶装水", 0)


# ---------- 有货：全事件序 ----------


def test_stock_in_stock_full_event_sequence(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    sid = client.post("/api/service/sessions").json()["id"]

    events = _ask(client, sid, "钛钢保温杯有货吗？")
    kinds = [event for event, _ in events]
    assert kinds[0:2] == ["thinking", "tool"]
    assert kinds[-1] == "complete"
    assert "delta" in kinds

    assert events[0][1] == {"text": "查询库存中…"}
    tool = events[1][1]
    assert tool == {"name": "get_stock", "arg": "钛钢保温杯", "result": "有货 · 42 件"}

    complete = events[-1][1]
    assert complete["kind"] == "answer"
    assert complete["handoff"] is False
    assert complete["citations"] == []  # 库存是商品列不是资产，不可引用（0002/0037）
    assert complete["tool"] == tool
    assert complete["fallback"] is False
    assert complete["gap_id"] is None

    streamed = "".join(d["text"] for e, d in events if e == "delta")
    assert streamed == "钛钢保温杯有货，当前库存 42 件。"

    # 简称「保温杯」经 LCS 匹配同一商品（词条 _Avoid_：不再拿规格文档答有没有货）
    short = _ask(client, sid, "保温杯有货吗")
    assert short[1][1] == tool

    # 回放完整性：工具条随消息落库，会话详情回读还原
    detail = client.get(f"/api/service/sessions/{sid}").json()
    agent_msgs = [m for m in detail["messages"] if m["role"] == "agent"]
    assert agent_msgs[0]["tool"] == tool
    assert agent_msgs[0]["kind"] == "answer"


# ---------- 无货 / 未设置 / 未命中 ----------


def test_stock_zero_is_factual_answer(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    sid = client.post("/api/service/sessions").json()["id"]

    events = _ask(client, sid, "瓶装水有货吗？")
    assert events[1][1] == {"name": "get_stock", "arg": "瓶装水", "result": "暂时无货"}
    complete = events[-1][1]
    assert complete["kind"] == "answer"  # 0 是事实数据不是失败
    assert complete["handoff"] is False
    streamed = "".join(d["text"] for e, d in events if e == "delta")
    assert streamed == "瓶装水暂时无货。"


def test_stock_null_handoff(api: ApiFixture) -> None:
    url = os.environ[_URL_ENV]
    client, _ = api
    _login(client)
    gaps_before = _gaps_count(client)
    _set_stock(url, "瓶装水", None)
    try:
        sid = client.post("/api/service/sessions").json()["id"]
        events = _ask(client, sid, "瓶装水还有货吗？")
        assert events[1][1] == {"name": "get_stock", "arg": "瓶装水", "result": "未设置"}
        complete = events[-1][1]
        assert complete["kind"] == "handoff"
        assert complete["handoff"] is True
        assert complete["citations"] == []
        streamed = "".join(d["text"] for e, d in events if e == "delta")
        assert streamed == "瓶装水库存未设置，已转人工。"
        assert _gaps_count(client) == gaps_before  # 0024：不产生缺口
    finally:
        _set_stock(url, "瓶装水", 0)


def test_stock_product_miss_handoff_no_gap(api: ApiFixture) -> None:
    client, _ = api
    gaps_before = _gaps_count(client)
    sid = client.post("/api/service/sessions").json()["id"]

    events = _ask(client, sid, "小龙虾有货吗")
    kinds = [event for event, _ in events]
    assert kinds[1] == "tool"
    assert events[1][1] == {"name": "get_stock", "arg": "小龙虾有货吗", "result": "未找到商品"}
    complete = events[-1][1]
    assert complete["kind"] == "handoff"
    assert complete["handoff"] is True
    assert complete["citations"] == []
    assert complete["gap_id"] is None
    streamed = "".join(d["text"] for e, d in events if e == "delta")
    assert streamed == "没有找到对应商品，已转人工。"

    assert _gaps_count(client) == gaps_before  # 不检索不缺口（0018/0024）

    detail = client.get(f"/api/service/sessions/{sid}").json()
    agent_msg = next(m for m in detail["messages"] if m["role"] == "agent")
    assert agent_msg["kind"] == "handoff"
    assert agent_msg["tool"]["result"] == "未找到商品"


# ---------- 零漂移：规格问题仍检索 / 订单问题仍是订单工具 ----------


def test_spec_question_still_retrieves(api: ApiFixture) -> None:
    """「保温杯的净含量是多少」词表外：登记+发布文档后仍走检索并引用（不变）。"""
    client, _ = api
    _login(client)
    cup_id = next(p["id"] for p in client.get("/api/products").json() if p["name"] == "钛钢保温杯")
    files = {"file": ("spec.txt", _CUP_DOC, "text/plain")}
    registered = client.post(
        "/api/assets/register", files=files, data={"productId": str(cup_id), "title": "保温杯规格文档"}
    )
    assert registered.status_code == 201
    doc_id: int = registered.json()["id"]
    client.patch(
        f"/api/assets/{doc_id}/versions/1/fields", json={"材质": "钛钢", "净含量": "480ml"}
    )
    assert client.post(f"/api/assets/{doc_id}/publish").status_code == 200

    sid = client.post("/api/service/sessions").json()["id"]
    events = _ask(client, sid, "保温杯的净含量是多少？")
    kinds = [event for event, _ in events]
    assert events[0][1] == {"text": "正在检索已发布资产…"}  # 检索状态行原样
    assert "tool" not in kinds  # 库存工具零接触
    complete = events[-1][1]
    assert complete["kind"] == "answer"
    assert complete["citations"] == [{"asset_id": doc_id, "version_no": 1}]
    assert complete["tool"] is None
    streamed = "".join(d["text"] for e, d in events if e == "delta")
    assert "480ml" in streamed

    # 发布后库存问题也不受影响：仍走工具、citations 恒空（不拿规格文档顶）
    stock_events = _ask(client, sid, "钛钢保温杯有货吗？")
    assert stock_events[-1][1]["citations"] == []
    assert stock_events[1][1]["name"] == "get_stock"


def test_order_question_still_order_tool(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    sid = client.post("/api/service/sessions").json()["id"]
    events = _ask(client, sid, "我的订单 SO-1001 到哪了？")
    assert events[0][1] == {"text": "查询订单中…"}
    tool: dict[str, Any] = events[1][1]
    assert tool["name"] == "get_order_status"
    assert tool == {"name": "get_order_status", "arg": "SO-1001", "result": "已发货 · 2 个物流事件"}
    assert events[-1][1]["kind"] == "answer"

    # 同含单号与库存词：订单分派优先（第 13 刀 1.5 在第 14 刀 1.6 之前）
    both = _ask(client, sid, "SO-1001 里的保温杯有货吗？")
    assert both[1][1]["name"] == "get_order_status"
    assert both[0][1] == {"text": "查询订单中…"}


# ---------- 顾客通道同形状 ----------


def test_customer_channel_stock_same_shape(api: ApiFixture) -> None:
    client, _ = api
    client.cookies.clear()  # 顾客无登录态
    created = client.post("/api/customer/sessions").json()
    sid, token = created["session_id"], created["token"]
    with client.stream(
        "POST",
        f"/api/customer/sessions/{sid}/messages",
        json={"content": "钛钢保温杯有货吗？"},
        headers={"Authorization": f"Bearer {token}"},
    ) as resp:
        assert resp.status_code == 200
        events = parse_sse_events("".join(resp.iter_text()))

    kinds = [event for event, _ in events]
    assert kinds[0:2] == ["thinking", "tool"]
    assert kinds[-1] == "complete"
    assert events[0][1] == {"text": "查询库存中…"}
    assert events[1][1] == {"name": "get_stock", "arg": "钛钢保温杯", "result": "有货 · 42 件"}
    complete = events[-1][1]
    assert complete["kind"] == "answer"
    assert complete["citations"] == []
    assert complete["tool"] == events[1][1]  # 两通道同形状不裁剪
    assert "gap_id" not in complete  # 顾客白名单口径不回归
