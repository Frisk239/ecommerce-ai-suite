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


# ---------- 第 46 刀：video 正文只来自 transcript 字段（纯函数，不依赖 ffmpeg） ----------


class _ExplodingStorage:
    """video 路径的字节是 mp4 二进制：任何一次读字节都是 bug，直接炸出来。

    （真切用例依赖 ffmpeg、无 ffmpeg 的环境整套 skip——这条纯单测保证「video
    永不读字节」在任何环境都有回归网。）"""

    def get_bytes(self, object_key: str) -> bytes:
        raise AssertionError(f"video 正文不该读对象字节: {object_key}")


def test_index_chunks_video_body_comes_from_transcript_field() -> None:
    extracted = {"transcript": {"value": "现场实测：灌95度热水六小时后63度。", "source": "machine"}}
    chunks = index_chunks_for_version(
        _ExplodingStorage(), "clips/x/1.mp4", "video", {}, extracted
    )
    assert chunks == ["现场实测：灌95度热水六小时后63度。"]


def test_index_chunks_video_without_transcript_field_has_empty_body() -> None:
    """字段缺失=正文为空（照常发布，只是没有正文块）——不回落读字节。"""
    assert index_chunks_for_version(_ExplodingStorage(), "clips/x/2.mp4", "video", {}, {}) == []
    assert index_chunks_for_version(_ExplodingStorage(), "clips/x/3.mp4", "video", {}) == []


def test_index_chunks_document_still_reads_bytes() -> None:
    """其余种类照旧读字节切块（回归钉子：video 分派没把别人带偏）。"""
    chunks = index_chunks_for_version(
        _FakeStorage("产品说明。".encode()), "documents/x/2.txt", "document", {}
    )
    assert chunks == ["产品说明"]


# ---------- 第 94a 刀：image 正文只来自「图片描述」字段（ADR 0051） ----------


def test_index_chunks_image_body_comes_from_confirmed_description() -> None:
    """图片字节是 png/jpeg/webp，解 UTF-8 必失败——正文只从「图片描述」字段进，
    永不读字节（_ExplodingStorage 任何一次读都炸出来）。"""
    confirmed = {
        "图片描述": {"value": "显示器侧面带可调节支架，正面三边窄边框", "source": "human"}
    }
    chunks = index_chunks_for_version(
        _ExplodingStorage(), "documents/x/1.png", "image", confirmed
    )
    assert chunks == ["显示器侧面带可调节支架，正面三边窄边框"]


def test_index_chunks_image_confirmed_wins_over_draft_and_is_not_duplicated() -> None:
    """confirmed 优先（0010：人确认才进索引），且正文供体字段**不再**补一块
    「字段名：值」——否则同一句描述在索引里存两份（第 94a 刀收口）。"""
    confirmed = {"图片描述": {"value": "带支架的黑色显示器", "source": "human"}}
    extracted = {"图片描述": {"value": "显示器（VLM 草稿，未确认）", "source": "machine"}}
    chunks = index_chunks_for_version(
        _ExplodingStorage(), "documents/x/2.png", "image", confirmed, extracted
    )
    assert chunks == ["带支架的黑色显示器"]  # 草稿不进；也没有「图片描述：…」重复块
    assert not any(chunk.startswith("图片描述：") for chunk in chunks)


def test_index_chunks_image_draft_alone_never_enters_index() -> None:
    """「VLM 只出草稿、人确认生效」（ADR 0051 口径）：extracted 里的草稿即便
    发布也不进索引——顾客面前不出现未经人确认的模型文本（与 video 允许回落
    extracted 的 46 刀口径刻意不同级，理由见 index_chunks_for_version）。"""
    extracted = {"图片描述": {"value": "VLM 草稿：一个带支架的显示器", "source": "machine"}}
    assert (
        index_chunks_for_version(_ExplodingStorage(), "documents/x/6.png", "image", {}, extracted)
        == []
    )


def test_index_chunks_image_without_description_has_empty_body() -> None:
    """没有描述（未填）的图片照常可发布，只是没有正文块——不回落读字节。"""
    assert index_chunks_for_version(_ExplodingStorage(), "documents/x/3.png", "image", {}) == []
    abstained = {"图片描述": {"abstained": True}}
    assert (
        index_chunks_for_version(_ExplodingStorage(), "documents/x/4.png", "image", {}, abstained)
        == []
    )


