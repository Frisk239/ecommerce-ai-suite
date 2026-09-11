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


def test_catalog_skips_spec_question_and_impure_listing(api: ApiFixture) -> None:
    """零回归闸：规格问/不纯列举一律走既有拒答（retrieve 空时，非 catalog）。"""
    client, _ = api
    _login(client)
    for question in (
        "帆布包42的作者是谁",  # 规格词闸（须走既有拒答，非 catalog）
        "会员日有什么优惠？",  # 不纯列举
    ):
        outcome, _ = _run_question(client, question)
        assert outcome.answer.kind == "refusal", question
        assert outcome.tool is None, question
        assert outcome.gap is not None, question


def test_explicit_human_request_becomes_handoff_not_gap(api: ApiFixture) -> None:
    """第 42 刀（ADR 0046）：显式「转人工」是转人工、不是知识题。

    第 41 刀零回归闸里「卖什么？转人工」期望 refusal+gap；本刀后词表快路径
    在目录回落之前接住——kind=handoff、回执带工单号 H-xxxx、**不产生缺口**。
    """
    client, _ = api
    _login(client)
    outcome, _ = _run_question(client, "卖什么？转人工")
    assert outcome.answer.kind == "handoff"
    assert outcome.answer.handoff is True
    assert outcome.tool is not None and outcome.tool["name"] == "handoff"
    assert outcome.gap is None
    assert outcome.ticket is not None
    assert f"H-{outcome.ticket.id:04d}" in outcome.answer.content


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


# ---------- 第 50 刀：报价不看检索命中（ADR 0045 修订） ----------


def _fake_db_with(products: list[Any]) -> Any:
    from unittest.mock import MagicMock

    db = MagicMock()
    db.scalars.return_value = products
    return db


def test_try_price_answer_fires_with_hits_for_named_product() -> None:
    """报价意图 + 命中商品名 + 有价 -> 直接答行价（不看检索命中）。

    这条是第 50 刀修订 ADR 0045 的落点：老口径要求「有命中永不回落」，而演示库
    里带商品名的问句几乎都有命中，于是补齐的 115 行价永远问不出来。
    """
    from suite_api.services.catalog_tools import try_price_answer

    product = _mem_product(7, "钛钢保温杯", 12900)
    answer = try_price_answer(_fake_db_with([product]), "钛钢保温杯多少钱？")
    assert answer is not None
    assert "129" in answer.content
    assert answer.tool["arg"] == "钛钢保温杯"
    assert answer.tool["name"] == "catalog"


def test_try_price_answer_requires_price_intent() -> None:
    """只有报价意图才吃这条路：列举/规格问/政策问都不许被它抢走。"""
    from suite_api.services.catalog_tools import try_price_answer

    db = _fake_db_with([_mem_product(7, "钛钢保温杯", 12900)])
    assert try_price_answer(db, "你们卖什么？") is None  # 列举：仍走空命中闸
    assert try_price_answer(db, "保温杯的净含量是多少？") is None  # 规格问
    assert try_price_answer(db, "退货运费多少钱？") is None  # 政策问（政策词闸）


@pytest.mark.parametrize(
    "question",
    [
        "保温杯刻字怎么收费",  # 服务问：含「收费」但主体是刻字
        "钛钢保温杯怎么保养，收费吗",
        "保温杯刻字限多少字",
    ],
)
def test_try_price_answer_does_not_steal_service_questions(question: str) -> None:
    """评审 P1：含价格词 + 命中商品名，但问的是服务/规格 -> 交回 RAG 答文档。

    （演示库里有「定制刻字服务怎么收费」这类文档；报价模板抢答会答成售价。）
    """
    from suite_api.services.catalog_tools import try_price_answer

    db = _fake_db_with([_mem_product(2, "钛钢保温杯", 12900)])
    assert try_price_answer(db, question) is None


