"""库存工具集成测试（真 PG；conftest 的 skip 与 LLM 清理口径）。

第 14 刀契约（ADR 0037/0018/0024）：
- 种子：products.stock 回填（钛钢保温杯 42、瓶装水 0）；seed 幂等（跑两次
  行数与值不变）；NULL 存量行由下一次 seed 回填。
- 有货问题全事件序：thinking(查询库存中…) -> tool(get_stock(钛钢保温杯) ->
  有货 · 42 件) -> delta* -> complete（kind=answer、citations=[]、tool 同形）。
- 无货 stock=0 ->「暂时无货」事实 answer；NULL -> handoff（不产生缺口）。
- 第 16 刀修订（P1#3 词表+商品双前置）：词表命中但商品未命中（「小龙虾有货吗」）
  -> 回检索路径，无证据 refusal 落缺口（旧「未命中→handoff 不留缺口」作废）。
- 收窄后的泛词（「保温杯还剩多少毫升」「库存政策是什么」）零接触工具走检索；
  已发布库存政策文档可被「库存政策」问句命中（旧词表会永久吞掉它）。
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


def test_stock_word_hit_product_miss_falls_back_to_retrieval_refusal(api: ApiFixture) -> None:
    """第 16 刀 P1#3 双前置（真库全链路）：词表命中（「有货」在列）但 LCS 商品
    未命中 -> 回既有检索路径，不是 handoff 不是工具；此时发布集无小龙虾证据 ->
    refusal + 缺口（0024 留缺口归宿；旧「未命中→handoff 不留缺口」作废）。"""
    client, _ = api
    gaps_before = _gaps_count(client)
    sid = client.post("/api/service/sessions").json()["id"]

    events = _ask(client, sid, "小龙虾有货吗")
    kinds = [event for event, _ in events]
    assert "tool" not in kinds  # 工具零接触：不回 handoff
    assert events[0][1] == {"text": "正在检索已发布资产…"}  # 检索状态行
    complete = events[-1][1]
    assert complete["kind"] == "refusal"
    assert complete["handoff"] is True
    assert complete["citations"] == []
    assert complete["tool"] is None
    assert complete["gap_id"] is not None  # 拒答留缺口（0024）

    assert _gaps_count(client) == gaps_before + 1

    detail = client.get(f"/api/service/sessions/{sid}").json()
    agent_msg = next(m for m in detail["messages"] if m["role"] == "agent")
    assert agent_msg["kind"] == "refusal"
    assert agent_msg["tool"] is None


# ---------- 零漂移：规格问题仍检索 / 订单问题仍是订单工具 ----------


def test_spec_question_still_retrieves(api: ApiFixture) -> None:
    """「保温杯的净含量是多少」词表外：登记+发布文档后仍走检索并引用（不变）。"""
    client, _ = api
    _login(client)
    cup_id = next(p["id"] for p in client.get("/api/products").json() if p["name"] == "钛钢保温杯")
    files = {"file": ("spec.txt", _CUP_DOC, "text/plain")}
    registered = client.post(
        "/api/assets/register",
        files=files,
        data={"productId": str(cup_id), "title": "保温杯规格文档"},
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


# ---------- 第 16 刀 P1#3：词表收窄后误伤问句回检索（集成钉） ----------


def test_stock_policy_question_hits_published_policy_doc(api: ApiFixture) -> None:
    """「库存政策是什么」不再被词表吞掉：登记+发布库存政策文档后，该问句走
    检索并命中引用（旧词表含「库存」时此问句永远到不了检索——审计刀 3 P1#3）。"""
    client, _ = api
    _login(client)
    policy_doc = (
        "库存政策说明\n库存：现货商品付款后48小时内发货；预售商品以详情页时效为准。".encode()
    )
    registered = client.post(
        "/api/assets/register",
        files={"file": ("policy.txt", policy_doc, "text/plain")},
        data={"title": "库存政策说明"},
    )
    assert registered.status_code == 201
    policy_id: int = registered.json()["id"]
    assert client.post(f"/api/assets/{policy_id}/publish").status_code == 200

    sid = client.post("/api/service/sessions").json()["id"]
    events = _ask(client, sid, "库存政策是什么")
    kinds = [event for event, _ in events]
    assert "tool" not in kinds  # 泛词「库存」不再分派工具
    assert events[0][1] == {"text": "正在检索已发布资产…"}
    complete = events[-1][1]
    assert complete["kind"] == "answer"
    assert {"asset_id": policy_id, "version_no": 1} in complete["citations"]


def test_remaining_capacity_question_retrieves_not_stock_tool(api: ApiFixture) -> None:
    """「保温杯还剩多少毫升」是规格问句（含「剩」+商品名）：词表收窄后零接触
    库存工具、走检索（命中上一用例已发布的保温杯规格文档）——旧词表下会被
    误答「有货 42 件」，正是词条 _Avoid_「用规格文档回答有没有货」的反向误伤。"""
    client, _ = api
    _login(client)
    sid = client.post("/api/service/sessions").json()["id"]
    events = _ask(client, sid, "保温杯还剩多少毫升？")
    kinds = [event for event, _ in events]
    assert "tool" not in kinds  # 库存工具零接触
    assert events[0][1] == {"text": "正在检索已发布资产…"}
    complete = events[-1][1]
    assert complete["kind"] == "answer"  # 检索命中（既有已发布规格文档），非库存模板
    assert complete["tool"] is None
    assert complete["citations"]  # 证据来自检索引用（0007）
    streamed = "".join(d["text"] for e, d in events if e == "delta")
    assert "有货" not in streamed  # 绝不出现「有货 N 件」


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
