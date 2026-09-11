"""引擎路径金标（第 68 刀）：目录/库存/订单/评论闸/多轮的**确定性契约**进 CI。

为什么要有这一层：检索面金标（golden_large.json + run_eval.py）直调
retrieve+compose，**不经引擎**——历次审计抓的缺陷几乎都在引擎路径上
（目录闸只装一条入口、路由词表层间缝、拼接检索挤掉本问主题、评论证据
适用域），金标全看不见，靠浏览器验收与子代理对抗才抓到。本层用
``run_ask`` 真引擎逐 case 跑，断言**确定性**产物（kind/tool/citations/
模板文案）——LLM 依赖的生成路径不进本层（无 key 也可复现，ADR 0027
空 key 回归路径的既定取舍），那面仍由检索金标 + 忠实度闸钉。

加 case 以改 ``evals/golden_engine.json`` 为主（与检索金标同约定）；
种子的形态要求写在 ``_seed`` docstring。
"""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from suite_api.models import ServiceSession
from suite_api.services.chat_engine import run_ask

GOLDEN_PATH = Path(__file__).resolve().parents[1] / "evals" / "golden_engine.json"

ApiFixture = tuple[Any, Path]


_ALLOWED_EXPECT_KEYS = {
    "kind", "handoff", "tool", "tool_arg", "cites", "cite_asset", "content_has",
}


def _load_cases() -> tuple[list[dict[str, Any]], str | None]:
    """读 golden_engine.json；返回 (cases, 错误)。坏 JSON 不炸收集，交 schema 自检报
    （与 test_eval_set 同约定——打错 expect 键是空断言绿，必须在 schema 层拒绝）。"""
    try:
        data = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [], f"golden_engine.json 读取失败: {exc}"
    if not isinstance(data, list) or not data:
        return [], "golden_engine.json 必须是非空 case 数组"
    return data, None


def _case_problems(case: Any) -> list[str]:
    problems: list[str] = []
    if not (isinstance(case, dict) and case.get("id") and case.get("surface")):
        return [f"case 缺 id/surface: {case!r}"]
    label = case["id"]
    expects = []
    if "turns" in case:
        if len(case["turns"]) < 2 or not all(isinstance(t, dict) and t.get("question") for t in case["turns"]):
            problems.append(f"{label}: turns 须 >=2 且每轮有 question")
        expects = [t["expect"] for t in case["turns"] if "expect" in t]
        if not expects:
            problems.append(f"{label}: turns 至少末轮要有 expect")
    elif case.get("question") and isinstance(case.get("expect"), dict):
        expects = [case["expect"]]
    else:
        problems.append(f"{label}: 需要 question+expect 或 turns")
    for expect in expects:
        unknown = set(expect) - _ALLOWED_EXPECT_KEYS
        if unknown:
            problems.append(f"{label}: expect 未知键 {sorted(unknown)}（会静默变空断言）")
    return problems


CASES, _LOAD_ERROR = _load_cases()


def _login(client: Any) -> None:
    assert (
        client.post("/api/auth/login", json={"username": "operator", "password": "operator123"}).status_code
        == 200
    )


def _upload_publish(client: Any, content: str, title: str) -> int:
    resp = client.post(
        "/api/assets/register",
        files={"file": ("engine-eval.txt", content.encode(), "text/plain")},
        data={"title": title},
    )
    assert resp.status_code == 201, resp.text
    asset_id = int(resp.json()["id"])
    assert client.post(f"/api/assets/{asset_id}/publish").status_code == 200
    return asset_id


def _seed_review_asset(db: Any, chunks: list[str]) -> int:
    """直接落一行已发布 review_import 资产 + 版本 + 切块（评论闸的种子）。"""
    from suite_api.models import Asset, AssetVersion, RetrievalChunk

    asset = Asset(kind="document", status="published", source_kind="review_import", title="引擎评测评论种子")
    db.add(asset)
    db.flush()
    version = AssetVersion(asset_id=asset.id, version_no=1, object_key="engine-eval-key")
    db.add(version)
    db.flush()
    asset.current_published_version_id = version.id
    for seq, text in enumerate(chunks, 1):
        db.add(RetrievalChunk(asset_id=asset.id, version_no=1, seq=seq, chunk=text))
    return asset.id


