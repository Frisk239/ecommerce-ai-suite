"""第 82 刀 实体存在性闸（OOV）测试：判据 + 引擎收口 + 语料护栏。

症状（审计刀 16 C 轴）：问「雀巢咖啡的配料是什么」（库内无此商品）会引花生酱/
雪糕的配料作答——用户看到别人家商品的资料。判据见 retrieval.oov_verdict。

注：`OOV_MIN_CORPUS_ROWS` 护栏（小语料不判，见该常量注释）在测试库里恒触发，
本文件按需 monkeypatch 放开——测试种子的语料规模由测试自己声明，与生产小店的
「数据不足不启用」判据是两件事（刀 82 评审记：护栏本身另有钉子）。
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient

from suite_api.models import Asset, AssetVersion, KnowledgeGap, RetrievalChunk, ServiceSession

ApiFixture = tuple[TestClient, Any]


def _seed_doc(db: Any, title: str, chunks: list[str], source_kind: str = "upload") -> int:
    asset = Asset(kind="document", status="published", source_kind=source_kind, title=title)
    db.add(asset)
    db.flush()
    version = AssetVersion(asset_id=asset.id, version_no=1, object_key="oov-test-key")
    db.add(version)
    db.flush()
    asset.current_published_version_id = version.id
    for seq, text in enumerate(chunks, 1):
        db.add(RetrievalChunk(asset_id=asset.id, version_no=1, seq=seq, chunk=text))
    return asset.id


def _make_session(client: TestClient, token: str) -> tuple[int, ServiceSession]:
    factory = client.app.state.session_factory
    with factory() as db:
        session = ServiceSession(
            status="active",
            customer_token=token,
            customer_token_expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        db.add(session)
        db.commit()
        return session.id, session


def _open_corpus(monkeypatch: Any) -> None:
    """放开语料护栏（测试库种子小，见文件 docstring）。"""
    from suite_api.services import retrieval

    monkeypatch.setattr(retrieval, "OOV_MIN_CORPUS_ROWS", 0)


def test_oov_verdict_flags_absent_entity_only(api: ApiFixture, monkeypatch: Any) -> None:
    """判据四类真库验证：OOV 判实体串；字段问/正例/同义改写一律 None。"""
    client, _ = api
    factory = client.app.state.session_factory
    with factory() as db:
        _seed_doc(db, "保温杯 规格", ["净含量：500ml", "材质：钛钢", "保温效果出色"])
        _seed_doc(db, "退货政策说明", ["签收后7天内可申请退货"])
        db.commit()
        _open_corpus(monkeypatch)
        from suite_api.services.retrieval import oov_verdict

        # OOV：库内无此实体 -> 返回实体串
        assert oov_verdict(db, "雀巢咖啡的配料是什么") == "雀巢咖啡"
        assert oov_verdict(db, "星巴克杯子的价格是多少") == "星巴克杯子"
        # 纯字段问（无实体）-> 不判（否则字段问全被拒）
        assert oov_verdict(db, "净含量是多少") is None
        assert oov_verdict(db, "材质是什么") is None
        # 库内有该实体 -> 不判
        assert oov_verdict(db, "保温杯的净含量是多少") is None
        # 同义改写（零出现串分散、共享单字）-> 不判
        assert oov_verdict(db, "请问退换政策说明值得入手吗") is None


def test_oov_verdict_respects_corpus_guard(api: ApiFixture) -> None:
    """护栏钉子：不放开语料护栏时，小语料库一律不判（数据不足不启用）。"""
    client, _ = api
    factory = client.app.state.session_factory
    with factory() as db:
        _seed_doc(db, "保温杯 规格", ["净含量：500ml"])
        db.commit()
        from suite_api.services.retrieval import oov_verdict

        assert oov_verdict(db, "雀巢咖啡的配料是什么") is None


def test_oov_gate_refuses_instead_of_citing_other_product(
    api: ApiFixture, monkeypatch: Any
) -> None:
    """引擎收口：问库内不存在的实体 -> 拒答（不引他品证据）+ fallback_reason=oov
    + 缺口照落（真实的「知识待补」信号）。"""
    from suite_api.services.chat_engine import run_ask

    client, _ = api
    factory = client.app.state.session_factory
    with factory() as db:
        _seed_doc(
            db,
            "M&M white 规格（OFF）",
            ["配料：花生、糖", "净含量：500g"],
            source_kind="openfoodfacts",
        )
        db.commit()
        session_id, session = _make_session(client, "oov-refuse-token")
        session = db.get(ServiceSession, session_id)
        _open_corpus(monkeypatch)

        outcome = asyncio.run(run_ask(db, session, "雀巢咖啡的配料是什么"))
        # 收口为拒答：不把 M&M white 的配料当作雀巢咖啡的答案
        assert outcome.answer.kind == "refusal"
        assert outcome.answer.citations == []
        assert outcome.answer.handoff is True
        assert outcome.fallback_reason == "oov"
        # 第 86 刀：雀巢咖啡**不在商品表** -> 只建工单、不落知识缺口（缺口池语义
        # 收紧；缺口有无由 test_oov_in_catalog_goods_keeps_gap 与
        # test_oov_not_in_catalog_has_no_gap 两枚钉子覆盖）
        assert db.query(KnowledgeGap).filter(KnowledgeGap.question == "雀巢咖啡的配料是什么").count() == 0


def test_oov_gate_leaves_normal_questions_alone(api: ApiFixture, monkeypatch: Any) -> None:
    """反向钉子：库内有该实体时照常作答（OOV 闸不许碰正常问句）。"""
    from suite_api.services.chat_engine import run_ask

    client, _ = api
    factory = client.app.state.session_factory
    with factory() as db:
        _seed_doc(db, "保温杯 规格", ["净含量：500ml"])
        db.commit()
        session_id, _ = _make_session(client, "oov-normal-token")
        session = db.get(ServiceSession, session_id)
        _open_corpus(monkeypatch)

        outcome = asyncio.run(run_ask(db, session, "保温杯的净含量是多少"))
        assert outcome.answer.kind == "answer"
        assert outcome.fallback_reason is None


def test_oov_verdict_affinity_condition_is_load_bearing(api: ApiFixture, monkeypatch: Any) -> None:
    """承重钉子（评审 P1-1 E4 的反转）：**亲和条件**的判别力。

    构造（单条件回退可验证）：问句含两段实体——一段在库（「雀巢咖啡」，标题与
    chunk 都在）、一段零出现（「星巴克杯子」，连续 5 字）。零出现串条件成立，
    span 也够；**只有亲和条件**（有资产被点名 -> 不判）把它挡下。删亲和条件
    本断言必红（回退验证见 closeout）。
    注：这是判据的**边界形态**（多实体混问只答库内那个），钉的是条件存在性，
    不是「理想语义」（语义另记债）。
    """
    client, _ = api
    factory = client.app.state.session_factory
    with factory() as db:
        _seed_doc(db, "雀巢咖啡 规格", ["净含量：500ml"])
        db.commit()
        _open_corpus(monkeypatch)
        from suite_api.services.retrieval import oov_verdict

        assert oov_verdict(db, "请问雀巢咖啡和星巴克杯子的配料是什么") is None, (
            "库内商品被点名（亲和>0）-> 不判 OOV：删亲和条件本断言必红"
        )
        # 同一问句去掉库内实体后，零出现串条件独立成立 -> 判 OOV（证明上一条
        # 的「不判」确实由亲和条件承重，而非零出现串条件本身不成立）
        assert oov_verdict(db, "请问星巴克杯子的配料是什么") == "星巴克杯子"


def test_oov_verdict_span_threshold_is_load_bearing(api: ApiFixture, monkeypatch: Any) -> None:
    """承重钉子（Spec 轴 P1 反转）：**连续串长度阈值**的单条件判别力。

    构造：种子含「手机」bigram（chunk「手机支架通用配件」），使「华为手机的材质
    是什么」的零出现串只有「华为/为手」= 3 字（阈值 4 挡下）。把 OOV_MIN_SPAN
    降到 3，同一问句即判出——本测试内含**双向断言**，阈值改动必红。
    （Spec 轴指出此前的「华为手机 span=3 不判」是双条件兜底、不构成承重实证。）
    """
    client, _ = api
    factory = client.app.state.session_factory
    with factory() as db:
        _seed_doc(db, "数码周边清单", ["手机支架通用配件", "材质：钛钢"])
        db.commit()
        _open_corpus(monkeypatch)
        from suite_api.services import retrieval

        # 阈值 4（生产值）：span=3 -> 不判
        assert retrieval.oov_verdict(db, "华为手机的材质是什么") is None
        # 阈值 3：同一问句判出 -> 阈值是唯一差别（单条件承重）
        monkeypatch.setattr(retrieval, "OOV_MIN_SPAN", 3)
        assert retrieval.oov_verdict(db, "华为手机的材质是什么") is not None


def test_oov_refusal_text_names_the_missing_entity(api: ApiFixture, monkeypatch: Any) -> None:
    """第 84 刀：OOV 拒答首行**点名未收录对象**（比固定文案精确）；非 OOV 路径
    首行仍是 REFUSAL_CONTENT 常量原样（既有全等断言不破）。"""
    from suite_api.services.answer import REFUSAL_CONTENT, build_refusal_handoff_content

    # 非 OOV：首行 = 常量原样
    plain = build_refusal_handoff_content("随便问问")
    assert plain.startswith(REFUSAL_CONTENT)
    # OOV：首行点名实体
    # 商品不在库 -> 「本店暂时没有这款」
    absent = build_refusal_handoff_content("雀巢咖啡的配料是什么", missing_entity="雀巢咖啡")
    assert absent.startswith("本店暂时没有「雀巢咖啡」这款商品")
    assert REFUSAL_CONTENT not in absent.splitlines()[0]
    assert "问句摘要：雀巢咖啡的配料是什么" in absent
    # 商品在库但资料缺 -> 「资料还在补充中」（第 86 刀两类分说）
    incat = build_refusal_handoff_content(
        "乐事薯片的配料是什么", missing_entity="乐事薯片", known_product="乐事薯片"
    )
    assert incat.startswith("「乐事薯片」的商品资料还在补充中")


def test_oov_gate_message_contains_entity(api: ApiFixture, monkeypatch: Any) -> None:
    """引擎级：OOV 收口落库的 agent 消息首行点名实体。"""
    from suite_api.models import ServiceMessage
    from suite_api.services.chat_engine import run_ask

    client, _ = api
    factory = client.app.state.session_factory
    with factory() as db:
        _seed_doc(db, "M&M white 规格（OFF）", ["配料：花生、糖"], source_kind="openfoodfacts")
        db.commit()
        session_id, _ = _make_session(client, "oov-text-token")
        session = db.get(ServiceSession, session_id)
        _open_corpus(monkeypatch)

        # 用**本文件其它用例未种过的实体**（module 共享库：前测种过「雀巢咖啡」
        # 标题会让亲和 > 0 而不再判 OOV——顺序依赖踩过一次）
        asyncio.run(run_ask(db, session, "星巴克杯子的配料是什么"))
        msg = (
            db.query(ServiceMessage)
            .filter(ServiceMessage.session_id == session_id, ServiceMessage.role == "agent")
            .one()
        )
        assert msg.content.startswith("本店暂时没有「星巴克杯子」这款商品")


def test_corpus_snapshot_cache_invalidates_on_asset_change(
    api: ApiFixture, monkeypatch: Any
) -> None:
    """第 85 刀：语料快照缓存必须在资产集合/标题/废弃位变化后失效——
    否则 OOV 判定会拿旧语料（新增商品后仍拒答、废弃后仍当在库）。"""
    from suite_api.models import Asset
    from suite_api.services import retrieval

    client, _ = api
    factory = client.app.state.session_factory
    with factory() as db:
        _open_corpus(monkeypatch)
        # 自含语料（评审 P2：本钉此前依赖文件序前置用例种语料，单跑即红）
        _seed_doc(db, "无关占位资料", ["本店经营各类日用百货"])
        # 用本文件其它用例未种过的实体（module 共享库，踩过两次顺序依赖）
        Q = "戴森吸尘器的配料是什么"
        # ① 初始：库内无该实体 -> 判 OOV
        assert retrieval.oov_verdict(db, Q) == "戴森吸尘器"
        # ② 新增标题含该实体的已发布资产 -> 缓存须失效，判定翻转为不判
        _seed_doc(db, "戴森吸尘器 规格", ["净含量：500ml"])
        db.commit()
        assert retrieval.oov_verdict(db, Q) is None, (
            "新增资产后必须失效：否则下游拿旧语料继续拒答"
        )
        # ③ 废弃该资产 -> 缓存再失效，判定恢复 OOV
        asset = db.query(Asset).filter(Asset.title == "戴森吸尘器 规格").one()
        asset.discarded_at = datetime.now(UTC)
        db.commit()
        assert retrieval.oov_verdict(db, Q) == "戴森吸尘器", (
            "废弃后必须失效：否则废弃资产仍算「库内有此实体」"
        )
        # ④ 恢复在库 + 改标题 -> 摘要再变，判定恢复 OOV（若缓存不失效，
        #    旧标题「戴森吸尘器 规格」仍在语料里 -> 会判 None -> 本断言红；
        #    评审 P1 指出此前把 ④ 放在废弃态之后，与 ③ 不可区分、打不红）
        asset.discarded_at = None
        asset.title = "别的商品 规格"
        db.commit()
        assert retrieval.oov_verdict(db, Q) == "戴森吸尘器", (
            "改标题后必须失效：否则旧标题仍在语料里"
        )


def test_oov_product_match_criterion(api: ApiFixture) -> None:
    """第 86 刀判据表：商品名双向包含（方向各有限制）+ 类目名命中。

    库外实体必须为 None（否则会把「本店没有」误说成「资料待补」）。
    短泛词负例：「星巴克杯子」曾被商品名片段「杯子」误命中（方向 B 的长度限制）。
    """
    from suite_api.models import Product
    from suite_api.services.retrieval import oov_product_match

    client, _ = api
    factory = client.app.state.session_factory
    with factory() as db:
        db.add(Product(name="钛钢保温杯", category="器皿"))
        db.add(Product(name="笔记本电脑", category="笔记本电脑"))
        db.commit()
        # 方向 A：顾客用简称（实体 ⊂ 商品名）
        assert oov_product_match(db, "保温杯") == "钛钢保温杯"
        # 精确命中
        assert oov_product_match(db, "钛钢保温杯") == "钛钢保温杯"
        # 类目名命中（商品名是具体型号，类目是「笔记本电脑」）
        assert oov_product_match(db, "笔记本电脑") == "笔记本电脑"
        # 库外实体
        assert oov_product_match(db, "雀巢咖啡") is None
        assert oov_product_match(db, "戴森吸尘器") is None
        # 短泛词负例（方向 B 要求库内名 ≥3 字）
        db.add(Product(name="杯子", category="器皿"))
        db.commit()
        assert oov_product_match(db, "星巴克杯子") is None, (
            "「杯子」两字泛词不得把库外实体误判成在库"
        )


def test_oov_in_catalog_goods_keeps_gap(api: ApiFixture, monkeypatch: Any) -> None:
    """第 86 刀：商品**在库但资料没上架** -> 文案「资料还在补充中」+ **落知识缺口**
    （确实是知识待补，点「去补文档」能补出来）+ 建工单（顾客想买）。"""
    from suite_api.models import KnowledgeGap, Product, ServiceMessage
    from suite_api.services.chat_engine import run_ask

    client, _ = api
    factory = client.app.state.session_factory
    # 用「乐事薯片」（前例已实测零出现串 4 字）；「测试商品甲」的「商品」二字
    # 在语料里出现过，零出现串被截到 3 字、判不出 OOV
    q = "乐事薯片的配料是什么"
    with factory() as db:
        db.add(Product(name="乐事薯片", category="食品"))
        db.commit()
        session_id, _ = _make_session(client, "oov-incat-token")
        session = db.get(ServiceSession, session_id)
        _open_corpus(monkeypatch)

        outcome = asyncio.run(run_ask(db, session, q))
        assert outcome.answer.kind == "refusal"
        assert outcome.fallback_reason == "oov"
        msg = (
            db.query(ServiceMessage)
            .filter(ServiceMessage.session_id == session_id, ServiceMessage.role == "agent")
            .one()
        )
        assert msg.content.startswith("「乐事薯片」的商品资料还在补充中")
        assert msg.handoff is True  # 工单照建（顾客要人）
        assert (
            db.query(KnowledgeGap).filter(KnowledgeGap.question == q).count() >= 1
        ), "在库商品资料缺 = 知识待补，必须落缺口"


def test_oov_not_in_catalog_has_no_gap(api: ApiFixture, monkeypatch: Any) -> None:
    """第 86 刀：商品**不在库** -> 文案「本店暂时没有这款」+ **不落知识缺口**
    （补文档也补不出来，属经营范围问题）+ 仍建工单（需求信号）。"""
    from suite_api.models import KnowledgeGap, ServiceMessage
    from suite_api.services.chat_engine import run_ask

    client, _ = api
    factory = client.app.state.session_factory
    q = "戴森吹风机的配料是什么"
    with factory() as db:
        session_id, _ = _make_session(client, "oov-nocat-token")
        session = db.get(ServiceSession, session_id)
        _open_corpus(monkeypatch)

        outcome = asyncio.run(run_ask(db, session, q))
        assert outcome.answer.kind == "refusal"
        assert outcome.fallback_reason == "oov"
        msg = (
            db.query(ServiceMessage)
            .filter(ServiceMessage.session_id == session_id, ServiceMessage.role == "agent")
            .one()
        )
        assert msg.content.startswith("本店暂时没有「戴森吹风机」这款商品")
        assert msg.handoff is True  # 工单照建（Owner 裁决：需求信号）
        assert (
            db.query(KnowledgeGap).filter(KnowledgeGap.question == q).count() == 0
        ), "本店没有这款商品不是知识缺口（补文档补不出来），不得污染缺口池"
