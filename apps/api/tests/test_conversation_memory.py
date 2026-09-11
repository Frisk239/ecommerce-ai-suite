"""会话多轮记忆单测（第 29 刀 feat/multi-turn）。

不依赖真 PG：recent_turns 的 where/排序（exclude/created_at 倒序）由集成
测试在真库上钉（test_multi_turn_integration.py），这里只钉配对/跳过/窗口/
代词拼接的纯逻辑——fake db 按传入序原样返回行，测试自己构造「最新在前」
的行序模拟 SQL order_by 的结果。
"""

from types import SimpleNamespace
from typing import Any

from suite_api.services.conversation_memory import (
    PRONOUN_RE,
    recent_turns,
    retrieval_query,
)


class _FakeScalars:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def __iter__(self) -> Any:
        return iter(self._rows)


class _FakeDb:
    def __init__(self, rows_newest_first: list[Any]) -> None:
        self._rows = rows_newest_first

    def scalars(self, _stmt: Any) -> _FakeScalars:
        return _FakeScalars(self._rows)


def _msg(mid: int, role: str, content: str, *, kind: str | None = None, tool: Any = None) -> Any:
    return SimpleNamespace(id=mid, role=role, content=content, kind=kind, tool=tool)


def _turn(qid: int, question: str, aid: int, answer: str, *, kind: str = "answer", tool: Any = None):
    return _msg(qid, "customer", question), _msg(aid, "agent", answer, kind=kind, tool=tool)


def _rows(*turns_newest_first) -> list[Any]:
    """轮按「最新在前」给 -> 展开成行序（每轮 agent 比 customer 新）。"""
    return [message for pair in turns_newest_first for message in (pair[1], pair[0])]


# ---------- recent_turns：配对与正序 ----------


def test_recent_turns_pairs_and_returns_chronological_order() -> None:
    older = _turn(2, "钛杯净含量多少", 3, "净含量500ml")
    newer = _turn(4, "怎么清洗", 5, "可拆洗")
    history = recent_turns(_FakeDb(_rows(newer, older)), session_id=1)
    assert history == [
        {"role": "customer", "content": "钛杯净含量多少"},
        {"role": "agent", "content": "净含量500ml"},
        {"role": "customer", "content": "怎么清洗"},
        {"role": "agent", "content": "可拆洗"},
    ]


def test_recent_turns_truncates_to_window() -> None:
    """窗口 N=4：六轮只留最近四轮（正序首条=第 3 轮问句，第 1/2 轮被截掉）。"""
    turns = [_turn(q, f"问{q}", q + 1, f"答{q}") for q in range(2, 14, 2)]  # 6 轮（旧在前）
    history = recent_turns(_FakeDb(_rows(*reversed(turns))), session_id=1)
    assert [m["content"] for m in history] == [
        "问6", "答6", "问8", "答8", "问10", "答10", "问12", "答12",
    ]


def test_recent_turns_skips_unanswered_customer_residue() -> None:
    """生成失败残留的裸问句（无 agent 答）不成轮：最新残问被跳过。"""
    residue = _msg(9, "customer", "没答上的问句")
    turn = _turn(7, "问句", 8, "回答")
    history = recent_turns(_FakeDb([residue, *_rows(turn)]), session_id=1)
    assert history == [
        {"role": "customer", "content": "问句"},
        {"role": "agent", "content": "回答"},
    ]


# ---------- recent_turns：跳过规则三态（整轮跳，含问句） ----------


def test_recent_turns_skips_refusal_handoff_and_tool_turns() -> None:
    valid_old = _turn(2, "旧问句", 3, "旧回答")
    refusal = _turn(4, "拒答的问句", 5, "抱歉，已发布资产里没有能回答这个问题的证据。", kind="refusal")
    handoff = _turn(6, "转人工的问句", 7, "订单工具转人工文案", kind="handoff")
    tool = _turn(8, "工具轮问句", 9, "订单 SO-1001 已发货", tool={"name": "get_order_status", "arg": "SO-1001"})
    valid_new = _turn(10, "新问句", 11, "新回答")
    history = recent_turns(_FakeDb(_rows(valid_new, tool, handoff, refusal, valid_old)), session_id=1)
    # 三态轮整轮消失：拒答/转人工/工具的问句与回答都不进记忆
    assert history == [
        {"role": "customer", "content": "旧问句"},
        {"role": "agent", "content": "旧回答"},
        {"role": "customer", "content": "新问句"},
        {"role": "agent", "content": "新回答"},
    ]


