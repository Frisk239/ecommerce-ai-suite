"""检索切块与词法打分单测（纯函数，无 DB）。

覆盖：文档/对话切块（字段行、句级、长句逗号断、上限）、查询词法单元
（停用词过滤、单字退化 unigram）、打分（命中/不命中/长度归一）。
"""

from suite_api.services.retrieval import (
    MAX_CHUNKS,
    chunk_dialogue,
    chunk_document,
    chunk_text,
    query_terms,
    retrieve,
    score_chunk,
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
