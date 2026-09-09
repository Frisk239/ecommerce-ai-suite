"""检索切块与词法打分单测（纯函数，无 DB）。

覆盖：文档/对话切块（字段行、句级、长句逗号断、上限）、查询词法单元
（停用词过滤、单字退化 unigram）、打分（命中/不命中/长度归一）。
"""

from datetime import UTC, datetime, timedelta

import pytest

from suite_api.services.retrieval import (
    MAX_CHUNKS,
    STALE_DAYS_DEFAULT,
    STALE_MULTIPLIER,
    chunk_dialogue,
    chunk_document,
    chunk_text,
    index_chunks_for_version,
    is_stale,
    query_terms,
    retrieve,
    score_chunk,
    stale_days,
)

# ---------- 切块：文档 ----------


def test_document_field_lines_are_whole_chunks() -> None:
    text = "【产品规格】\n净含量：550毫升\n保质期：12个月\n储存条件：常温避光"
    chunks = chunk_document(text)
    # 「字段：值」行整行成块：字段名与值是一个证据单元，问「净含量」要命中值
    assert "净含量：550毫升" in chunks
    assert "保质期：12个月" in chunks
    assert "储存条件：常温避光" in chunks
    # 标题行按句切（无句号 -> 整行一句），不是字段行
    assert chunks[0] == "【产品规格】"


def test_document_sentence_splitting() -> None:
    text = "本品采用双层真空结构。保温效果出色；杯身轻便。"
    assert chunk_document(text) == [
        "本品采用双层真空结构",
        "保温效果出色",
        "杯身轻便",
    ]


def test_long_sentence_split_by_comma() -> None:
    # 超过 40 字的长句按逗号/顿号断
    text = "这款保温杯采用食品级钛钢内胆，双层真空隔热工艺，杯身轻便耐用，适合通勤与户外露营使用。"
    chunks = chunk_document(text)
    assert len(chunks) == 4
    assert chunks[0] == "这款保温杯采用食品级钛钢内胆"


def test_document_drops_short_fragments_and_blank_lines() -> None:
    text = "\n\n好。。\n  \nx"
    # 「好」句号切后是单字（<2 字）弃之；「x」单字弃之；空行丢弃
    assert chunk_document(text) == []


def test_document_chunk_cap() -> None:
    text = "\n".join(f"第{i}句内容各不相同" for i in range(MAX_CHUNKS + 50))
    chunks = chunk_document(text)
    assert len(chunks) == MAX_CHUNKS
    assert chunks[0] == "第0句内容各不相同"  # 截断保头部（顺序证据）


def test_document_mixed_language() -> None:
    # 句读=中文句号/分号/换行（英文句点不切：「3.5升」里的点不能当句读）
    assert chunk_document("Made of steel。Keep it dry。") == ["Made of steel", "Keep it dry"]
    assert chunk_document("Material: steel") == ["Material: steel"]


# ---------- 切块：对话转写 ----------


def test_dialogue_chunks_by_turn() -> None:
    transcript = (
        "顾客：保温杯的净含量是多少？\n客服：根据已发布的规格文档，净含量为480ml。\n顾客：谢谢"
    )
    # 对话按行/轮成块：一轮=一条证据，句中的问号句号不拆（轮次完整性优先）
    assert chunk_dialogue(transcript) == [
        "顾客：保温杯的净含量是多少？",
        "客服：根据已发布的规格文档，净含量为480ml。",
        "顾客：谢谢",
    ]


def test_dialogue_cap_and_blank_lines() -> None:
    transcript = "\n".join(["顾客：问题", "客服：回答"] * 150)
    assert len(chunk_dialogue(transcript)) == MAX_CHUNKS
    assert chunk_dialogue("\n \n") == []


def test_chunk_text_dispatches_by_kind() -> None:
    # dialogue 走按行/轮切块；document 下「顾客：…」整行命中字段行口径，整行成块
    text = "顾客：净含量多少？客服：480ml。"
    assert chunk_text(text, "dialogue") == [text]
    assert chunk_text(text, "document") == [text]  # 字段行整行成块，句号不拆
    assert chunk_text("第一句。第二句。", "document") == ["第一句", "第二句"]