def test_price_residual_is_pure_function() -> None:
    """扣掉商品名与问价虚词后剩什么：空=纯问价（可回落），非空=实质问句。"""
    from suite_api.services.catalog_tools import price_residual

    assert price_residual("钛钢保温杯多少钱？", "钛钢保温杯") == ""
    assert price_residual("请问钛钢保温杯的售价是多少？", "钛钢保温杯") == ""
    assert price_residual("保温杯刻字怎么收费", "钛钢保温杯") != ""  # 剩下「刻字」这类实质词


def test_try_price_answer_misses_on_unknown_or_unpriced_product() -> None:
    from suite_api.services.catalog_tools import try_price_answer

    assert try_price_answer(_fake_db_with([_mem_product(7, "帆布包", None)]), "帆布包多少钱？") is None
    assert try_price_answer(_fake_db_with([]), "小龙虾多少钱？") is None


# 端到端用例用的正文（模块级常量：避免把多行中文塞进 files= 的元组里）
_CUP_NOTICE = "钛钢保温杯调价通知" + chr(10) + "钛钢保温杯将于下月调价，具体以门店为准。"


def test_price_question_uses_row_price_even_when_retrieval_hits(api: ApiFixture) -> None:
    """端到端（真引擎）：库里已有会命中的资产时，问价仍答**行价**。

    修订前这条问句走 RAG（证据里没有价）→ 答「证据未覆盖价格」，把补齐的价白
    放了。现在：报价意图 + 命中商品名 → 工具式行价（citations 恒空、kind=answer）。
    """
    client, _ = api
    _login(client)
    # **先造出检索命中**（评审 P1-2）：发布一份正文含「钛钢保温杯」的资产，
    # 并确认 retrieve 真的会命中——否则这条用例走的是老的「空命中回落」，
    # 删掉引擎里 try_price_answer 那两行它照样绿。
    registered = client.post(
        "/api/assets/register",
        files={
            "file": (
                "cup-notice.txt",
                _CUP_NOTICE.encode(),
                "text/plain",
            )
        },
        data={"title": "钛钢保温杯调价通知"},
    )
    assert registered.status_code == 201
    asset_id = registered.json()["id"]
    assert client.post(f"/api/assets/{asset_id}/publish").status_code == 200

    with client.app.state.session_factory() as db:
        from suite_api.services.retrieval import retrieve

        assert retrieve(db, "钛钢保温杯多少钱"), "本用例要求检索有命中（否则钉不到新路径）"

    outcome, _ = _run_question(client, "钛钢保温杯多少钱？")
    assert outcome.tool is not None and outcome.tool["name"] == "catalog"
    assert outcome.answer.citations == []  # 工具数据源不是引用（0007）
    assert outcome.answer.kind == "answer"
    assert "129" in outcome.answer.content
    assert outcome.fallback is False  # 模板即正式产出，不是降级


def test_empty_hit_quote_path_also_respects_purity_gate() -> None:
    """审计刀 10 P0：纯度闸必须装在**共用的**报价路径上。

    此前闸只在 `try_price_answer`（有命中那条），空命中那条旧路径照旧抢答——
    实测「家具送货安装怎么收费？」（无命中）被答成「售价 899元」。这条钉死
    两条路径共用同一套判定。
    """
    from suite_api.services.catalog_tools import try_catalog_answer

    products = [_mem_product(3, "WANDS 家具（演示）", 89900)]
    db = _fake_db_with(products)
    # 空命中 + 报价意图 + 命中商品名 + 服务问句 -> 不回落（交回 RAG）
    assert try_catalog_answer(db, "家具送货安装怎么收费？", []) is None
    # 对照：纯问价照旧回落
    answer = try_catalog_answer(db, "WANDS 家具（演示）多少钱？", [])
    assert answer is not None and "899" in answer.content