def test_index_chunks_video_confirmed_transcript_block_not_duplicated() -> None:
    """第 94a 刀同款收口在 video 上的回归钉子：确认字段里的 transcript 是正文
    供体，不再另补「transcript：…」块（罕见路径，一并收口）。"""
    confirmed = {"transcript": {"value": "内胆是316不锈钢", "source": "human"}}
    chunks = index_chunks_for_version(_ExplodingStorage(), "clips/x/5.mp4", "video", confirmed)
    assert chunks == ["内胆是316不锈钢"]


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


# ---------- 第 123 刀：拉丁/数字段整词 token（生产数据实测的权重修根） ----------


def test_query_terms_latin_word_is_single_token() -> None:
    """拉丁词一个 token（不是 O(len) 个 bigram）：品牌名与中文属性词权重对称。

    修根动机（OFF 重灌实测）：旧滑窗把「Nutella」切 6 个 bigram、「净含量」
    切 2 个——品牌块词法分恒为属性值块 ~2 倍，证据窗口被标题/品牌块占满，
    「Nutella的净含量是多少」拒答。
    """
    assert query_terms("Nutella的净含量是多少") == frozenset(
        {"nutella", "净含", "含量"}
    )
    # 大小写归一（旧 bigram 是大小写敏感的：nutella ≠ Nu）
    assert query_terms("NUTELLA nutella Nutella") == frozenset({"nutella"})
    # 字母↔数字边界切分：订单号/混合型号不粘成巨型 token
    assert query_terms("SO-1002") == frozenset({"so", "1002"})
    # 空格/逗号天然断词：品牌列表不拼接（先归一会丢分隔信息）
    assert query_terms("品牌：Nutella, Ferrero") == frozenset(
        {"品牌", "nutella", "ferrero"}
    )
    # 纯数字段整段一个 token
    assert query_terms("容量 400") == frozenset({"容量", "400"})


def test_score_chunk_attribute_value_outranks_brand_title() -> None:
    """生产形态钉子：问「拉丁品牌+属性」时，含答案的数值块须反超标题/品牌块。

    旧统一 bigram 下实测（A-2 库）：规格标题块 1.604 > 品牌块 1.604 >
    「净含量：400g」0.816——prompt 只取 top-2 证据，模型只见标题块，如实
    自述未覆盖 -> 第 58 刀收口成拒答。
    """
    terms = query_terms("Nutella的净含量是多少")
    value = score_chunk(terms, "净含量：400g")
    brand = score_chunk(terms, "品牌：Nutella, Ferrero")
    title = score_chunk(terms, "Nutella 榛子巧克力酱 规格")
    assert value > brand > 0
    assert value > title > 0


# ---------- 第 123 刀：字段行定向（属性问句的证据窗收口） ----------


def test_field_line_named_shape_gate() -> None:
    """形态闸：纯 CJK 短字段头 + 问句点名才触发；头行/QA/拉丁头不触发。"""
    from suite_api.services.retrieval import _field_line_named

    terms = query_terms("Coca-Cola 可乐的配料有什么")
    assert _field_line_named("配料：Agua carbonatada, azúcar", terms) is True
    assert _field_line_named("上市年份：2009", query_terms("Sennheiser 是哪年上市的")) is True
    # 品牌（单字段）不在问句里 -> 不定向（问配料不是问品牌）
    assert _field_line_named("品牌：COCA-COLA SERVICES SA/NV", terms) is False
    # 名字头行：无冒号形态（整块作头段，含拉丁/超长）不触发
    assert _field_line_named("Sennheiser HD 800 规格", query_terms("Sennheiser HD 800 怎么样")) is False
    # QA 块单字头「问」进不了问句词法（请/问等虚词被停用字滤掉）
    assert _field_line_named("问：退货怎么办", query_terms("请问退货怎么办")) is False
    # 长头段（>6 字 CJK，如句子行）不触发
    assert _field_line_named("签收后七天内可申请退货：详见政策", query_terms("退货政策")) is False


