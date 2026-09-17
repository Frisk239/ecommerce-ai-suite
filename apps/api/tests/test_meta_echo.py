"""第 108B 刀（W3）：会话元问题快路径（真 PG，LLM 空凭证不调外网）。

缺陷（108 刀审计）：`我的上一个问题是什么` / `我刚才问了啥` / `再说一遍` 这类
**关于对话本身**的问题被当知识问题——落检索 -> 无命中 -> 拒答+转人工+**污染
知识缺口池**（与 86 刀「不在售商品」同族病）。

修法：步 1 快路径分派（转人工 -> 订单号 -> 库存/报价回落 -> **元意图**）命中即
回声当前会话上一条顾客消息：kind=answer、零 LLM 零检索、**不落缺口、不建工单**；
首问边界回固定引导句；词表协调——带真实诉求的问句（库存词 + 再问一遍）不被截胡。

本文件钉子：正则正/反例、首问边界、多轮回声、不落缺口（无新行）+ 不建工单、
遗留元问题缺口「答上即关」、分派位置（库存优先）、SSE（thinking/tool/path）。
"""

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sse_helpers import parse_sse_events

from suite_api.models import HandoffTicket, KnowledgeGap, ServiceSession
from suite_api.services.chat_engine import (
    _META_INTENT_RE,
    META_ECHO_THINKING_TEXT,
    META_ECHO_TOOL_NAME,
    run_ask,
    sse_event_stream,
)
from suite_api.services.knowledge_gaps import normalize_question

ApiFixture = tuple[TestClient, Path]

_FIRST_BOUNDARY = "这是我们对话的第一条消息，您有什么想问的？"


def _login(client: TestClient) -> None:
    assert (
        client.post(
            "/api/auth/login", json={"username": "operator", "password": "operator123"}
        ).status_code
        == 200
    )


def _new_session(client: TestClient) -> ServiceSession:
    factory = client.app.state.session_factory
    with factory() as db:
        session = ServiceSession(status="active")
        db.add(session)
        db.commit()
        return session


def _ask(client: TestClient, session: ServiceSession, question: str) -> Any:
    factory = client.app.state.session_factory
    with factory() as db:
        return asyncio.run(run_ask(db, session, question))


# ---------- 纯函数：元意图词表（正例覆盖 Owner 口径，反例钉截胡面） ----------


@pytest.mark.parametrize(
    "question",
    [
        "我的上一个问题是什么",
        "我刚才问了什么",
        "我刚才问了啥",
        "我刚才问的是啥",
        "我刚才的问题是什么",
        "我上一条说了什么",
        "我的上一条消息是什么",
        "上面我问了什么",
        "我刚刚说了什么",
        "我的上一个问题",  # 裸名词收尾（锚定形态）
        "再说一遍",
        "你能再说一遍吗？",
        "重复一下",
        "复述一下",
    ],
)
def test_meta_intent_regex_hits(question: str) -> None:
    assert _META_INTENT_RE.search(question) is not None, question


@pytest.mark.parametrize(
    "question",
    [
        "你刚才说的退货政策是什么",  # 内容问（元问题 vs 内容问的分界）
        "我刚刚买的杯子什么时候发货",
        "上面那个杯子多少钱",
        "这个问题的答案是什么",
        "重复一下退货政策并说说保修",  # 混意图长句（收尾锚定把它挡在外面）
        "复述退货条款和保修期",
        "保温杯的净含量是多少",
    ],
)
def test_meta_intent_regex_rejects_content_questions(question: str) -> None:
    assert _META_INTENT_RE.search(question) is None, question


# ---------- 引擎路径：回声 / 首问边界 / 不落缺口 / 分派位置 ----------


def test_meta_echo_first_question_boundary(api: ApiFixture) -> None:
    """首问边界：会话里没有更早的顾客消息 -> 固定引导句（不回声、不拒答）。"""
    client, _ = api
    _login(client)
    session = _new_session(client)

    outcome = _ask(client, session, "我的上一个问题是什么")

    assert outcome.answer.kind == "answer"
    assert outcome.answer.handoff is False
    assert outcome.answer.citations == []
    assert outcome.agent_message.content == _FIRST_BOUNDARY
    assert outcome.agent_message.kind == "answer"
    assert outcome.tool is not None and outcome.tool["name"] == META_ECHO_TOOL_NAME
    assert outcome.gap is None  # 元问题不落缺口
    assert outcome.ticket is None  # 元问题不建工单
    _assert_no_gap_no_ticket(client, session.id, "我的上一个问题是什么")


def test_meta_echo_repeats_previous_question_across_turns(api: ApiFixture) -> None:
    """多轮：回声的是**当前会话上一条顾客消息**（不含本轮问句本身）。"""
    client, _ = api
    _login(client)
    session = _new_session(client)

    first = _ask(client, session, "我的订单 SO-1001 到哪了")  # 工具路径（零 LLM）
    assert first.answer.kind == "answer"
    outcome = _ask(client, session, "我刚才问了什么")

    assert outcome.answer.kind == "answer"
    assert outcome.agent_message.content == "您上一条问的是『我的订单 SO-1001 到哪了』。"
    assert outcome.tool is not None and outcome.tool["name"] == META_ECHO_TOOL_NAME
    assert outcome.gap is None and outcome.ticket is None
    # 「再说一遍」同出口（同一会话，仍回声上一条**顾客**消息而非元问本身）
    again = _ask(client, session, "再说一遍")
    assert again.agent_message.content == "您上一条问的是『我刚才问了什么』。"
    _assert_no_gap_no_ticket(client, session.id, "我刚才问了什么", "再说一遍")


