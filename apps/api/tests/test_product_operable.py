"""第 41 刀（ADR 0045）：商品可运营 + 目录可答。

- CRUD：POST 上新 / PATCH 改档改价（401/404/409/422 语义对齐既有）。
- 改价即时生效 + audit 产品档（非资产 publish）；非改价 PATCH 不留痕；
  发布写回不碰价格。
- 目录回落：列举/报价两形态（工具式模板：不调 LLM、citations 空、
  kind=answer、tool= catalog）；miss 沿拒答+转人工+缺口（去补=上新/改价）。
- 零回归闸：不纯列举（会员日优惠）、裸多少+规格词（毫升/净含量/作者）、
  显式真人、retrieve 有命中——一律不进回落。

纯函数部分（catalog_intent/format/render/截断）无 DB 可跑；其余需真 PG
（conftest 的 SUITE_TEST_DATABASE_URL，未设则 skip）。
"""

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from suite_api.models import Product, ServiceSession
from suite_api.services.catalog_tools import (
    catalog_intent,
    format_price,
    render_listing,
    try_catalog_answer,
)
from suite_api.services.chat_engine import run_ask

ApiFixture = tuple[TestClient, Path]


def _login(client: TestClient) -> None:
    assert (
        client.post(
            "/api/auth/login", json={"username": "operator", "password": "operator123"}
        ).status_code
        == 200
    )


def _run_question(client: TestClient, question: str):
    factory = client.app.state.session_factory
    with factory() as db:
        session = ServiceSession(status="active")
        db.add(session)
        db.commit()
        outcome = asyncio.run(run_ask(db, session, question))
        return outcome, session.id


def _price_change_rows(client: TestClient) -> list[dict[str, Any]]:
    return [r for r in client.get("/api/audit").json() if r["action"] == "price_change"]


# ---------- 纯函数（无 DB） ----------


@pytest.mark.parametrize(
    ("question", "want"),
    [
        ("你们店里卖什么？", "listing"),
        ("卖什么", "listing"),
        ("有什么", "listing"),
        ("目录", "listing"),
        ("店里在售哪些？", "listing"),
        ("钛钢保温杯多少钱？", "price"),
        ("瓶装水价格是多少", "price"),
        ("帆布包售价多少", "price"),
        ("筷长是多少？", "price"),  # 无规格词的裸多少是价格意图（匹配不到照旧拒答）
        ("会员积分怎么兑换礼品？", None),
        ("保温杯的净含量是多少？", None),  # 规格词闸：净含量
        ("保温杯还剩多少毫升？", None),  # 规格词闸：毫升
        ("钛杯净含量多少", None),  # 规格词闸（多轮记忆里的问法同口径）
        ("会员日有什么优惠？", None),  # 不纯列举：优惠问不是目录问
        ("保温杯有什么卖点", None),  # 不纯列举：卖点问不是目录问
        ("卖什么？转人工", None),  # 显式要真人不抢
        ("多少钱？找真人客服", None),  # 显式要真人不抢
        ("库存政策是什么", None),
        ("小龙虾有货吗？", None),
    ],
)
def test_catalog_intent(question: str, want: str | None) -> None:
    assert catalog_intent(question) == want


@pytest.mark.parametrize(
    ("cents", "currency", "want"),
    [
        (12900, "CNY", "129元"),
        (300, "CNY", "3元"),
        (399, "CNY", "3.99元"),
        (3050, "CNY", "30.5元"),
        (0, "CNY", "0元"),
        (1000, "USD", "10 USD"),
        (None, "CNY", "价格未定"),
        (500, None, "5元"),
    ],
)
def test_format_price(cents: int | None, currency: str | None, want: str) -> None:
    assert format_price(cents, currency) == want


def _mem_product(pid: int, name: str, price: int | None) -> Product:
    return Product(
        id=pid,
        name=name,
        category="食品",
        spec_schema={},
        spec_values={},
        price_cents=price,
        currency="CNY",
    )


def test_render_listing_lists_priced_only_and_truncates_at_8() -> None:
    """列举只列已定价（前 8 件）：未定价行不逐条铺，避免「价格未定」糊屏（UX-A2）。"""
    products = [_mem_product(i, f"商品{i:02d}", 100) for i in range(1, 13)]
    products.append(_mem_product(99, "未定价货", None))
    text = render_listing(products)
    assert "共 13 件" in text
    assert "已定价 12 件" in text
    assert "商品08" in text
    assert "商品09" not in text  # 超出 MAX_LISTED 的已定价件不列
    assert "仅列出前 8 件已定价商品" in text
    assert "未定价货" not in text  # 未定价商品不进清单
    assert "其余未定价，直接问商品名" in text