def test_price_residual_handles_partial_product_names() -> None:
    """审计刀 10 P1：部分名问价（「保温杯多少钱」对商品「钛钢保温杯」）要能报价。

    `match_product` 按 LCS≥2 命中，纯度闸就得用同一口径剔除命中片段；要求「全名
    子串」会把口语问价静默挡回 RAG（同一句问话答不答取决于检索有没有命中）。
    """
    from suite_api.services.catalog_tools import price_residual, try_price_answer

    assert price_residual("保温杯多少钱？", "钛钢保温杯") == ""
    assert price_residual("保温杯怎么刻字？", "钛钢保温杯") != ""

    db = _fake_db_with([_mem_product(2, "钛钢保温杯", 12900)])
    answer = try_price_answer(db, "保温杯多少钱？")
    assert answer is not None and "129" in answer.content


# ---------- 第 52 刀：按类目问价 ----------


def _catalog_db(products: list[Any]) -> Any:
    from unittest.mock import MagicMock

    db = MagicMock()
    db.scalars.return_value = products
    return db


def test_category_price_quote_reports_range_and_count() -> None:
    """列举刚说「均已定价」，按类目问价却拒答 = 自相矛盾（审计刀 10 P1）。

    类目价是聚合事实：件数 + 已定价数 + 区间（都同价说「均为」）。
    """
    from suite_api.services.catalog_tools import try_price_answer

    products = [
        _mem_product(1, "笔记本 A", 499900),
        _mem_product(2, "笔记本 B", 399900),
        _mem_product(3, "笔记本 C", None),  # 未定价：计入总数不计入区间
    ]
    for product in products:
        product.category = "笔记本电脑"
    answer = try_price_answer(_catalog_db(products), "笔记本电脑多少钱？")
    assert answer is not None
    assert "共 3 件" in answer.content
    assert "已定价 2 件" in answer.content
    assert "3999" in answer.content and "4999" in answer.content
    assert "另有 1 件未定价" in answer.content
    assert answer.tool["arg"] == "笔记本电脑"


def test_category_price_quote_single_price_says_uniform() -> None:
    from suite_api.services.catalog_tools import try_price_answer

    product = _mem_product(1, "瓶装水", 300)
    product.category = "食品"
    answer = try_price_answer(_catalog_db([product]), "食品多少钱？")
    assert answer is not None
    assert "均为 3元" in answer.content


def test_category_price_quote_respects_purity_gate() -> None:
    """类目问价同样要过纯度闸：服务/规格问不许被答成类目价。"""
    from suite_api.services.catalog_tools import try_price_answer

    product = _mem_product(1, "笔记本 A", 499900)
    product.category = "笔记本电脑"
    db = _catalog_db([product])
    assert try_price_answer(db, "笔记本电脑刻字怎么收费？") is None
    assert try_price_answer(db, "笔记本电脑怎么保养？") is None


def test_category_price_quote_misses_when_category_unpriced_or_unknown() -> None:
    from suite_api.services.catalog_tools import try_price_answer

    product = _mem_product(1, "笔记本 A", None)
    product.category = "笔记本电脑"
    assert try_price_answer(_catalog_db([product]), "笔记本电脑多少钱？") is None
    assert try_price_answer(_catalog_db([product]), "无人机多少钱？") is None


def test_category_question_uses_category_quote_end_to_end(api: ApiFixture) -> None:
    """端到端（真引擎）：种子里有「食品」类目（瓶装水 3 元）-> 按类目问价答类目价。"""
    client, _ = api
    _login(client)
    outcome, _ = _run_question(client, "食品多少钱？")
    assert outcome.tool is not None and outcome.tool["name"] == "catalog"
    assert outcome.answer.citations == []
    assert "食品" in outcome.answer.content and "3元" in outcome.answer.content