def test_field_directed_promotes_top_asset_value_chunk() -> None:
    """第 123 刀重排钉子：问句点名字段时 top-1 资产自己的字段行进首槽。

    生产形态：数值行词法分低（长值撑大分母），融合序里被名字头行/图片描述/
    兄弟商品头行压到 3-5 位——证据窗（prompt/引用前 2）看不见，模型如实自述
    未覆盖→拒答。重排只动赢家资产先出哪块：跨资产次序与分数不动（乘数方案
    实测会把兄弟商品的短字段块抬到第 1，评测 -4.4pp，见 _field_directed 注释）。
    """
    from suite_api.services.retrieval import _field_directed

    terms = query_terms("Coca-Cola 可乐的配料有什么")
    fused = [
        {"asset_id": 6, "chunk": "Coca-Cola 可乐 330ml 规格", "score": 1.33},
        {"asset_id": 5, "chunk": "Coca-Cola 可乐 330ml 的产品实拍图", "score": 1.02},
        {"asset_id": 6, "chunk": "品牌：COCA-COLA SERVICES SA/NV", "score": 0.90},
        {"asset_id": 6, "chunk": "配料：Agua carbonatada, azúcar", "score": 0.16},
    ]
    out = _field_directed(fused, terms)
    # 配料行（top-1 资产 A-6 自己的）提到首槽；其余三条次序原样
    assert out[0]["chunk"].startswith("配料：")
    assert [h["chunk"] for h in out[1:]] == [fused[0]["chunk"], fused[1]["chunk"], fused[2]["chunk"]]
    # 问句没点名字段（纯实体问）时零变化
    plain = _field_directed(fused, query_terms("Coca-Cola 可乐 330ml 怎么样"))
    assert plain == fused
    # 赢家资产没有被问字段行时零变化（字段行属于兄弟资产不动它）
    fused2 = [
        {"asset_id": 5, "chunk": "Coca-Cola 可乐 330ml 的产品实拍图", "score": 1.4},
        {"asset_id": 6, "chunk": "配料：Agua carbonatada, azúcar", "score": 0.16},
    ]
    assert _field_directed(fused2, terms) == fused2



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
    # 第 39 刀保鲜：候选 SQL 必须随带 last_verified_at（stale 降权的判据列）；
    # 第 66 刀评论适用域：随带 source_kind（判据列同款纪律）
    assert "last_verified_at" in normalized
    assert "source_kind" in normalized


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


# ---------- 评论证据的适用域（第 66 刀，审计刀 13 P0-2） ----------


@pytest.mark.parametrize(
    "query, expected",
    [
        ("到货了吗", True),  # 审计实测：曾引英文 account_access 评论（「到货后看着很多」）
        ("什么时候能收到货", True),
        ("退货运费多少钱", True),  # 曾首引衣服评论（0.447 压过退货政策 0.408）
        ("我的订单到哪了", True),
        ("退货政策是什么", True),  # 政策问：该答的是政策文档，不是评论
        ("物流怎么样", False),  # 观点问：评论正是对的证据（金标 conf-013 期望评论资产）
        ("洗发水到货后看着很多怎么样", False),  # 金标 pos-029 同款形态
        ("请问手机评价 这个机子值得入手吗", False),  # 金标 syn-021 同款形态
        ("保温杯的净含量是多少", False),  # 无服务词：评论闸不触发
        # ---- 第 66 刀评审补齐（P1#2/P2#3，金标程序化核验零回归）----
        ("这家的退货体验如何", False),  # 「如何」与「怎么样」同频，漏了会饿死观点问
        ("发货快吗", False),  # 有服务词的**观点问**——评论正是对的证据
        ("物流慢不慢", False),
        ("换货流程怎么走", True),  # 服务词 += 售后/换货/客服/保修
        ("售后政策", True),
        ("退货怎么办理", True),  # 程序问（怎么办**理**）：该滤
        # 已知取舍（评审 P2#4，钉住）：「不值得/不推荐」含「值得/推荐」仍豁免——
        # 豁免侧从宽比错杀轻（错杀把可答变拒答；从宽只是退回闸前词法形态）
        ("退货流程不值得吐槽吗", False),
        # ---- 复审审计 P1（B/C 轴同源）：咋字族与可靠 ----
        ("物流咋样", False),  # 67 刀把咋字族在报价侧合法化后，观点表不同步会饿死同义问
        ("快递什么样", False),
        ("快递可靠吗", False),  # 靠谱的同义词
        # ---- 复审审计 P2：服务词补齐 ----
        ("包邮吗", True),
        # ---- 第 69 刀（评审订正后）：扩观点尾白名单，保持默认拦 ----
        # 初版用「事实问尾白名单」收窄，评审证伪：事实尾是开集，「退货运费谁承担」
        # 「查物流」等未枚举同义全部漏放并引无关评论（66 刀 P0 同义复现）。按
        # 「误判比漏检贵」反转：漏枚举观点尾=诚实拒答（便宜），漏枚举事实尾=
        # 错误答案（贵）。观点尾=品质形容词+吗 的实测形态。
        ("客服态度好吗", False),  # 好吗=观点尾——评论正是证据
        ("退货麻烦吗", False),
        ("快递包装结实吗", False),
        ("发货及时吗", False),
        ("退款顺利吗", False),
        ("退货爽快吗", False),
        ("物流给力吗", False),
        ("到货了么", True),
        # 事实问的同义形态（评审 P1 实测漏放面）：默认拦，全部钉住
        ("退货运费谁承担", True),
        ("退货要运费吗", True),
        ("查物流", True),
        ("物流信息", True),
        ("发货地是哪里", True),
        ("支持退货吗", True),
        ("收货地址能改吗", True),
        ("可以开发票吗", True),
        # ---- 审计刀 14 B 轴 P0：裸「如何」误豁免事实问（如何+动词）----
        ("退货运费如何计算", True),  # 曾引衣服评论 0.447 压过退货政策——66 刀 P0 同义复现
        ("售后如何处理", True),
        ("物流信息如何查询", True),
        ("快递如何查", True),
        ("物流如何", True),  # 裸「如何」落诚实拒答（便宜的失效方向，主动选择）
        # 观点复合词保留（体验/服务/态度/速度如何——问的还是评价本身）
        ("这家的退货体验如何", False),
        ("客服服务如何", False),
        ("物流速度如何", False),
        # 审计刀 14 B 轴 P2：登记实测体感形态
        ("物流省心吗", False),
        ("快递准时吗", False),
        ("物流好评吗", False),
    ],
)
def test_excludes_review_evidence_truth_table(query: str, expected: bool) -> None:
    """纯函数真值表：服务状态词 × 观点标记豁免。

    豁免是零回归的关键——金标 19 条期望评论资产的 case **全部**带观点标记
    （怎么样/值得入手吗/评价），实测大集逐位相同（70.0/65.0）。
    """
    from suite_api.services.retrieval import excludes_review_evidence

    assert excludes_review_evidence(query) is expected