# ---------- 发布切块：确认字段块 + QA 对块（第 12 刀） ----------


class _FakeStorage:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def get_bytes(self, object_key: str) -> bytes:
        return self._data


def test_index_chunks_dialogue_adds_confirmed_qa_pair_blocks() -> None:
    """块序（第 16 刀 P1#5 改序）：confirmed qa_pairs 每对「问：/答：」块排在
    转写正文块**之前**——人洗成果价值密度高，[:MAX_CHUNKS] 截断先丢正文不丢 QA。
    本断言按新顺序更新（旧口径「正文在前 QA 续排」是审计刀 3 P1#5 要修的行为）。"""
    transcript = "顾客：几天到账？\n客服：质检后3个工作日到账"
    confirmed = {
        "qa_pairs": {
            "value": [
                {"q": "退款几天到账", "a": "质检后3个工作日到账"},
                {"q": "要不要吊牌", "a": "需保持吊牌完整"},
            ],
            "source": "human",
        }
    }
    chunks = index_chunks_for_version(
        _FakeStorage(transcript.encode()), "dialogue/x/1.txt", "dialogue", confirmed
    )
    assert chunks[0] == "问：退款几天到账\n答：质检后3个工作日到账"
    assert chunks[1] == "问：要不要吊牌\n答：需保持吊牌完整"
    assert chunks[2:] == ["顾客：几天到账？", "客服：质检后3个工作日到账"]  # 正文块其后


def test_index_chunks_long_transcript_keeps_confirmed_qa_before_truncation() -> None:
    """P1#5 钉子：>MAX_CHUNKS 行长转写 + 确认 1 对 QA -> 截断后索引里 QA 块
    仍在（旧顺序 QA 排尾，整批静默丢失）。"""
    transcript = "\n".join(f"顾客：轮次{i}的问题\n客服：轮次{i}的回答" for i in range(250))
    confirmed = {"qa_pairs": {"value": [{"q": "唯一确认对", "a": "确认答案"}], "source": "human"}}
    chunks = index_chunks_for_version(
        _FakeStorage(transcript.encode()), "dialogue/x/long.txt", "dialogue", confirmed
    )
    assert len(chunks) == MAX_CHUNKS  # 截断照常发生（防炸索引口径不变）
    assert chunks[0] == "问：唯一确认对\n答：确认答案"  # QA 块首位保住
    assert "问：唯一确认对\n答：确认答案" in chunks


def test_index_chunks_dialogue_skips_unconfirmed_and_empty_qa() -> None:
    transcript = "顾客：保修多久\n客服：整机一年"
    # 机洗草稿未确认（confirmed 无该字段）：不成块（0010 confirmed 才进索引）
    with_draft_only = index_chunks_for_version(
        _FakeStorage(transcript.encode()), "dialogue/x/2.txt", "dialogue", {}
    )
    assert not any(c.startswith("问：") for c in with_draft_only)
    # 确认「没有 QA」（空数组）：发布闸门可过、无 QA 块
    empty_confirmed = {"qa_pairs": {"value": [], "source": "human"}}
    chunks = index_chunks_for_version(
        _FakeStorage(transcript.encode()), "dialogue/x/3.txt", "dialogue", empty_confirmed
    )
    assert chunks == ["顾客：保修多久", "客服：整机一年"]


def test_index_chunks_document_confirmed_field_blocks_unchanged() -> None:
    # 文档确认字段仍走「字段名：值」单行块；QA 块逻辑只认 qa_pairs
    confirmed = {
        "净含量": {"value": "550毫升", "source": "human"},
        "qa_pairs": {"value": [{"q": "怪", "a": "怪"}], "source": "human"},  # 不该出现在文档
    }
    chunks = index_chunks_for_version(
        _FakeStorage("产品说明。".encode()), "documents/x/1.txt", "document", confirmed
    )
    assert "净含量：550毫升" in chunks
    assert "问：怪\n答：怪" in chunks  # 按字段值成块（防御口径，文档链路不产生此字段）


# ---------- 查询词法单元 ----------


def test_query_terms_filters_stop_chars() -> None:
    # 「的」是停用字：含它的 bigram 全部无效，跨虚词噪音（杯的/的净）不参与命中
    assert query_terms("保温杯的净含量？") == frozenset({"保温", "温杯", "净含", "含量"})


