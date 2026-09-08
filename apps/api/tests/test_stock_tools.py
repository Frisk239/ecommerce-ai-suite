"""库存工具单测（第 14 刀/ADR 0037，无 DB）：

- 词表分派：STOCK_KEYWORD_PATTERN 命中/不命中（规格词零误伤）。
- match_product 纯函数：LCS ≥2 字、「保温杯」截取「钛钢保温杯」、多命中取
  最长、平手取 id 小、无关问题 None、1 字公共不算命中。
- get_stock / query_stock 分支（fake db：命中/未命中/DB 异常吞成 {error}+回滚）。
- 摘要与五分支模板文案（stock>0 / ==0 / NULL / 未命中 / error）。
- run_ask 分派：库存路径 retrieve/gap/LLM 三哨兵零调用、kind/文案；订单分派
  优先于库存（同含 SO-1001 与「有货」走订单）；非库存非订单对工具零接触。
- sse_event_stream：库存路径 thinking(查询库存中…) -> tool -> delta* ->
  complete 带 tool；订单路径文案不回归。
"""

import asyncio
import json
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import SQLAlchemyError

from suite_api.models import Product, ServiceMessage
from suite_api.services import stock_tools
from suite_api.services.answer import ComposedAnswer
from suite_api.services.chat_engine import AskOutcome, run_ask, sse_event_stream


def _parse(events: list[str]) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    for block in events:
        lines = block.strip().splitlines()
        event = next(line.removeprefix("event: ") for line in lines if line.startswith("event: "))
        data = json.loads(
            next(line.removeprefix("data: ") for line in lines if line.startswith("data: "))
        )
        out.append((event, data))
    return out


def _product(pid: int, name: str) -> Product:
    """内存对象（不绑 session）：match/get 单测只读 name/stock 属性。"""
    return Product(id=pid, name=name, category="测试", spec_schema={}, spec_values={})


_SEED_LIKE: list[Product] = [_product(1, "瓶装水"), _product(2, "钛钢保温杯")]


# ---------- 词表分派 ----------


@pytest.mark.parametrize(
    "question",
    [
        "钛钢保温杯有货吗？",
        "瓶装水没货了吗",
        "现在是无货状态？",
        "这个缺货吗？",
    ],
)
def test_stock_pattern_hits(question: str) -> None:
    assert stock_tools.STOCK_KEYWORD_PATTERN.search(question) is not None


@pytest.mark.parametrize(
    "question",
    [
        "保温杯的净含量是多少？",  # 规格问题：必须不命中（零漂移走检索）
        "饮用水的保质期是多久？",
        "材质是什么？",
        "怎么退货？",
        "我的订单 SO-1001 到哪了？",
        # 第 16 刀词表收窄（P1#3）：通用词与「剩」不再是分派词——
        "库存还剩多少？",  # 无商品名的泛问回检索
        "有现货吗",  # 「现货」吞已发布政策文档，已去
        "还剩下几件？",  # 「剩」误伤规格问句，已去
        "保温杯还剩多少毫升",  # 规格问句含「剩」：不命中，回检索不误答有货
        "库存政策是什么",  # 含「库存」的政策问句：不命中，政策文档可被检索命中
        "",
    ],
)
def test_stock_pattern_misses(question: str) -> None:
    assert stock_tools.STOCK_KEYWORD_PATTERN.search(question) is None


# ---------- match_product（LCS 纯函数） ----------


def test_match_product_lcs_from_longer_name() -> None:
    """「保温杯有货吗」以 3 字 LCS「保温杯」命中更长商品名「钛钢保温杯」。"""
    hit = stock_tools.match_product("保温杯有货吗？", _SEED_LIKE)
    assert hit is not None
    assert hit.name == "钛钢保温杯"


def test_match_product_exact_and_miss() -> None:
    assert stock_tools.match_product("瓶装水有货吗", _SEED_LIKE) is _SEED_LIKE[0]
    # 无关商品词：与两个名字都没有 ≥2 字公共子串
    assert stock_tools.match_product("小龙虾有货吗", _SEED_LIKE) is None
    # 1 字公共（「水」）不算命中——最小长度 2
    assert stock_tools.match_product("水还有吗", _SEED_LIKE) is None