def test_category_quote_wins_when_product_name_contains_category() -> None:
    """审计刀 11 P1：商品名里含完整类目名时，类目问价不能被单品劫持。

    反例形态：商品「WANDS 家具（演示）」含「家具」——按单品走会答成这一个商品的价，
    类目聚合（件数/区间/未定价）被静默吞掉；再加一件不同价的商品就答错。
    """
    from suite_api.services.catalog_tools import try_price_answer

    products = [
        _mem_product(1, "WANDS 家具（演示）", 89900),
        _mem_product(2, "实木餐桌", 159900),
    ]
    for product in products:
        product.category = "家具"
    answer = try_price_answer(_catalog_db(products), "家具多少钱？")
    assert answer is not None
    assert answer.tool["arg"] == "家具"  # 类目聚合，不是单品
    assert "共 2 件" in answer.content
    assert "899" in answer.content and "1599" in answer.content  # 区间

    # 具体型号问价仍走单品
    single = try_price_answer(_catalog_db(products), "实木餐桌多少钱？")
    assert single is not None and single.tool["arg"] == "实木餐桌"


def test_category_quote_refuses_mixed_currency() -> None:
    """混币种不做类目聚合（跨币种比大小无意义）——宁可拒答也不给假区间。"""
    from suite_api.services.catalog_tools import try_price_answer

    first = _mem_product(1, "甲", 300)
    first.category = "食品"
    second = _mem_product(2, "乙", 500)
    second.category = "食品"
    second.currency = "USD"
    assert try_price_answer(_catalog_db([first, second]), "食品多少钱？") is None


def test_catalog_answer_closes_matching_open_gap(api: ApiFixture) -> None:
    """审计刀 11 P0-1：目录能答 = 这条不是「知识待补」——同问的 open 缺口一并收掉。

    （否则治理台「待补」写着客服当下已经能答的问题，点「去补文档」还把人引去补
    一份不需要的文档。）
    """
    client, _ = api
    _login(client)
    factory = client.app.state.session_factory
    with factory() as db:
        from suite_api.models import KnowledgeGap
        from suite_api.services.knowledge_gaps import normalize_question

        gap = KnowledgeGap(
            question="食品多少钱？", normalized_question=normalize_question("食品多少钱？")
        )
        db.add(gap)
        db.commit()
        gap_id = gap.id

    outcome, _ = _run_question(client, "食品多少钱？")
    assert outcome.tool is not None and outcome.tool["name"] == "catalog"

    with factory() as db:
        from suite_api.models import KnowledgeGap

        row = db.get(KnowledgeGap, gap_id)
        assert row.status == "resolved"
        assert row.resolved_at is not None
        assert row.resolved_by_asset_id is None  # 不是靠文档补上的，如实留白


# ---------- 第 56 刀：类目别名与口语问价 ----------


def test_category_alias_matches_library_category() -> None:
    """「笔记本多少钱？」这类口语短称要认到库里的类目（审计刀 11 C-P1-3）。

    纯度闸用**别名**剔除（顾客没说「笔记本电脑」四个字），聚合仍按目标类目算。
    """
    from suite_api.services.catalog_tools import try_price_answer

    products = [_mem_product(i, f"笔记本{i}", 499900) for i in range(1, 4)]
    for product in products:
        product.category = "笔记本电脑"
    db = _catalog_db(products)

    answer = try_price_answer(db, "笔记本多少钱？")
    assert answer is not None
    assert answer.tool["arg"] == "笔记本电脑"
    assert "共 3 件" in answer.content

    # 别名 + 服务问仍被挡（纯度闸）
    assert try_price_answer(db, "笔记本怎么刻字？") is None


def test_price_intent_covers_colloquial_phrasings() -> None:
    """口语问价词（怎么卖 / 什么价 / 多钱）都算报价意图；规格/服务问照旧不算。"""
    from suite_api.services.catalog_tools import catalog_intent, try_price_answer

    assert catalog_intent("笔记本电脑怎么卖？") == "price"
    assert catalog_intent("笔记本电脑什么价？") == "price"
    assert catalog_intent("笔记本电脑多钱？") == "price"

    product = _mem_product(1, "笔记本 A", 499900)
    product.category = "笔记本电脑"
    db = _catalog_db([product])
    assert try_price_answer(db, "笔记本电脑怎么卖？") is not None
    # 规格问不被吸进来
    assert try_price_answer(db, "笔记本电脑的净含量是多少？") is None