def test_query_terms_empty_for_pure_stopwords() -> None:
    assert query_terms("") == frozenset()
    assert query_terms("   ") == frozenset()
    assert query_terms("的吗？呢！") == frozenset()
    assert query_terms("怎么什么如何多少") == frozenset()


def test_query_terms_single_char_degrades_to_unigram() -> None:
    assert query_terms("杯") == frozenset({"杯"})
    assert query_terms("的") == frozenset()  # 单字停用字 -> 空（宁缺勿滥）


# ---------- 打分 ----------


def test_score_chunk_hits_and_misses() -> None:
    terms = query_terms("保温杯的净含量？")
    assert score_chunk(terms, "净含量：480ml") > 0
    assert score_chunk(terms, "保温杯杯身轻便耐用") > 0
    # 无交集：退货政策句不命中净含量查询
    assert score_chunk(terms, "七日无理由退货") == 0


def test_score_chunk_length_normalization() -> None:
    terms = query_terms("保温杯")
    short = score_chunk(terms, "保温杯")
    long_chunk = "保温杯" + "细节描述丰富做工精致包装讲究售后完善" * 5
    assert long_chunk.count("保温") >= 1  # 长块同样包含查询 bigram
    assert score_chunk(terms, long_chunk) < short  # 但长度归一压低长块分数


def test_score_chunk_empty_terms_is_zero() -> None:
    assert score_chunk(frozenset(), "任意内容") == 0
    assert score_chunk(query_terms("的吗"), "净含量：480ml") == 0


def test_retrieve_candidate_query_orders_by_chunk_id() -> None:
    """万行截断按主键排序，避免无 ORDER BY 的非确定 LIMIT。"""

    class _FakeResult:
        def all(self) -> list:
            return []

    class _FakeDb:
        def execute(self, stmt):
            self.stmt = stmt
            return _FakeResult()

    db = _FakeDb()
    assert retrieve(db, "净含量") == []  # type: ignore[arg-type]
    compiled = str(db.stmt.compile(compile_kwargs={"literal_binds": True}))
    normalized = " ".join(compiled.split())
    assert "ORDER BY retrieval_chunks.id" in normalized
    # 第 39 刀保鲜：候选 SQL 必须随带 last_verified_at（stale 降权的判据列）
    assert "last_verified_at" in normalized


# ---------- 保鲜降权（第 39 刀）：is_stale 纯函数 + 候选 SQL 判据列 ----------


def test_is_stale_none_is_never_stale() -> None:
    """null 不降权（spec 内嵌裁决钉死）：未验证=按新鲜处理。理由：存量/演示库
    资产 last_verified_at 全 NULL，NULL 降权=整库降权，评测基线数字必破——
    只降「显式验证过后放旧」的。"""
    now = datetime(2026, 9, 9, tzinfo=UTC)
    assert is_stale(None, now=now, days=90) is False
    # 阈值以内（89 天）也不 stale
    assert is_stale(now - timedelta(days=89), now=now, days=90) is False


def test_is_stale_after_threshold_days() -> None:
    now = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
    # 恰好 90 天：未超（>），91 天：超
    assert is_stale(now - timedelta(days=90), now=now, days=90) is False
    assert is_stale(now - timedelta(days=90, seconds=1), now=now, days=90) is True
    assert is_stale(now - timedelta(days=365), now=now, days=90) is True


def test_stale_days_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STALE_DAYS", "7")
    assert stale_days() == 7
    monkeypatch.delenv("STALE_DAYS")
    assert stale_days() == STALE_DAYS_DEFAULT
    monkeypatch.setenv("STALE_DAYS", "")
    assert stale_days() == STALE_DAYS_DEFAULT  # 空串回落默认（同 .env 口径）


def test_stale_multiplier_applied_as_post_factor() -> None:
    """降权是乘数后处理：score_chunk 本体不受影响（打分口径可独立校准）。"""
    terms = query_terms("保温杯")
    base = score_chunk(terms, "保温杯")
    assert base > 0
    assert base * STALE_MULTIPLIER == pytest.approx(base / 2)