def test_match_product_longest_then_smallest_id() -> None:
    a = _product(3, "保温杯")
    b = _product(4, "钛钢保温杯")
    # 平手取 id 小：问题对两者 LCS 同为 3 字「保温杯」
    tie = stock_tools.match_product("保温杯有货吗", [b, a])
    assert tie is not None and tie.id == 3
    # 多命中取最长：更长的连续公共子串优先，即使 id 更大
    c = _product(5, "保温杯套装礼盒")
    hit = stock_tools.match_product("保温杯套装有货吗", [a, c])
    assert hit is not None
    assert hit.id == 5
    # 空商品表
    assert stock_tools.match_product("保温杯有货吗", []) is None


# ---------- get_stock / query_stock 分支（fake db） ----------


class _FakeDb:
    """scalars 可注入返回值或异常；记录 rollback 次数。"""

    def __init__(
        self,
        scalars_result: Any = None,
        scalars_error: Exception | None = None,
    ) -> None:
        self._result = scalars_result
        self._error = scalars_error
        self.rollback_calls = 0

    def scalars(self, *_a: Any, **_k: Any) -> Any:
        if self._error is not None:
            raise self._error
        return iter(self._result or [])

    def rollback(self) -> None:
        self.rollback_calls += 1


def test_get_stock_found_with_value_and_none() -> None:
    cup = _product(2, "钛钢保温杯")
    cup.stock = 42
    assert stock_tools.get_stock(_FakeDb(), cup) == {  # type: ignore[arg-type]
        "found": True,
        "product_name": "钛钢保温杯",
        "stock": 42,
    }
    unset = _product(1, "瓶装水")
    assert stock_tools.get_stock(_FakeDb(), unset)["stock"] is None  # type: ignore[arg-type]


class _BoomProduct:
    id = 9
    name = "怪商品"

    @property
    def stock(self) -> int:  # 模拟 expire 后惰性加载炸（DB 不可用）
        raise SQLAlchemyError("db down")


def test_get_stock_db_error_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """P1#6 钉子：吞异常转 {error} 的对外行为不变，但捕获处必须 logger.exception
    （原文只进服务端日志，线上不再只剩「查询失败」）。对模块 logger 打桩断言
    调用形状——不用 caplog：带 DB 跑时 alembic fileConfig 会重置 root handler，
    跨模块采集顺序不稳定。"""
    db = _FakeDb()
    seen: list[tuple[Any, ...]] = []
    monkeypatch.setattr(stock_tools.logger, "exception", lambda *a: seen.append(a))

    result = stock_tools.get_stock(db, _BoomProduct())  # type: ignore[arg-type]
    assert result == {"error": True}
    # 异常后尽力回滚恢复会话可用（handoff 消息还要在同 session 落库）
    assert db.rollback_calls == 1
    assert any("库存工具读取商品库存失败" in str(a[0]) for a in seen)


def test_query_stock_hit_and_miss() -> None:
    cup = _product(2, "钛钢保温杯")
    cup.stock = 42
    db = _FakeDb(scalars_result=[_product(1, "瓶装水"), cup])
    assert stock_tools.query_stock(db, "保温杯有货吗？") == {  # type: ignore[arg-type]
        "found": True,
        "product_name": "钛钢保温杯",
        "stock": 42,
    }
    assert stock_tools.query_stock(_FakeDb(scalars_result=_SEED_LIKE), "小龙虾有货吗") == {  # type: ignore[arg-type]
        "found": False
    }


def test_query_stock_list_error_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb(scalars_error=SQLAlchemyError("db down"))
    seen: list[tuple[Any, ...]] = []
    monkeypatch.setattr(stock_tools.logger, "exception", lambda *a: seen.append(a))

    result = stock_tools.query_stock(db, "保温杯有货吗")  # type: ignore[arg-type]
    assert result == {"error": True}
    assert db.rollback_calls == 1
    assert any("库存工具列取商品失败" in str(a[0]) for a in seen)  # P1#6


# ---------- 摘要与五分支模板 ----------