def test_service_question_is_not_taken_as_price() -> None:
    """服务/规格问仍交回 RAG（虚词表补了「怎么/如何」后仍要挡得住刻字这类实质词）。"""
    from suite_api.services.catalog_tools import price_residual

    assert price_residual("笔记本电脑怎么卖？", "笔记本电脑") == ""
    assert price_residual("保温杯刻字怎么收费", "钛钢保温杯") != ""
    assert price_residual("笔记本电脑怎么保养？", "笔记本电脑") != ""


# ---------- 第 62 刀：别名扩表到 9/9 类目 + 类目问句纯度（共享候选表） ----------

# 演示库 9 个类目的「顾客会怎么说」——一句话即一条覆盖契约（第 62 刀）。
_DEMO_CATEGORY_PHRASES: list[tuple[str, str]] = [
    ("笔记本电脑", "笔记本多少钱？"),
    ("笔记本电脑", "电脑多少钱？"),
    ("笔记本电脑", "本本多少钱？"),
    ("智能手机", "手机多少钱？"),
    ("智能手机", "智能机多少钱？"),
    ("平板电脑", "平板多少钱？"),
    ("电视机", "电视多少钱？"),
    ("电视机", "彩电多少钱？"),
    ("食品", "零食多少钱？"),
    ("食品", "吃的多少钱？"),
    ("食品", "食物多少钱？"),
    ("图书", "书多少钱？"),
    ("图书", "书籍多少钱？"),
    ("图书", "书本多少钱？"),
    ("洗衣机", "洗衣机多少钱？"),
    ("器皿", "杯子多少钱？"),
    ("器皿", "水杯多少钱？"),
    ("家具", "家具多少钱？"),
]


def _catalog_of(categories: list[str]) -> Any:
    """每个类目种一件同价商品的内存目录（覆盖契约用，不碰 DB）。"""
    products = [_mem_product(i, f"商品{i}", 10000) for i in range(1, len(categories) + 1)]
    for product, category in zip(products, categories, strict=True):
        product.category = category
    return _catalog_db(products)


def test_category_alias_covers_every_demo_category() -> None:
    """第 62 刀：9 个类目都要有顾客会说的短称（不再只覆盖 4 个）。

    审计刀 12 C-P2 实测「电脑/书 多少钱？」拒答——别名表只覆盖笔记本/手机/平板/
    电视。第 56 刀不敢收「书」（撞「说明书」），第 62 刀把歧义面交给纯度闸：
    `category_question_residual` 剔完命中词后必须为空，所以「说明书多少钱」照旧拒答。
    """
    from suite_api.services.catalog_tools import try_price_answer

    db = _catalog_of([category for category, _ in _DEMO_CATEGORY_PHRASES])
    covered = {category for category, _ in _DEMO_CATEGORY_PHRASES}
    assert covered == {"笔记本电脑", "智能手机", "平板电脑", "电视机", "食品", "图书", "洗衣机", "器皿", "家具"}
    for category, phrase in _DEMO_CATEGORY_PHRASES:
        answer = try_price_answer(db, phrase)
        assert answer is not None, f"{phrase} 应命中类目价"
        assert answer.tool["arg"] == category, f"{phrase} 应归到 {category}"


@pytest.mark.parametrize(
    "question",
    [
        "说明书多少钱？",  # 「书」当后缀
        "书桌多少钱？",  # 「书」当定语，其实是家具
        "书签多少钱？",
        "证书多少钱？",
        "电脑包多少钱？",  # 「电脑」当定语
        "手机壳多少钱？",
        "电视柜多少钱？",
        "平板支撑多少钱？",
        "食物保鲜盒多少钱？",
    ],
)
def test_category_alias_near_misses_stay_refused(question: str) -> None:
    """别名词当**修饰语**（配件/家具/箱包/证书）时不许按类目报价——纯度闸的负例钉子。

    这批用例在「只按 token 命中」的实现下会误答（命中词就在问句里，闸一撤就答），
    所以它们钉的是**闸**。注意它们抓的是「扩了表却没装闸」这种半回退：若整刀回退到
    第 62 刀前，「书/电脑」等还不是别名，这批会因「token 不命中」而**照样通过**——
    整刀回退由 `test_category_alias_covers_every_demo_category` 的正向覆盖抓。
    """
    from suite_api.services.catalog_tools import try_price_answer

    db = _catalog_of([category for category, _ in _DEMO_CATEGORY_PHRASES])
    assert try_price_answer(db, question) is None