def _seed_published_asset(
    db: object, title: str, chunks: list[str], source_kind: str
) -> int:
    """直接落一行已发布资产 + 版本 + 切块（检索闸只看这三样，不走 API 全流程）。"""
    from suite_api.models import Asset, AssetVersion, RetrievalChunk

    asset = Asset(kind="document", status="published", source_kind=source_kind, title=title)
    db.add(asset)
    db.flush()
    version = AssetVersion(asset_id=asset.id, version_no=1, object_key="test-key")
    db.add(version)
    db.flush()
    asset.current_published_version_id = version.id
    for seq, text in enumerate(chunks, 1):
        db.add(RetrievalChunk(asset_id=asset.id, version_no=1, seq=seq, chunk=text))
    return asset.id


def test_retrieve_drops_review_chunks_for_service_state_query(api: object) -> None:
    """服务状态问下评论块不算证据（真库）：政策文档照常命中、评论被滤掉。

    分数阈值分不开这个面（实测坏 case 0.4–0.5、金标真命中最低 0.17——短评论块
    bigram 少分数天然高），分得开的是证据**类别**与问句**意图**。评论块故意
    写得比政策块**更命中**（多一个 bigram），钉住「不是分数排序问题」。
    """
    client, _ = api
    factory = client.app.state.session_factory
    with factory() as db:
        from suite_api.services.retrieval import retrieve

        review_id = _seed_published_asset(
            db,
            "衣服评论 · 发货速度慢",
            ["等了好久才到货，还要我自己承担运费"],  # 同时撞 到货+运费 bigram
            source_kind="review_import",
        )
        policy_id = _seed_published_asset(
            db,
            "退货政策",
            ["签收后7天内可申请退货，运费商家承担"],
            source_kind="upload",
        )
        db.commit()

        # 状态/政策问：评论被滤（即便它分数更高），政策文档命中
        hits = retrieve(db, "退货运费多少钱")
        assert hits, "政策文档仍应命中"
        assert all(h["asset_id"] != review_id for h in hits), "评论不该再出现"
        assert hits[0]["asset_id"] == policy_id

        # 纯状态问只剩评论证据：无命中（引擎侧即拒答留缺口）
        assert retrieve(db, "到货了吗") == []

        # 观点豁免：同一批评论在观点问下照常命中（金标 19 条的形态——服务词
        # 到货 + 观点标记怎么样，与 pos-029「到货后看着很多怎么样」同构）
        opinion = retrieve(db, "到货速度怎么样")
        assert any(h["asset_id"] == review_id for h in opinion)

        # 拼接缝（评审 P1）：闸必须按**本问**判定——上一问的观点标记（怎么样）
        # 不能豁免本问的服务态问句，否则拼接检索原样放行 garbage 评论
        glued = "物流怎么样 它到货了吗"
        assert retrieve(db, glued) != [], "缺省按 query 判（无观点标记的本问场景）"
        assert retrieve(db, glued, gate_question="它到货了吗") == []