def test_summarize_stock_result() -> None:
    assert (
        stock_tools.summarize_stock_result(
            {"found": True, "product_name": "钛钢保温杯", "stock": 42}
        )
        == "有货 · 42 件"
    )
    assert stock_tools.summarize_stock_result(
        {"found": True, "product_name": "瓶装水", "stock": 0}
    ) == ("暂时无货")
    assert (
        stock_tools.summarize_stock_result({"found": True, "product_name": "瓶装水", "stock": None})
        == "未设置"
    )
    assert stock_tools.summarize_stock_result({"found": False}) == "未找到商品"
    assert stock_tools.summarize_stock_result({"error": True}) == "查询失败"


def test_render_stock_answer_templates() -> None:
    assert (
        stock_tools.render_stock_answer({"found": True, "product_name": "钛钢保温杯", "stock": 42})
        == "钛钢保温杯有货，当前库存 42 件。"
    )
    # stock==0 是事实数据不是失败：正常回答分支
    assert stock_tools.render_stock_answer(
        {"found": True, "product_name": "瓶装水", "stock": 0}
    ) == ("瓶装水暂时无货。")


def test_render_stock_handoff_content() -> None:
    assert (
        stock_tools.render_stock_handoff_content(
            {"found": True, "product_name": "钛钢保温杯", "stock": None}
        )
        == "钛钢保温杯库存未设置，已转人工。"
    )
    assert (
        stock_tools.render_stock_handoff_content({"found": False}) == "没有找到对应商品，已转人工。"
    )
    assert stock_tools.render_stock_handoff_content({"error": True}) == "库存查询失败，已转人工。"


# ---------- run_ask 分派（MagicMock db，不碰真库） ----------


