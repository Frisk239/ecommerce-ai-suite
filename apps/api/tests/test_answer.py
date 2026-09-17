"""回答组装与拒答判定单测（纯函数，无 DB；0018 拒答是消息种类不是异常）。"""

from suite_api.services.answer import (
    _REFUSAL_HANDOFF_LINE,
    _REFUSAL_TAIL,
    REFUSAL_CONTENT,
    REFUSAL_OPENING,
    build_refusal_handoff_content,
    compose_answer,
)


def _hit(asset_id: int, version_no: int, chunk: str, score: float = 1.0) -> dict:
    return {"asset_id": asset_id, "version_no": version_no, "chunk": chunk, "score": score}


def test_no_hits_is_refusal_with_handoff() -> None:
    answer = compose_answer([], {})
    assert answer.kind == "refusal"
    assert answer.handoff is True
    assert answer.content == REFUSAL_CONTENT
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
    meta = {
        3: {"kind": "document", "title": "保温杯规格文档"},
        9: {"kind": "dialogue", "title": "对话"},
        4: {"kind": "document", "title": "别的"},
    }
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
    # 文案固定（任务锁定）：前端与测试都按此断言。第 108B 刀（W4）重写为四段式
    # ——旧文案的「已发布资产」是内部术语（顾客不可理解）且无引导。
    assert REFUSAL_CONTENT == (
        "抱歉，这个问题我暂时没有查到可靠的资料——不想随便编一个答案误导您。\n"
        "已经为您转人工处理，工作时间会在 4 小时内回复您。\n"
        "您也可以在下方留言，或者换个说法再问我一次。"
    )
    assert "已发布资产" not in REFUSAL_CONTENT  # 内部术语清零（W4 验收）


# ---------- 第 27 刀：拒答交接摘要拼装（纯函数，通道差异由 gap_id 传否决定） ----------


def test_handoff_summary_operator_with_gap_ref() -> None:
    content = build_refusal_handoff_content("登山绳可以定制长度吗", gap_id=7)
    lines = content.splitlines()
    assert lines[0] == REFUSAL_OPENING
    assert lines[1] == _REFUSAL_HANDOFF_LINE.format(no="")
    assert lines[2] == _REFUSAL_TAIL
    assert lines[3:] == ["问句摘要：登山绳可以定制长度吗", "缺口：G-0007"]
    assert content.startswith(REFUSAL_CONTENT)  # 四段式模板原样，摘要段追加


def test_handoff_summary_without_gap_id_has_no_gap_line() -> None:
    # 顾客通道（run_ask expose_gap_id=False → 传 None）：带问句摘要、不带缺口段
    content = build_refusal_handoff_content("会员生日礼怎么领？")
    assert content == f"{REFUSAL_CONTENT}\n问句摘要：会员生日礼怎么领？"


def test_handoff_summary_carries_ticket_no() -> None:
    """第 108B 刀（W4）：新话术的转人工回执行带真实工单号（H 号是顾客回执，
    不走 gap_id 白名单）；无工单上下文的纯函数直调省略号码，其余逐字一致。"""
    numbered = build_refusal_handoff_content("会员生日礼怎么领？", ticket_no="H-0007")
    assert "已经为您转人工处理（工单 H-0007），工作时间会在 4 小时内回复您。" in numbered
    assert numbered.splitlines()[0] == REFUSAL_OPENING
    assert numbered.splitlines()[2] == _REFUSAL_TAIL
    # 无号形态 = 常量逐字（号码是唯一差异）
    plain = build_refusal_handoff_content("会员生日礼怎么领？")
    assert plain.splitlines()[1] == _REFUSAL_HANDOFF_LINE.format(no="")


def test_handoff_summary_truncates_and_masks() -> None:
    # 先掩后截（0038 出口掩口径，同 _first_question）：60 字 + 「…」
    question = "退款请打到 13812345678" + "啊" * 80
    masked_summary = ("退款请打到 1********78" + "啊" * 80)[:60]
    content = build_refusal_handoff_content(question)
    assert "13812345678" not in content
    assert content.endswith(f"问句摘要：{masked_summary}…")