def test_render_listing_without_prices_invites_asking_by_name() -> None:
    """一件都没定价：不写「价格未定」，改为报总数并请顾客直接问商品名。"""
    text = render_listing([_mem_product(1, "瓶装水", None)])
    assert "共 1 件" in text
    assert "都没有公布价格" in text
    assert "价格未定" not in text
    assert "直接问商品名" in text


def test_render_listing_all_priced_never_claims_unpriced_left() -> None:
    """全店已定价时不得说「其余未定价」（自审揪出的事实错误：店里没有未定价商品）。"""
    text = render_listing([_mem_product(i, f"商品{i:02d}", 100) for i in range(1, 4)])
    assert "均已定价" in text
    assert "共 3 件" in text
    assert "其余未定价" not in text
    # 全已定价且超过上限：截断说明保留，收尾改说「直接问名字」，仍不得谎称未定价
    many = render_listing([_mem_product(i, f"商品{i:02d}", 100) for i in range(1, 13)])
    assert "仅列出前 8 件已定价商品" in many
    assert "其余未定价" not in many
    assert "其余商品直接问名字" in many


def test_render_listing_exactly_max_priced_has_no_truncation_note() -> None:
    """恰好 MAX_LISTED 件已定价：不出现截断说明（边界，防 off-by-one）。"""
    text = render_listing([_mem_product(i, f"商品{i:02d}", 100) for i in range(1, 9)])
    assert "商品08" in text
    assert "仅列出前" not in text
    assert "其余未定价" not in text


def test_render_listing_empty_store_is_sane() -> None:
    """空店生产路径由调用方挡住（try_catalog_answer 返回 None），纯函数仍给人话。"""
    assert render_listing([]) == "本店还没有上架商品。"


def test_try_catalog_answer_never_fires_on_hits() -> None:
    from unittest.mock import MagicMock

    assert try_catalog_answer(MagicMock(), "你们卖什么？", [{"asset_id": 1}]) is None


# ---------- CRUD（需 DB） ----------


def test_product_write_endpoints_require_login(api: ApiFixture) -> None:
    client, _ = api
    client.cookies.clear()
    assert client.post("/api/products", json={"name": "X", "category": "食品"}).status_code == 401
    assert client.patch("/api/products/1", json={"price_cents": 100}).status_code == 401
    _login(client)