# ---------- 实体亲和重排（第 79 刀，ADR 0048） ----------


def test_title_affinity_full_coverage_is_one() -> None:
    """问句覆盖标题全部区分性 bigram -> 1.0（问句点名了该资产）。"""
    from suite_api.services.retrieval import title_affinity, title_idf

    idf = title_idf(["钛钢保温杯 规格", "Erdbeeren 规格"])
    # 「钛钢保温杯的容量」terms 含标题「钛钢保温杯」的全部 bigram（规格不含）
    aff = title_affinity(query_terms("钛钢保温杯的容量"), "钛钢保温杯 规格", idf)
    assert 0.0 < aff < 1.0  # 规格未被覆盖，按 idf 权重折算（部分覆盖）
    # 全覆盖（问句覆盖标题全部区分性 bigram）-> 恰为 1.0（名实相符）
    full = title_affinity(query_terms("钛钢保温杯规格是多少"), "钛钢保温杯 规格", idf)
    assert full == 1.0


def test_title_affinity_named_asset_beats_shared_words() -> None:
    """共享 bigram（规格，两标题都有 -> 低 idf）几乎不贡献亲和，专名主导。"""
    from suite_api.services.retrieval import title_affinity, title_idf

    idf = title_idf(["M&M white 规格（OFF）", "Erdbeeren 规格（OFF）"])
    terms = query_terms("Erdbeeren的条码是多少")
    named = title_affinity(terms, "Erdbeeren 规格（OFF）", idf)
    other = title_affinity(terms, "M&M white 规格（OFF）", idf)
    assert named > other == 0.0  # 问句不含共享词（规格）时他品亲和为 0：专名主导
    # 问句带上共享字段词后他品亲和仍远低于点名资产——idf 对共享词只做温和
    # 压制（df=N 时下界 log2≈0.69，不是近零），真正的差距来自比值归一里
    # 点名资产的专名覆盖
    field_terms = query_terms("Erdbeeren的规格是多少")
    named2 = title_affinity(field_terms, "Erdbeeren 规格（OFF）", idf)
    other2 = title_affinity(field_terms, "M&M white 规格（OFF）", idf)
    assert named2 > other2 > 0.0


def test_title_affinity_blank_title_is_zero() -> None:
    """空标题/纯停用字标题亲和 0（乘数 1=中性，不因无标题被显式惩罚）。"""
    from suite_api.services.retrieval import title_affinity, title_idf

    idf = title_idf(["退货政策说明", ""])
    assert title_affinity(query_terms("退货政策是多少"), "", idf) == 0.0
    assert title_affinity(query_terms("退货政策是多少"), None, idf) == 0.0
    # 纯停用字标题无有效 bigram -> 0（docstring 声明的面，补钉）
    assert title_affinity(query_terms("退货政策是多少"), "的了吗", idf) == 0.0


def test_retrieve_entity_affinity_breaks_cross_product_tie(api: object) -> None:
    """真库：同文字段块跨资产并列时，问句点名的资产破开并列（病根正钉）。

    构造复现 75 刀病根形态：A/B 两资产各有一个「净含量：500ml」**同文**块
    （字段名+值全同 -> 词法分完全并列），且 B 的标题不含问句实体。被点名资产
    **后建**（id 更大）——baseline 的 (asset_id, chunk) 稳定序会把 B 排第一，
    只有亲和乘数真正生效时 A 才翻到 top-1（把 AFFINITY_ALPHA 置 0 本用例
    必红——评审证伪出伪钉后的反转钉法）。两个真库用例用互不相同的商品域
    （杯/奶粉），切断 module 级共享库下跨用例的同标题并列污染。
    """
    client, _ = api
    factory = client.app.state.session_factory
    with factory() as db:
        other_id = _seed_published_asset(
            db,
            "Erdbeeren 规格（OFF）",
            ["净含量：500ml"],  # 同文块：词法分与 A 完全并列；先建（id 小）
            source_kind="openfoodfacts",
        )
        named_id = _seed_published_asset(
            db,
            "钛钢保温杯 规格",
            ["净含量：500ml"],  # 后建（id 大）：baseline 稳定序下排第二
            source_kind="upload",
        )
        db.commit()

        hits = retrieve(db, "钛钢保温杯的净含量是多少")
        assert [h["asset_id"] for h in hits[:2]] == [named_id, other_id]
        assert hits[0]["chunk"] == "净含量：500ml"

        # 问句与两标题均无 bigram 交集（标题不含「净含/含量」字段词）时乘数
        # 恒 1、零漂移——两块仍按 (asset_id, chunk) 稳定序出榜。这是中性面的
        # 真实边界（无交集才恒 1）；标题含字段词时纯字段问也有亲和，属设计内
        # 行为（ADR 0048 已知取舍：混淆组提升正来自「字段词恰入标题」）。
        plain = retrieve(db, "净含如何")
        assert plain, "字段词问句仍应命中"
        assert [h["asset_id"] for h in plain[:2]] == [other_id, named_id]