# ---------- recent_turns：redact（0038：进厂商 prompt 必掩） ----------


def test_recent_turns_redacts_pii_in_history() -> None:
    turn = _turn(2, "我的13812345678怎么改绑", 3, "回拨13812345678核实")
    history = recent_turns(_FakeDb(_rows(turn)), session_id=1)
    assert [m["content"] for m in history] == [
        "我的1********78怎么改绑",
        "回拨1********78核实",
    ]


# ---------- 检索词补全：代词检测与拼接 ----------


def test_retrieval_query_concatenates_prev_question_for_pronoun() -> None:
    history = [
        {"role": "customer", "content": "钛杯的净含量是多少"},
        {"role": "agent", "content": "净含量为500ml"},
    ]
    assert retrieval_query("那它的保修政策是什么", history) == (
        "钛杯的净含量是多少 那它的保修政策是什么"
    )


def test_retrieval_query_unchanged_without_pronoun_or_history() -> None:
    history = [
        {"role": "customer", "content": "钛杯的净含量是多少"},
        {"role": "agent", "content": "净含量为500ml"},
    ]
    # 无代词：即使有历史也不拼接
    assert retrieval_query("钛杯的材质是什么", history) == "钛杯的材质是什么"
    # 有代词但无历史（首问/前轮全被跳过）：不拼接，记忆不制造检索词
    assert retrieval_query("那它的材质是什么", []) == "那它的材质是什么"
    # 防御：历史里没有 customer 问（真实 recent_turns 不会产出）原样返回
    assert retrieval_query("那它的材质是什么", [{"role": "agent", "content": "答"}]) == "那它的材质是什么"


def test_pronoun_pattern_matches_spec_lexicon() -> None:
    """spec Must 3 词表：它|他|她|这个|那个|这款。"""
    for text in ("那它呢", "他多少钱", "她呢", "这个怎么洗", "那个保修", "这款有货"):
        assert PRONOUN_RE.search(text), text
    for text in ("钛杯的净含量是多少", "材质是什么", "保修政策"):
        assert not PRONOUN_RE.search(text), text


# ---------- 审计刀 13 C 轴 P0-1：拼接检索的本问保底 ----------


def test_merge_own_hits_appends_fresh_hits_capped_at_two() -> None:
    """拼接生效时本问裸检索的新命中去重后并入（≤2），已有命中不丢不重。

    复现背景：净含量问 → 「那它的材质是什么」时，拼接 top-5 全是净含量块，
    「材质：钛钢」被挤出——模型上下文里没有材质证据只能拒答（README 演示口径）。
    """
    from suite_api.services.chat_engine import merge_own_hits

    def hit(asset: int, chunk: str) -> dict[str, object]:
        return {"asset_id": asset, "version_no": 1, "chunk": chunk}

    glued = [hit(9, "净含量：500ml"), hit(226, "净含量：80g")]
    own = [
        hit(13, "材质：钛钢"),  # 新命中：并入
        hit(9, "净含量：500ml"),  # 已在拼接结果里：去重
        hit(3, "材质：316不锈钢"),  # 新命中：并入
        hit(10, "材质：316不锈钢"),  # 超上限：截断
    ]
    merged = merge_own_hits(glued, own)
    # **交错**而非追加：prompt/引用只看前两条（_MAX_PROMPT_EVIDENCE=2），
    # 追加在尾部等于没并——窗口必须是「语境（拼接首位）+ 主题（本问首位）」
    assert [(h["asset_id"], h["chunk"]) for h in merged] == [
        (9, "净含量：500ml"),
        (13, "材质：钛钢"),
        (226, "净含量：80g"),
        (3, "材质：316不锈钢"),
    ]


def test_merge_own_hits_keeps_glued_order_when_all_dupes() -> None:
    """本问命中全部已在拼接结果里：原样返回（不重复、不加塞）。"""
    from suite_api.services.chat_engine import merge_own_hits

    glued = [{"asset_id": 9, "version_no": 2, "chunk": "净含量"}]
    assert merge_own_hits(glued, list(glued)) == glued