@pytest.mark.parametrize(
    "question, expect",
    [
        ("请问一下手机多少钱？", "智能手机"),  # 「问一下」收了复合词，「一下」没收到第 62 刀评审
        ("你好，请问笔记本电脑多少钱", "笔记本电脑"),
        ("想问下手机多少钱", "智能手机"),  # 裸「下」：想问下/请问下 都要它
        ("麻烦问下彩电多少钱", "电视机"),
        ("笔记本电脑多少钱一台", "笔记本电脑"),  # 量词形态
        ("书一本多少钱", "图书"),
        ("一本书多少钱", "图书"),
        ("杯子多少钱一个", "器皿"),
        ("彩电什么价", "电视机"),  # 意图词表有「什么价」，纯度层原剔不掉裸「价」——死意图路径
        ("手机啥价", "智能手机"),
        ("电脑价格怎么样啊", "笔记本电脑"),  # 评价问
        ("笔记本电脑多少钱啦", "笔记本电脑"),  # 语气「啦」原不在字类
    ],
)
def test_price_filler_covers_polite_measure_and_bare_jia(question: str, expect: str) -> None:
    """第 62 刀评审（Spec 轴）P2×2：这些问法此前**意图层认、纯度层剔不掉**。

    「意图==price 却在纯度闸前止步」比「意图就不认」更隐蔽——顾客用了自然问法，
    落到的却是拒答+缺口。修在 `_PRICE_RE`（问价虚词表，`price_residual` 与
    `category_question_residual` 共用），不在意图层。
    """
    from suite_api.services.catalog_tools import try_price_answer

    db = _catalog_of([category for category, _ in _DEMO_CATEGORY_PHRASES])
    answer = try_price_answer(db, question)
    assert answer is not None, question
    assert answer.tool["arg"] == expect, question


@pytest.mark.parametrize(
    "question, expect",
    [
        ("手机咋卖", "智能手机"),  # 「怎么卖」认了「咋卖」没认（第 65 刀）
        ("彩电咋买", "电视机"),
        ("手机贵吗", "智能手机"),  # 意图表只有「贵不贵」；裸「贵」排在长词后（先匹配贵不贵）
        ("笔记本电脑贵吗", "笔记本电脑"),
        ("保温杯贵吗", "钛钢保温杯"),  # 单品路径（price_residual 共用 _PRICE_RE）
    ],
)
def test_price_colloquial_za_and_gui_forms(question: str, expect: str) -> None:
    """第 65 刀：咋卖/咋买（怎么卖的北方式说法）与「贵吗」是自然问价形态。

    此前是**死意图路径**的两种新面：咋卖压根不进意图（只认「怎么」）；贵吗
    进不了意图（只有「贵不贵」）。意图层与纯度层（_PRICE_RE 补裸「贵」，排在
    「贵不贵」后面）成对补齐。
    """
    from suite_api.services.catalog_tools import try_price_answer

    products = [_mem_product(1, "钛钢保温杯", 12900)]
    products[0].category = "器皿"
    db = _catalog_of([c for c, _ in _DEMO_CATEGORY_PHRASES])
    if expect == "钛钢保温杯":
        db = _catalog_db(products)
    answer = try_price_answer(db, question)
    assert answer is not None, question
    assert answer.tool["arg"] == expect, question