def _guards(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """检索/缺口/LLM 全部装哨兵：库存路径必须一个都不碰（0037/0018/0024）。"""
    calls = {"retrieve": 0, "gap": 0, "llm": 0}

    def _boom(name: str) -> Any:
        def _fn(*_a: Any, **_k: Any) -> Any:
            calls[name] += 1
            raise AssertionError(f"库存工具路径不允许触发 {name}（0037/0018/0024）")

        return _fn

    monkeypatch.setattr("suite_api.services.chat_engine.retrieve", _boom("retrieve"))
    monkeypatch.setattr("suite_api.services.chat_engine.assets_meta", _boom("retrieve"))
    monkeypatch.setattr("suite_api.services.chat_engine.record_refusal_gap", _boom("gap"))

    async def _no_llm(*_a: Any, **_k: Any) -> Any:
        calls["llm"] += 1
        raise AssertionError("库存工具路径不调 LLM（0037 v1 模板组装）")
        yield ""  # pragma: no cover

    monkeypatch.setattr("suite_api.services.chat_engine.llm.stream_chat", _no_llm)
    return calls


def _mock_db() -> MagicMock:
    db = MagicMock()
    db.refresh = MagicMock()
    return db


def _stub_stock(monkeypatch: pytest.MonkeyPatch, result: dict[str, Any]) -> None:
    monkeypatch.setattr("suite_api.services.chat_engine.query_stock", lambda *_a, **_k: result)


def test_run_ask_stock_in_stock_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _guards(monkeypatch)
    _stub_stock(monkeypatch, {"found": True, "product_name": "钛钢保温杯", "stock": 42})

    outcome = asyncio.run(run_ask(_mock_db(), MagicMock(id=1), "钛钢保温杯有货吗？"))

    assert calls == {"retrieve": 0, "gap": 0, "llm": 0}
    assert outcome.answer.kind == "answer"
    assert outcome.answer.handoff is False
    assert outcome.answer.citations == []
    assert outcome.agent_message.content == "钛钢保温杯有货，当前库存 42 件。"
    assert outcome.tool == {
        "name": "get_stock",
        "arg": "钛钢保温杯",
        "result": "有货 · 42 件",
    }
    assert outcome.gap is None
    assert outcome.generated is False
    assert outcome.fallback is False


def test_run_ask_stock_zero_is_factual_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _guards(monkeypatch)
    _stub_stock(monkeypatch, {"found": True, "product_name": "瓶装水", "stock": 0})

    outcome = asyncio.run(run_ask(_mock_db(), MagicMock(id=1), "瓶装水有货吗"))

    assert calls == {"retrieve": 0, "gap": 0, "llm": 0}
    assert outcome.answer.kind == "answer"  # 0 是事实数据不是失败
    assert outcome.agent_message.content == "瓶装水暂时无货。"
    assert outcome.tool["result"] == "暂时无货"


def test_run_ask_stock_null_handoff_without_gap(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _guards(monkeypatch)
    _stub_stock(monkeypatch, {"found": True, "product_name": "钛钢保温杯", "stock": None})

    outcome = asyncio.run(run_ask(_mock_db(), MagicMock(id=1), "钛钢保温杯有货吗？"))

    assert calls == {"retrieve": 0, "gap": 0, "llm": 0}
    assert outcome.answer.kind == "handoff"
    assert outcome.answer.handoff is True
    assert outcome.gap is None  # 工具路径不产生缺口（0024）
    assert outcome.agent_message.content == "钛钢保温杯库存未设置，已转人工。"
    assert outcome.tool["result"] == "未设置"


def test_run_ask_stock_word_hit_product_miss_falls_back_to_retrieval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """第 16 刀 P1#3 双前置：词表命中但 LCS 商品未命中 -> **回既有检索路径**
    （不是 handoff，不是工具）；归宿对齐词条——检索无证据走 refusal 落缺口。"""
    _stub_stock(monkeypatch, {"found": False})
    retrieve_calls: list[str] = []
    monkeypatch.setattr(
        "suite_api.services.chat_engine.retrieve",
        lambda _db, question, **_k: (retrieve_calls.append(question), [])[1],
    )
    monkeypatch.setattr("suite_api.services.chat_engine.assets_meta", lambda *_a, **_k: {})
    monkeypatch.setattr("suite_api.services.chat_engine.record_refusal_gap", lambda *_a, **_k: None)

    async def _no_llm(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("降级路径不该调 LLM")
        yield ""  # pragma: no cover

    monkeypatch.setattr("suite_api.services.chat_engine.llm.stream_chat", _no_llm)

    outcome = asyncio.run(run_ask(_mock_db(), MagicMock(id=1), "还有货吗"))

    assert retrieve_calls == ["还有货吗"]  # 词表命中但无商品匹配 -> 检索被调
    assert outcome.tool is None  # 工具零接触（不回工具）
    assert outcome.answer.kind == "refusal"  # 检索空 -> 既有拒答（0018，留缺口口径）


def test_run_ask_stock_error_handoff(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _guards(monkeypatch)
    _stub_stock(monkeypatch, {"error": True})

    outcome = asyncio.run(run_ask(_mock_db(), MagicMock(id=1), "瓶装水没货了吗？"))

    assert calls == {"retrieve": 0, "gap": 0, "llm": 0}
    assert outcome.answer.kind == "handoff"
    assert outcome.agent_message.content == "库存查询失败，已转人工。"
    assert outcome.tool["result"] == "查询失败"


def test_run_ask_narrowed_words_never_touch_stock_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """词表收窄误伤钉（P1#3）：「保温杯还剩多少毫升」（规格问句含「剩」+商品名）
    与「库存政策是什么」（通用词政策问）都必须零接触库存工具，走既有检索。"""
    monkeypatch.setattr(
        "suite_api.services.chat_engine.query_stock",
        lambda *_a, **_k: pytest.fail("收窄后的词不得调用库存工具"),
    )
    monkeypatch.setattr("suite_api.services.chat_engine.retrieve", lambda *_a, **_k: [])
    monkeypatch.setattr("suite_api.services.chat_engine.assets_meta", lambda *_a, **_k: {})
    # 第 27 刀：拒答消息文本要格式化缺口 id，本单测钉分派零接触不测文本——
    # mock 库把缺口钉 None（摘要/缺口段文本由 test_answer 单测+DB 集成钉）
    monkeypatch.setattr("suite_api.services.chat_engine.record_refusal_gap", lambda *_a, **_k: None)

    for question in ("保温杯还剩多少毫升？", "库存政策是什么"):
        outcome = asyncio.run(run_ask(_mock_db(), MagicMock(id=1), question))
        assert outcome.tool is None
        assert outcome.answer.kind == "refusal"  # 检索空 -> 既有拒答路径原样


def test_run_ask_order_takes_priority_over_stock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """分派序：同含订单号与库存词的问题走订单（1.5 在 1.6 之前）。"""
    _guards(monkeypatch)
    monkeypatch.setattr(
        "suite_api.services.chat_engine.query_stock",
        lambda *_a, **_k: pytest.fail("订单号命中时不得进库存分派"),
    )
    monkeypatch.setattr(
        "suite_api.services.chat_engine.get_order_status",
        lambda *_a, **_k: {
            "found": True,
            "order_no": "SO-1001",
            "status": "已发货",
            "items": [],
            "events": [],
        },
    )

    outcome = asyncio.run(run_ask(_mock_db(), MagicMock(id=1), "SO-1001 里那个保温杯有货吗？"))

    assert outcome.tool["name"] == "get_order_status"
    assert outcome.answer.kind == "answer"


def test_run_ask_non_stock_never_touches_stock_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """非库存非订单问题对库存工具零接触（不加 commit/查询，既有路径零漂移）。"""
    monkeypatch.setattr(
        "suite_api.services.chat_engine.query_stock",
        lambda *_a, **_k: pytest.fail("词表外问题不得调用库存工具"),
    )
    monkeypatch.setattr("suite_api.services.chat_engine.retrieve", lambda *_a, **_k: [])
    monkeypatch.setattr("suite_api.services.chat_engine.assets_meta", lambda *_a, **_k: {})
    # 第 27 刀：同上——钉分派零接触，缺口 mock 成 None 绕开摘要文本格式化
    monkeypatch.setattr("suite_api.services.chat_engine.record_refusal_gap", lambda *_a, **_k: None)

    outcome = asyncio.run(run_ask(_mock_db(), MagicMock(id=1), "保温杯的净含量是多少？"))

    assert outcome.tool is None
    assert outcome.answer.kind == "refusal"  # 空检索 -> 既有拒答路径原样


# ---------- SSE 事件序 ----------


def _stock_outcome() -> AskOutcome:
    message = ServiceMessage(
        session_id=1,
        role="agent",
        content="钛钢保温杯有货，当前库存 42 件。",
        citations=[],
        kind="answer",
        handoff=False,
        tool={"name": "get_stock", "arg": "钛钢保温杯", "result": "有货 · 42 件"},
    )
    message.id = 88

    return AskOutcome(
        agent_message=message,
        answer=ComposedAnswer(content=message.content, citations=[], kind="answer", handoff=False),
        gap=None,
        generated=False,
        fallback=False,
        tool=dict(message.tool),
    )


def test_sse_stream_stock_path_events() -> None:
    events = _parse(list(sse_event_stream(_stock_outcome())))
    kinds = [event for event, _ in events]
    assert kinds[0:2] == ["thinking", "tool"]
    assert kinds[-1] == "complete"
    assert events[0][1] == {"text": "查询库存中…"}  # 状态行按工具名诚实切换
    assert events[1][1] == {
        "name": "get_stock",
        "arg": "钛钢保温杯",
        "result": "有货 · 42 件",
    }
    complete = events[-1][1]
    assert complete["kind"] == "answer"
    assert complete["citations"] == []
    assert complete["tool"] == events[1][1]
    assert complete["gap_id"] is None
    assert "".join(d["text"] for e, d in events if e == "delta") == (
        "钛钢保温杯有货，当前库存 42 件。"
    )


def test_sse_stream_order_thinking_not_regressed() -> None:
    """既有订单路径 thinking 文案不因 get_stock 分支回归。"""
    message = ServiceMessage(
        session_id=1,
        role="agent",
        content="订单 SO-1001 当前状态：已发货。",
        citations=[],
        kind="answer",
        handoff=False,
        tool={"name": "get_order_status", "arg": "SO-1001", "result": "已发货 · 2 个物流事件"},
    )
    message.id = 77
    outcome = AskOutcome(
        agent_message=message,
        answer=ComposedAnswer(content=message.content, citations=[], kind="answer", handoff=False),
        gap=None,
        generated=False,
        fallback=False,
        tool=dict(message.tool),
    )
    events = _parse(list(sse_event_stream(outcome)))
    assert events[0][1] == {"text": "查询订单中…"}
