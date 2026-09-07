"""回答组装与拒答判定单测（纯函数，无 DB；0018 拒答是消息种类不是异常）。"""

from suite_api.services.answer import REFUSAL_CONTENT, compose_answer


def _hit(asset_id: int, version_no: int, chunk: str, score: float = 1.0) -> dict:
    return {"asset_id": asset_id, "version_no": version_no, "chunk": chunk, "score": score}


def test_no_hits_is_refusal_with_handoff() -> None:
    answer = compose_answer([], {})
    assert answer.kind == "refusal"
    assert answer.handoff is True
    assert answer.content == "抱歉，已发布资产里没有能回答这个问题的证据。"
    assert answer.citations == []


def test_document_field_chunk_template() -> None:
    hits = [_hit(3, 1, "净含量：480ml")]
    meta = {3: {"kind": "document", "title": "保温杯规格文档"}}
    answer = compose_answer(hits, meta)
    assert answer.kind == "answer"
    assert answer.handoff is False
    # 字段值句与证据句同源：字段块本身就是证据，一次陈述不复读
    assert answer.content == "根据已发布的规格文档《保温杯规格文档》，净含量为480ml。"
    assert answer.citations == [{"asset_id": 3, "version_no": 1}]


def test_document_plain_chunk_template() -> None:
    hits = [_hit(3, 1, "本品采用双层真空结构")]
    meta = {3: {"kind": "document", "title": "保温杯规格文档"}}
    answer = compose_answer(hits, meta)
    assert answer.content == "根据已发布的规格文档《保温杯规格文档》：本品采用双层真空结构。"


def test_dialogue_chunk_template() -> None:
    hits = [_hit(9, 1, "客服：根据已发布的规格文档，净含量为480ml。", score=0.8)]
    meta = {9: {"kind": "dialogue", "title": "保温杯的净含量？"}}
    answer = compose_answer(hits, meta)
    assert answer.content == "根据已发布的客服对话记录：客服：根据已发布的规格文档，净含量为480ml。"
    assert answer.citations == [{"asset_id": 9, "version_no": 1}]


def test_multi_source_takes_top_two_with_own_citations() -> None:
    hits = [
        _hit(3, 1, "净含量：480ml", score=1.4),
        _hit(9, 1, "客服：净含量为480ml。", score=1.2),
        _hit(4, 1, "低分证据不应出现", score=0.3),
    ]
    meta = {3: {"kind": "document", "title": "保温杯规格文档"}, 9: {"kind": "dialogue", "title": "对话"}, 4: {"kind": "document", "title": "别的"}}
    answer = compose_answer(hits, meta)
    assert answer.content.count("\n") == 1  # 只取分数最高两条，各占一行
    assert "规格文档《保温杯规格文档》" in answer.content
    assert "客服对话记录" in answer.content
    assert "低分证据" not in answer.content
    assert answer.citations == [{"asset_id": 3, "version_no": 1}, {"asset_id": 9, "version_no": 1}]


def test_citations_deduped_by_asset_and_version() -> None:
    hits = [_hit(3, 1, "净含量：480ml", score=1.4), _hit(3, 1, "保温效果出色", score=1.1)]
    meta = {3: {"kind": "document", "title": "规格文档"}}
    answer = compose_answer(hits, meta)
    assert answer.citations == [{"asset_id": 3, "version_no": 1}]


def test_missing_title_falls_back() -> None:
    answer = compose_answer([_hit(5, 2, "材质：钛钢")], {5: {"kind": "document", "title": None}})
    assert "《未命名资产》" in answer.content


def test_refusal_content_constant_is_stable() -> None:
    # 文案固定（任务锁定）：前端与测试都按此断言
    assert REFUSAL_CONTENT == "抱歉，已发布资产里没有能回答这个问题的证据。"