def test_meta_echo_closes_legacy_meta_gap(api: ApiFixture) -> None:
    """W3 修前误落的同类元问题缺口，在真被答上时收掉（答上即关的泛化口径）。"""
    client, _ = api
    _login(client)
    session = _new_session(client)
    question = "我的上一个问题是什么"
    factory = client.app.state.session_factory
    with factory() as db:
        db.add(
            KnowledgeGap(
                question=question,
                normalized_question=normalize_question(question),
                status="open",
            )
        )
        db.commit()
        before = db.scalar(
            select(func.count()).select_from(KnowledgeGap).where(KnowledgeGap.status == "open")
        )

    outcome = _ask(client, session, question)
    assert outcome.agent_message.content == _FIRST_BOUNDARY

    with factory() as db:
        gap = db.scalar(
            select(KnowledgeGap).where(
                KnowledgeGap.normalized_question == normalize_question(question)
            )
        )
        assert gap is not None and gap.status == "resolved"
        after = db.scalar(
            select(func.count()).select_from(KnowledgeGap).where(KnowledgeGap.status == "open")
        )
        assert after == before - 1  # 只关同问那条，不新增任何行


def test_stock_fast_path_unchanged_by_meta_dispatch(api: ApiFixture) -> None:
    """分派位置回归钉：词表命中且商品匹配的库存问句照旧走工具（元意图插在库存
    之后，没有截胡库存面；工具条/文案与 W3 修前逐字一致）。"""
    client, _ = api
    _login(client)
    session = _new_session(client)

    outcome = _ask(client, session, "钛钢保温杯有货吗")

    assert outcome.tool is not None
    assert outcome.tool["name"] == "get_stock"
    assert "42" in outcome.agent_message.content


def test_repeat_request_with_stock_words_is_echo_not_gap(api: ApiFixture) -> None:
    """「库存多少再说一遍」的归宿（W3 主诉）：不落缺口。

    库存路由词表只留「到货状态」四类直陈词（0037 修订去掉了「库存」）——这个
    说法库存工具本来就接不住；W3 修前它落检索 -> 无命中 -> 拒答+转人工+**污染
    缺口池**。修后由元意图收尾锚定分支回声上一条顾客消息：kind=answer、零缺口、
    零工单（比拒答更诚实——顾客要的确实是「上一句」）。
    """
    client, _ = api
    _login(client)
    session = _new_session(client)
    _ask(client, session, "钛钢保温杯有货吗")

    outcome = _ask(client, session, "库存多少再说一遍")

    assert outcome.tool is not None and outcome.tool["name"] == META_ECHO_TOOL_NAME
    assert outcome.answer.kind == "answer" and outcome.answer.handoff is False
    assert outcome.agent_message.content == "您上一条问的是『钛钢保温杯有货吗』。"
    _assert_no_gap_no_ticket(client, session.id, "库存多少再说一遍")


def test_meta_echo_sse_shape(api: ApiFixture) -> None:
    """SSE：状态行「正在回看会话…」（没发生检索，不装作查过）+ tool 条 + path=tool。"""
    client, _ = api
    _login(client)
    session = _new_session(client)
    _ask(client, session, "你们卖什么")

    outcome = _ask(client, session, "我刚才问了什么")
    events = parse_sse_events("".join(sse_event_stream(outcome, expose_gap_id=False)))

    assert events[0] == ("thinking", {"text": META_ECHO_THINKING_TEXT})
    assert events[1][0] == "tool" and events[1][1]["name"] == META_ECHO_TOOL_NAME
    complete = events[-1][1]
    assert complete["kind"] == "answer" and complete["handoff"] is False
    assert complete["path"] == "tool"
    assert complete["citations"] == []
    assert all(event != "thinking" or data["text"] != "正在检索已发布资产…" for event, data in events)
    assert "".join(data["text"] for event, data in events if event == "delta") == (
        "您上一条问的是『你们卖什么』。"
    )


def _assert_no_gap_no_ticket(client: TestClient, session_id: int, *questions: str) -> None:
    """不落缺口 + 不建工单（W3 验收的硬断言）：那些问句在缺口池里零出现。"""
    factory = client.app.state.session_factory
    with factory() as db:
        for question in questions:
            gap = db.scalar(
                select(KnowledgeGap).where(
                    KnowledgeGap.normalized_question == normalize_question(question)
                )
            )
            assert gap is None, f"元问题不应落缺口: {question}"
        tickets = db.scalar(
            select(func.count())
            .select_from(HandoffTicket)
            .where(HandoffTicket.session_id == session_id)
        )
        assert tickets == 0