@pytest.fixture(scope="module")
def seeded(api: ApiFixture) -> dict[str, Any]:
    """引擎金标的种子（module 级一次）：商品三类带价/带库存、两份文档、一条评论。

    形态要求（加 case 前先对齐种子）：
    - 笔记本电脑 ×2（4999/3999 元、库存各 5）——类目报价与库存聚合；
    - 图书 ×1（59 元、库存 3）——别名「书」的类目聚合（65 刀卖完/还有么）；
    - 智能手机 ×1（2999 元、库存 5）——纯度负例的靶类目（手机壳/手机膜），
      没有它这些负例是同义反复（68 刀评审实测）；
    - 钛钢保温杯/瓶装水（conftest 种子自带：129 元/42 件、3 元/0 件）——单品路径；
    - 文档《保温杯规格》（净含量/材质 字段行）——RAG 直问与多轮拼接；
    - 文档《退货政策》（签收后 7 天）——RAG 政策问；
    - 评论资产（「物流很快，还没用，到货后看着很多」）——评论闸：状态问（到货了吗）滤掉、
      观点问（物流怎么样）保留（块里须真有「物流」，词法检索不是语义）。
    订单 SO-1001（已发货）由 conftest 的 seed_startup_data 自带。
    """
    client, _ = api
    _login(client)
    # 智能手机带价带库存——纯度负例的靶类目（手机壳/手机膜）：没有它这些负例
    # 是同义反复（68 刀评审实测：拆闸与否都 refusal）
    for name, category, price in (
        ("战本 14", "笔记本电脑", 499900),
        ("战本 15", "笔记本电脑", 399900),
        ("占卜书", "图书", 5900),
        ("演示手机", "智能手机", 299900),
    ):
        resp = client.post(
            "/api/products", json={"name": name, "category": category, "price_cents": price}
        )
        assert resp.status_code == 201, resp.text
    spec_asset = _upload_publish(
        client, "钛钢保温杯产品说明\n净含量：500ml\n材质：钛钢", "保温杯规格"
    )
    _upload_publish(client, "退货政策\n签收后7天内可申请退货；退货需保持吊牌完整。", "退货政策")
    # 竞争文档：一串高命中的「净含量」字段块——把「材质」块挤出拼接检索的 top-5
    # （复现审计刀 13 P0-1 的原始形态；语料太小时材质块仍在 top-5 里，merge 的
    # 去重会让本问保底变成空操作，eg-mt-001 就钉不住回归——68 刀评审实测）
    _upload_publish(
        client, "净含量对照\n净含量：480ml\n净含量：550ml\n净含量：1L", "净含量对照"
    )
    factory = client.app.state.session_factory
    with factory() as db:
        from sqlalchemy import select

        from suite_api.models import Product

        for name, stock in (("战本 14", 5), ("战本 15", 5), ("占卜书", 3), ("演示手机", 5)):
            row = db.scalar(select(Product).where(Product.name == name))
            assert row is not None, name
            row.stock = stock
        review_id = _seed_review_asset(
            db,
            [
                "物流很快，还没用，到货后看着很多，和超市买的一样",
                "客服态度很好，有问必答，售后也痛快",  # 第 69 刀：体感问的证据面
            ],
        )
        db.commit()
    return {"spec_asset": spec_asset, "review_asset": review_id}


def _resolve(value: Any, placeholders: dict[str, int]) -> Any:
    return placeholders.get(value, value) if isinstance(value, str) else value


def _assert_expect(outcome: Any, expect: dict[str, Any], placeholders: dict[str, int]) -> None:
    answer = outcome.answer
    if "kind" in expect:
        assert answer.kind == expect["kind"], (answer.kind, answer.content[:80])
    if "handoff" in expect:
        assert answer.handoff is expect["handoff"]
    if "tool" in expect:
        assert outcome.tool is not None and outcome.tool["name"] == expect["tool"], outcome.tool
    if "tool_arg" in expect:
        assert outcome.tool is not None and outcome.tool["arg"] == expect["tool_arg"], outcome.tool
    cites = answer.citations
    if expect.get("cites") == "empty":
        assert cites == [], cites
    elif expect.get("cites") == "nonempty":
        assert cites, "期望有引用"
    if "cite_asset" in expect:
        wanted = _resolve(expect["cite_asset"], placeholders)
        assert any(c["asset_id"] == wanted for c in cites), (wanted, cites)
    for piece in expect.get("content_has", []):
        assert piece in answer.content, (piece, answer.content[:120])


def _ask(factory: Any, session: ServiceSession, question: str) -> Any:
    with factory() as db:
        return asyncio.run(run_ask(db, session, question, expose_gap_id=False))


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_engine_golden_case(case: dict[str, Any], seeded: dict[str, Any], api: ApiFixture) -> None:
    client, _ = api
    factory = client.app.state.session_factory
    placeholders = {"$SPEC_ASSET": seeded["spec_asset"], "$REVIEW_ASSET": seeded["review_asset"]}
    with factory() as db:
        session = ServiceSession(status="active")
        db.add(session)
        db.commit()
    if "turns" in case:
        for turn in case["turns"]:
            outcome = _ask(factory, session, turn["question"])
            if "expect" in turn:
                _assert_expect(outcome, turn["expect"], placeholders)
    else:
        outcome = _ask(factory, session, case["question"])
        _assert_expect(outcome, case["expect"], placeholders)


def test_engine_golden_schema_selfcheck() -> None:
    """schema 自检（与检索金标同款约定）：surface 枚举有界、id 唯一、expect 键
    全在白名单（打错键=空断言绿，必须在这层拒绝）、坏 JSON 在此报而非炸收集。"""
    assert _LOAD_ERROR is None, _LOAD_ERROR
    known = {
        "catalog-price", "catalog-purity", "catalog-miss", "catalog-listing",
        "stock", "stock-purity", "order", "evidence-gate", "rag", "multi-turn",
    }
    ids = [case["id"] for case in CASES]
    assert len(ids) == len(set(ids)), "case id 重复"
    for case in CASES:
        assert case["surface"] in known, case["id"]
        for problem in _case_problems(case):
            raise AssertionError(problem)