def test_post_product_defaults_and_get_roundtrip(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    resp = client.post(
        "/api/products", json={"name": "帆布包41", "category": "器皿", "price_cents": 5900}
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["currency"] == "CNY"
    assert body["price_cents"] == 5900
    # 未显式给 spec_schema 即按类目模板派生（器皿：净含量+材质）
    assert body["spec_schema"] == {"净含量": {"required": True}, "材质": {"required": True}}
    detail = client.get(f"/api/products/{body['id']}").json()
    assert detail["price_cents"] == 5900
    assert detail["currency"] == "CNY"


def test_post_product_validation_422_and_409(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    assert (
        client.post(
            "/api/products", json={"name": "负价", "category": "食品", "price_cents": -1}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/products", json={"name": "布尔价", "category": "食品", "price_cents": True}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/products", json={"name": "坏币", "category": "食品", "currency": "人民币"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/products", json={"name": "坏币2", "category": "食品", "currency": "US"}
        ).status_code
        == 422
    )
    assert client.post("/api/products", json={"name": "  ", "category": "食品"}).status_code == 422
    assert (
        client.post(
            "/api/products",
            json={
                "name": "坏模板",
                "category": "食品",
                "spec_schema": {"自造字段": {"required": True}},
            },
        ).status_code
        == 422
    )
    # 食品模板恰好两键：子集同样 422（键集合须一致）
    assert (
        client.post(
            "/api/products",
            json={
                "name": "缺键",
                "category": "食品",
                "spec_schema": {"净含量": {"required": True}},
            },
        ).status_code
        == 422
    )
    dup = client.post("/api/products", json={"name": "重名41", "category": "食品"})
    assert dup.status_code == 201, dup.text
    assert (
        client.post("/api/products", json={"name": "重名41", "category": "食品"}).status_code == 409
    )
    assert client.patch("/api/products/999999", json={"price_cents": 1}).status_code == 404


def test_patch_price_immediate_and_audit_product_doc(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    created = client.post("/api/products", json={"name": "改价41", "category": "食品"}).json()
    pid = created["id"]
    assert created["price_cents"] is None  # 未定价
    before = len(_price_change_rows(client))
    resp = client.patch(f"/api/products/{pid}", json={"price_cents": 1250})
    assert resp.status_code == 200, resp.text
    assert resp.json()["price_cents"] == 1250
    # 即时生效：同事务外 GET 可见（回落读实时行价的前置）
    assert client.get(f"/api/products/{pid}").json()["price_cents"] == 1250
    rows = _price_change_rows(client)
    assert len(rows) == before + 1
    row = rows[-1]
    assert row["product_id"] == pid
    assert row["asset_id"] is None  # 产品档，非资产 publish
    assert row["version_no"] is None
    # 同值重写不留痕（真变才记）
    assert client.patch(f"/api/products/{pid}", json={"price_cents": 1250}).status_code == 200
    assert len(_price_change_rows(client)) == before + 1
    # 非改价 PATCH（改名）不留痕
    assert client.patch(f"/api/products/{pid}", json={"name": "改价41b"}).status_code == 200
    assert len(_price_change_rows(client)) == before + 1
    # 显式 null=改回未定价，留痕
    assert client.patch(f"/api/products/{pid}", json={"price_cents": None}).status_code == 200
    assert client.get(f"/api/products/{pid}").json()["price_cents"] is None
    assert len(_price_change_rows(client)) == before + 2


def test_patch_category_change_resets_schema(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    created = client.post("/api/products", json={"name": "转类41", "category": "食品"}).json()
    assert set(created["spec_schema"]) == {"净含量", "保质期"}
    moved = client.patch(f"/api/products/{created['id']}", json={"category": "器皿"}).json()
    assert set(moved["spec_schema"]) == {"净含量", "材质"}


def _upload(client: TestClient, content: bytes, *, product_id: int, title: str):
    files = {"file": ("spec.txt", content, "text/plain")}
    return client.post(
        "/api/assets/register",
        files=files,
        data={"productId": str(product_id), "title": title},
    )


def test_publish_writeback_does_not_touch_price(api: ApiFixture) -> None:
    """发布写回只动 spec_values：改价后的行价在发布前后不变。"""
    client, _ = api
    _login(client)
    created = client.post(
        "/api/products", json={"name": "写回41", "category": "食品", "price_cents": 888}
    ).json()
    pid = created["id"]
    resp = _upload(
        client,
        "【产品规格】\n净含量：550毫升\n保质期：12个月".encode(),
        product_id=pid,
        title="写回41规格",
    )
    assert resp.status_code == 201, resp.text
    asset_id = resp.json()["id"]
    # 机洗值须人工确认后才可发布（既有闸门）；确认后再发布
    confirmed = client.patch(
        f"/api/assets/{asset_id}/versions/1/fields",
        json={"净含量": "550毫升", "保质期": "12个月"},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert client.post(f"/api/assets/{asset_id}/publish").status_code == 200
    detail = client.get(f"/api/products/{pid}").json()
    assert detail["price_cents"] == 888  # 写回不动价格
    assert detail["spec_values"]["净含量"]["value"] == "550毫升"  # 写回正常发生


# ---------- 目录回落（需 DB，空 LLM key 确定性模板） ----------


def test_catalog_listing_answer_shape(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    outcome, _ = _run_question(client, "你们店里卖什么？")
    assert outcome.answer.kind == "answer"
    assert outcome.answer.handoff is False
    assert outcome.answer.citations == []  # 工具式模板：恒空
    assert outcome.tool is not None and outcome.tool["name"] == "catalog"
    assert outcome.gap is None  # 回答不落缺口
    assert outcome.generated is False and outcome.fallback is False
    assert "瓶装水" in outcome.answer.content
    assert "钛钢保温杯" in outcome.answer.content
    assert "3元" in outcome.answer.content  # 种子类目基准演示价实时行价
    assert "129元" in outcome.answer.content
    # UX-A2：只列已定价，不再逐条铺「价格未定」（演示库 115 件仅 3 件已定价）
    assert "已定价" in outcome.answer.content
    assert "价格未定" not in outcome.answer.content


def test_catalog_quote_answer_shape(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    outcome, _ = _run_question(client, "钛钢保温杯多少钱？")
    assert outcome.answer.kind == "answer"
    assert outcome.answer.citations == []
    assert outcome.tool is not None and outcome.tool["name"] == "catalog"
    assert outcome.tool["arg"] == "钛钢保温杯"
    assert "129元" in outcome.answer.content
    assert outcome.gap is None


def test_catalog_miss_unknown_product_refusal_and_gap(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    outcome, _ = _run_question(client, "小龙虾多少钱？")
    assert outcome.answer.kind == "refusal"
    assert outcome.answer.handoff is True
    assert outcome.tool is None
    assert outcome.gap is not None  # 去补=上新


def test_catalog_unpriced_then_price_patch_same_question_answers(api: ApiFixture) -> None:
    """去补=改价：无价拒答落缺口 → PATCH 定价 → 同问可答（回落读实时行价）。"""
    client, _ = api
    _login(client)
    created = client.post("/api/products", json={"name": "帆布包42", "category": "器皿"}).json()
    assert created["price_cents"] is None
    missed, _ = _run_question(client, "帆布包42多少钱？")
    assert missed.answer.kind == "refusal"
    assert missed.gap is not None
    assert (
        client.patch(f"/api/products/{created['id']}", json={"price_cents": 5900}).status_code
        == 200
    )
    hit, _ = _run_question(client, "帆布包42多少钱？")
    assert hit.answer.kind == "answer"
    assert "59元" in hit.answer.content
    assert hit.answer.citations == []


def test_catalog_skips_spec_question_purity_and_human(api: ApiFixture) -> None:
    """零回归闸：规格问/不纯列举/显式真人一律走既有拒答（retrieve 空时）。"""
    client, _ = api
    _login(client)
    for question in (
        "帆布包42的作者是谁",  # 规格词闸（须走既有拒答，非 catalog）
        "会员日有什么优惠？",  # 不纯列举
        "卖什么？转人工",  # 显式真人
    ):
        outcome, _ = _run_question(client, question)
        assert outcome.answer.kind == "refusal", question
        assert outcome.tool is None, question
        assert outcome.gap is not None, question


def test_catalog_listing_content_mentions_total_count(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    outcome, _ = _run_question(client, "目录")
    assert outcome.answer.kind == "answer"
    assert "共" in outcome.answer.content and "件" in outcome.answer.content


def test_seed_demo_prices_present(api: ApiFixture) -> None:
    """种子类目基准演示价回填：水 300 分/杯 12900 分，币种 CNY。"""
    client, _ = api
    _login(client)
    by_name = {p["name"]: p for p in client.get("/api/products").json()}
    assert by_name["瓶装水"]["price_cents"] == 300
    assert by_name["钛钢保温杯"]["price_cents"] == 12900
    assert by_name["瓶装水"]["currency"] == "CNY"
    # 仅 NULL 回填纪律：手改价重启不覆盖（本例直接断言 PATCH 值稳定可见）
    pid = by_name["瓶装水"]["id"]
    assert client.patch(f"/api/products/{pid}", json={"price_cents": 301}).status_code == 200
    assert client.get(f"/api/products/{pid}").json()["price_cents"] == 301
    assert client.patch(f"/api/products/{pid}", json={"price_cents": 300}).status_code == 200


class TestCatalogIntentHandoverFixes:
    """交接审查实修（第 41 刀收口）：报价政策词闸 + 列举口语形态。"""

    def test_policy_words_block_price_intent(self) -> None:
        """带政策词的报价问不得走回落（无证据库下答售价=答非所问，
        照旧拒答留缺口走治理去补）。"""
        from suite_api.services.catalog_tools import catalog_intent

        assert catalog_intent("瓶装水退货运费谁付，多少钱") is None
        assert catalog_intent("快递多少钱") is None
        assert catalog_intent("优惠价格是什么") is None
        assert catalog_intent("开发票要花多少") is None

    def test_colloquial_listing_forms(self) -> None:
        """口语形态的目录问（的吗呢啊尾巴/卖啥/有哪些）应判纯列举。"""
        from suite_api.services.catalog_tools import catalog_intent

        assert catalog_intent("卖什么的？") == "listing"
        assert catalog_intent("有目录吗") == "listing"
        assert catalog_intent("有哪些啊") == "listing"
        assert catalog_intent("你们卖啥的") == "listing"
        assert catalog_intent("店里都卖啥呢") == "listing"

    def test_pure_price_still_works(self) -> None:
        """真报价问不受政策闸影响。"""
        from suite_api.services.catalog_tools import catalog_intent

        assert catalog_intent("保温杯多少钱") == "price"
        assert catalog_intent("瓶装水价格") == "price"