@pytest.mark.parametrize("question", ["电脑桌贵吗", "二手手机贵吗", "手机膜咋卖", "电视机柜咋卖"])
def test_za_and_gui_forms_do_not_blank_modifiers(question: str) -> None:
    """裸「贵」进词表的代价面：别名词当修饰语（桌/二手/膜/柜）必须靠残字挡住；
    政策问（退货运费）照旧被政策闸挡在意图层之前。"""
    from suite_api.services.catalog_tools import try_price_answer

    db = _catalog_of([c for c, _ in _DEMO_CATEGORY_PHRASES])
    assert try_price_answer(db, question) is None
    assert try_price_answer(db, "退货运费贵吗") is None


@pytest.mark.parametrize(
    "question, expect",
    [
        ("保温杯花多少钱", "钛钢保温杯"),  # 审计刀 13 B 轴 P1：re 择先按最左起始位，「花多少」先吃剩「钱」
        ("笔记本电脑花多少钱", "笔记本电脑"),
        ("我想买保温杯多少钱", "钛钢保温杯"),  # 「我想」不在虚词表
    ],
)
def test_audit13_price_filler_leftmost_trap_and_woxiang(question: str, expect: str) -> None:
    """审计刀 13 B 轴 P1：「花多少钱」是**死意图路径**——意图层认（花多少），
    纯度层剔不净（最左匹配把「花多少」吃在「多少钱」之前，剩孤字「钱」）。
    「长词写在短词前面」管不住前缀起始更早的情形，修法是补裸「钱」兜底。"""
    from suite_api.services.catalog_tools import try_price_answer

    if expect == "钛钢保温杯":  # 单品用例要有真名商品（目录夹具是 商品1..9）
        cup = _mem_product(1, "钛钢保温杯", 12900)
        cup.category = "器皿"
        db = _catalog_db([cup])
    else:
        db = _catalog_of([category for category, _ in _DEMO_CATEGORY_PHRASES])
    answer = try_price_answer(db, question)
    assert answer is not None, question
    assert answer.tool["arg"] == expect, question


def test_audit13_price_negative_deposit_still_refuses() -> None:
    """裸「钱」的代价面：「押金多少钱」的「押」/「金」仍是残字，照旧拒答。"""
    from suite_api.services.catalog_tools import try_price_answer

    db = _catalog_of([category for category, _ in _DEMO_CATEGORY_PHRASES])
    assert try_price_answer(db, "保温杯押金多少钱") is None


@pytest.mark.parametrize(
    "question, expect",
    [
        ("彩电咋样卖", "电视机"),  # 第 67 刀：咋样卖/咋样买（咋样单独不是价格词）
        ("手机咋样买", "智能手机"),
        ("手机咋价", "智能手机"),  # 与啥价对称
        ("这手机啥价", "智能手机"),  # 裸「这」此前是残字
        ("那个手机咋卖", "智能手机"),  # 裸「个」同上
        ("这台笔记本电脑多少钱", "笔记本电脑"),
        ("帮我查下手机多少钱", "智能手机"),  # 第 70 刀评审：礼貌动词 帮/我/查 进虚词表
    ],
)
def test_word_seam_za_forms_and_demonstratives(question: str, expect: str) -> None:
    """第 67 刀（56 刀词面邻缝收口）：咋样卖/咋价进意图、这/那/个/台进纯度虚词。

    负例（这个咋样=评价问、电脑包咋样卖=修饰语）钉在下一测。
    """
    from suite_api.services.catalog_tools import catalog_intent, try_price_answer

    assert catalog_intent(question) == "price", question
    db = _catalog_of([category for category, _ in _DEMO_CATEGORY_PHRASES])
    answer = try_price_answer(db, question)
    assert answer is not None, question
    assert answer.tool["arg"] == expect, question


