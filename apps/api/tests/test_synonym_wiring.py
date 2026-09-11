"""检索同义词接线单测（第 36 刀，全离线：纯函数 + 内存 fake 候选行）。

钉三件事（spec Must 4）：
- 同义词问句命中原文块（表内词轮转一位后撞回同组另一说法的证据）；
- 无同义词问句零漂移（归一前后 query_terms 逐位一致，retrieve 行为不变）；
- 多轮代词拼接检索词经同一 retrieve 入口归一（conversation_memory 拼接串受益）。
"""

from datetime import datetime
from typing import Any

from suite_api.services.conversation_memory import retrieval_query
from suite_api.services.retrieval import query_terms, retrieve
from suite_api.services.synonyms import apply_synonyms


class _FakeResult:
    def __init__(self, rows: list[tuple[int, int, str, datetime | None]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[int, int, str, datetime | None]]:
        return self._rows


class _FakeDb:
    """只够 retrieve() 用的候选行源：execute(stmt).all() 返回候选行。

    调用方仍给 3 元组 (asset, version, chunk)；第 39 刀起候选 SQL 随带保鲜
    元数据（last_verified_at），第 66 刀再随带 source_kind（评论适用域闸），
    retrieve 按 5 元组解包——fake 在此统一补 None + "upload"（未验证不降权、
    非评论不被适用域闸滤），既有用例正文零改动。"""

    def __init__(self, rows: list[tuple[int, int, str]]) -> None:
        self._rows = [(a, v, c, None, "upload") for a, v, c in rows]

    def execute(self, stmt: Any) -> _FakeResult:
        return _FakeResult(self._rows)


# ---------- ① 同义词问句命中原文块 ----------


def test_synonym_query_hits_block_with_rotated_word() -> None:
    """块原文是「质保」，问「保修」（组内另一说法）——归一后命中。"""
    db = _FakeDb([(1, 1, "整机质保两年，人为损坏不在范围内")])
    hits = retrieve(db, "保修怎么样")  # type: ignore[arg-type]
    assert hits and hits[0]["asset_id"] == 1


def test_rotation_direction_is_pinned_by_miss() -> None:
    """轮转语义钉子：块只含「质保」，问「三包」轮到「保修」——块里没有，不命中。

    钉死 retrieve 走的确实是同义词路径（若没接线，「三包」bigram 同样不命中，
    但「保修怎么样」这条已证明表词进了查询；两条合起来钉轮转方向）。
    """
    db = _FakeDb([(1, 1, "整机质保两年")])
    assert retrieve(db, "三包政策如何") == []  # type: ignore[arg-type]
    assert retrieve(db, "保修怎么样") != []  # type: ignore[arg-type]


# ---------- ② 无同义词问句零漂移 ----------

_NO_SYNONYM_QUERIES = (
    "钛杯多重",
    "退款到账要几天？",
    "这个手机支持分期吗",
    "尺码偏大还是偏小",
    "Seven 测试 English abc",
    "",
    "   ",
    "的吗呢吧",
)


def test_no_synonym_query_has_zero_drift() -> None:
    """spec 裁决的零漂移口径：表外问句归一前后词法单元逐位一致。"""
    for q in _NO_SYNONYM_QUERIES:
        assert query_terms(apply_synonyms(q)) == query_terms(q), q


def test_retrieve_output_identical_for_synonym_free_query() -> None:
    """同一候选集下，表外问句 retrieve 结果（id/版本/块/分）与手工归一后一致。"""
    rows = [
        (1, 1, "钛杯容量五百毫升"),
        (2, 1, "支持七天无理由退款"),
        (3, 2, "配送范围覆盖全国"),
    ]
    for q in _NO_SYNONYM_QUERIES:
        direct = retrieve(_FakeDb(rows), q)  # type: ignore[arg-type]
        manual = retrieve(_FakeDb(rows), apply_synonyms(q))  # type: ignore[arg-type]
        assert direct == manual, q


def test_empty_query_stays_empty() -> None:
    """零命中保护：空查询/纯停用词仍空（0018 宁缺勿滥口径不变）。"""
    assert retrieve(_FakeDb([(1, 1, "任意块")]), "") == []  # type: ignore[arg-type]
    assert retrieve(_FakeDb([(1, 1, "任意块")]), "的吗呢吧") == []  # type: ignore[arg-type]


# ---------- ③ 代词拼接检索词归一 ----------


def test_pronoun_concatenated_query_normalized_via_retrieve() -> None:
    """「那它的保修政策是什么」拼接上一问后经 retrieve 入口归一，命中「质保」块。

    无接线时拼接串里的「保修」bigram 与「质保」块无交集——本测即钉接线对
    多轮记忆检索词同入口生效（chat_engine 走的就是这个入口）。
    """
    db = _FakeDb([(7, 1, "本店商品质保一年")])
    history = [
        {"role": "customer", "content": "这个钛杯多重"},
        {"role": "agent", "content": "约三百克"},
    ]
    concatenated = retrieval_query("那它的保修政策是什么", history)
    assert concatenated == "这个钛杯多重 那它的保修政策是什么"
    hits = retrieve(db, concatenated)  # type: ignore[arg-type]
    assert hits and hits[0]["asset_id"] == 7