def test_retrieve_entity_affinity_overrides_shorter_rival(api: object) -> None:
    """近同文且他品块更短（词法分更高）时，点名资产靠亲和翻盘（非并列形态）。

    病根的第二形态（75 刀实测「净含/含量类被拉向块更短更实的资产」）：
    B 的块字段名相同、值更短 -> sqrt 归一分母更小 -> 词法分微弱胜出。
    亲和乘数把问句点名的资产抬回 top-1。
    """
    client, _ = api
    factory = client.app.state.session_factory
    with factory() as db:
        other_id = _seed_published_asset(
            db,
            "安慕希酸奶 规格",
            ["净含量：500g"],  # 更短 -> 词法分更高（baseline 下 top-1）；先建
            source_kind="openfoodfacts",
        )
        named_id = _seed_published_asset(
            db,
            "雀巢奶粉 规格",
            ["净含量：500毫升"],  # 值更长 -> 分母更大 -> 词法分更低；后建
            source_kind="upload",
        )
        db.commit()

        # baseline 形态自检：亲和停用（alpha=0）时更短的他品块夺冠——证明本
        # 用例测的确实是「亲和翻盘」而非稳定序/建序巧合（评审证伪后补的钉）。
        # 断言限定在**本用例两资产**的配对序上（第 126 刀审计加固：模块共享库
        # 里早前用例的资产若恰也命中问句，全局 top-1 与本用例无关——旧写法
        # 隐含「干净语料」假设，集成环境整模块跑实测踩中）。
        from suite_api.services import retrieval as retrieval_module

        original_alpha = retrieval_module.AFFINITY_ALPHA
        retrieval_module.AFFINITY_ALPHA = 0.0
        try:
            baseline = [
                h
                for h in retrieve(db, "雀巢奶粉的净含量是多少")
                if h["asset_id"] in (other_id, named_id)
            ]
            assert baseline[0]["asset_id"] == other_id, "alpha=0 下更短他品块应夺冠"
        finally:
            retrieval_module.AFFINITY_ALPHA = original_alpha

        hits = [
            h
            for h in retrieve(db, "雀巢奶粉的净含量是多少")
            if h["asset_id"] in (other_id, named_id)
        ]
        assert hits[0]["asset_id"] == named_id, "点名资产须靠亲和赢过更短的他品块"


def test_retrieve_excludes_discarded_published_asset(api: object) -> None:
    """第 83 刀：**已发布后废弃**（0042 discarded_at）的资产不进检索候选。

    此前 retrieve 只过滤 status='published'——废弃资产照样进候选（实测
    「羊绒围巾起球怎么办」的引用里出现刚废弃的资产）。0042 语义是「标记隐藏」，
    检索面必须同口径。
    """
    from suite_api.models import Asset

    client, _ = api
    factory = client.app.state.session_factory
    with factory() as db:
        body = "羊绒围巾起球：使用去球器轻剃"
        kept = _seed_published_asset(db, "羊绒围巾起球处理", [body], "upload")
        dropped = _seed_published_asset(db, "羊绒围巾起球处理（副本）", [body], "upload")
        db.commit()
        db.get(Asset, dropped).discarded_at = datetime.now(UTC)
        db.commit()

        hits = retrieve(db, "羊绒围巾起球怎么办")
        ids = [h["asset_id"] for h in hits]
        assert kept in ids
        assert dropped not in ids, "废弃资产不得进检索候选"