@pytest.mark.parametrize("question", ["这个咋样", "电脑包咋样卖", "手机膜多少钱"])
def test_word_seam_negatives_still_refuse(question: str) -> None:
    """咋样**单独**不是价格词（评价问不进意图）；修饰语靠残字挡。"""
    from suite_api.services.catalog_tools import catalog_intent, try_price_answer

    db = _catalog_of([category for category, _ in _DEMO_CATEGORY_PHRASES])
    assert try_price_answer(db, question) is None, question
    if question == "电脑包咋样卖":
        assert catalog_intent(question) == "price"  # 进意图但被纯度闸挡（钉的是闸不是意图）


@pytest.mark.parametrize("question", ["手机下单多少钱", "线下体验多少钱", "电脑下架了吗多少钱"])
def test_bare_xia_does_not_blank_real_modifiers(question: str) -> None:
    """裸「下」进虚词表的代价面：真修饰语（下单/线下/下架）必须靠**残字**挡住。"""
    from suite_api.services.catalog_tools import try_price_answer

    db = _catalog_of([category for category, _ in _DEMO_CATEGORY_PHRASES])
    assert try_price_answer(db, question) is None


def test_category_targets_is_single_source_with_aliases_first() -> None:
    """共享候选表（第 62 刀）：别名在前、库内类目名在后（长名优先）。

    报价与库存两条路径都从 `category_targets` 取候选——第 56 刀两边各排一遍时
    库存侧漏装了纯度闸（第 62 刀实测 4 例误答）。这里钉住表序，免得改一处漏一处。
    """
    from suite_api.services.catalog_tools import category_targets

    targets = category_targets({"笔记本电脑", "食品"})
    assert ("笔记本", "笔记本电脑") in targets
    assert ("食物", "食品") in targets
    # 别名段结束后才是类目名段；类目名段按长度降序（「笔记本电脑」在「食品」前）
    alias_part = [pair for pair in targets if pair[0] != pair[1]]
    name_part = [pair for pair in targets if pair[0] == pair[1]]
    assert targets[: len(alias_part)] == alias_part
    assert name_part == [("笔记本电脑", "笔记本电脑"), ("食品", "食品")]
    # 库里没有的类目不给候选（别名指向的类目不在库里时整条跳过）
    assert category_targets({"食品"}) == [("零食", "食品"), ("吃的", "食品"), ("食物", "食品"), ("食品", "食品")]


def test_single_char_alias_is_stripped_literally() -> None:
    """单字别名（「书」）必须能剔干净：LCS 剔除有最小长度 2，单字永远剔不掉。

    第 62 刀实测：「书多少钱」走 `price_residual` 时 fragment 长 1 < 最小长度 2，
    整张单字别名全体失效（`书/书籍/书本` 里只有后两个能用）。
    """
    from suite_api.services.catalog_tools import category_question_residual

    assert category_question_residual("书多少钱？", "书") == ""
    assert category_question_residual("书籍多少钱？", "书籍") == ""
    assert category_question_residual("红书多少钱？", "书") == "红"
    assert category_question_residual("书桌多少钱？", "书") == "桌"


def test_stock_question_by_category_answers_aggregate(api: ApiFixture) -> None:
    """「你们有笔记本吗？」问的是一类有没有货——第 56 刀起给类目聚合。

    逐件匹配商品名必然落空（类目名不是商品名），此前落到「未找到商品」+转人工。
    """
    client, _ = api
    _login(client)
    outcome, _ = _run_question(client, "你们有笔记本吗？")
    # 种子里没有笔记本电脑类商品时不该编造：走到别处也算（断言只钉「有该类目时答聚合」）
    factory = client.app.state.session_factory
    with factory() as db:
        from sqlalchemy import select

        from suite_api.models import Product
        from suite_api.services.stock_tools import query_stock

        products = list(db.scalars(select(Product).order_by(Product.id)))
        result = query_stock(db, "你们有笔记本吗？")
        categories = {p.category for p in products}
        if "笔记本电脑" in categories:
            assert result["found"] is True
            assert result["category"] == "笔记本电脑"
            assert result["total"] >= 1
        else:
            assert result == {"found": False}
    assert outcome is not None
