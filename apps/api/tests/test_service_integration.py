"""客服引用全链路集成测试（需真 PG，见 conftest 的 SUITE_TEST_DATABASE_URL）。

用户路径全程（任务锁定）：
登录 -> 上传保温杯规格文档 -> 确认 -> 发布（发布事务切块入索引）->
新会话问「净含量」-> SSE 流（thinking/delta/complete）引用 {asset_id, version:1} ->
问待人洗独有内容（退货政策，未发布）-> refusal+handoff 无引用（0004/0018）->
回流登记 -> kind=dialogue 资产待人洗 -> 发布 -> 再问命中引用该对话（闭环）->
版本跟随指针（开修订发布 v2 后命中 v2；回滚 v1 后引用回到 v1）。

知识缺口契约组（0024/0030，第 4 刀）：拒答落缺口（精确幂等）、answer 不落、
complete 带 gap_id、补文档登记关联 -> 发布事务内 resolved -> 同问法再问命中。
"""

import re
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sse_helpers import parse_sse_events

from suite_api.models import KnowledgeGap, RetrievalChunk, ServiceMessage

ApiFixture = tuple[TestClient, Path]

_CUP_DOC = "钛钢保温杯产品说明\n净含量：480ml\n材质牌号未标注，详见吊牌。".encode()
_RETURN_DOC = "保修与退货政策\n七天无理由退货；退货需保持吊牌完整。".encode()
_POINTER_DOC = "专用刻度杯说明\n刻度容量：300ml".encode()
# 「回放锚定」为本测试独有关键词，不与其他已发布块串台（同 module 库共享）
_DISCONNECT_DOC = "断连落库验证说明\n回放锚定：77ml".encode()
# 缺口组独有关键词（同 module 库共享：维修网点/会员积分/发票/赠品/延保互不串台，
# 也不与本文件此前已发布块串台）
_SERVICE_DESK_DOC = "售后维修网点说明\n维修网点：统一寄回工厂检修".encode()
_GIFT_DOC = "赠品口径说明\n赠品：下单随杯附送同款杯刷一支".encode()
_EXTENDED_WARRANTY_DOC = "延保口径说明\n延保：下单一年内可补购延长保修服务".encode()


def _login(client: TestClient) -> None:
    assert (
        client.post(
            "/api/auth/login", json={"username": "operator", "password": "operator123"}
        ).status_code
        == 200
    )


def _upload(
    client: TestClient,
    content: bytes,
    *,
    product_id: int | None = None,
    title: str | None = None,
    gap_id: int | None = None,
) -> Any:
    files = {"file": ("spec.txt", content, "text/plain")}
    data: dict[str, str] = {}
    if product_id is not None:
        data["productId"] = str(product_id)
    if title is not None:
        data["title"] = title
    if gap_id is not None:
        data["knowledgeGapId"] = str(gap_id)
    return client.post("/api/assets/register", files=files, data=data)


def _ask(client: TestClient, session_id: int, question: str) -> list[tuple[str, dict]]:
    with client.stream(
        "POST", f"/api/service/sessions/{session_id}/messages", json={"content": question}
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        raw = "".join(resp.iter_text())
    return parse_sse_events(raw)


# ---------- 鉴权：会话/消息/检索全部需登录 ----------


def test_service_endpoints_require_login(api: ApiFixture) -> None:
    client, _ = api
    client.cookies.clear()
    assert client.post("/api/service/sessions").status_code == 401
    assert client.get("/api/service/sessions").status_code == 401
    assert client.get("/api/service/sessions/1").status_code == 401
    assert (
        client.post("/api/service/sessions/1/messages", json={"content": "你好"}).status_code == 401
    )
    assert client.post("/api/service/sessions/1/register").status_code == 401
    assert client.get("/api/knowledge-gaps").status_code == 401  # 缺口列表同样要登录


# ---------- 全闭环 ----------


def test_service_citation_full_loop(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    cup_id = next(p["id"] for p in client.get("/api/products").json() if p["name"] == "钛钢保温杯")

    # 1) 上传保温杯文档 -> 补填材质 -> 确认净含量 -> 发布（发布事务切块入索引）
    resp = _upload(client, _CUP_DOC, product_id=cup_id, title="保温杯规格文档")
    assert resp.status_code == 201
    doc_asset = resp.json()
    doc_id = doc_asset["id"]
    patched = client.patch(
        f"/api/assets/{doc_id}/versions/1/fields", json={"材质": "钛钢", "净含量": "480ml"}
    )
    assert patched.status_code == 200
    published = client.post(f"/api/assets/{doc_id}/publish")
    assert published.status_code == 200

    # 2) 新会话 -> 问净含量 -> SSE 流（thinking -> delta* -> complete），引用 v1
    session_resp = client.post("/api/service/sessions")
    assert session_resp.status_code == 201
    session = session_resp.json()
    assert session["status"] == "active"
    sid = session["id"]

    events = _ask(client, sid, "保温杯的净含量是多少？")
    kinds = [event for event, _ in events]
    assert kinds[0] == "thinking"
    assert events[0][1]["text"] == "正在检索已发布资产…"
    assert kinds[-1] == "complete"
    deltas = [data["text"] for event, data in events if event == "delta"]
    assert deltas and all(deltas), "应有多个 delta 分片（~10-20 字/片）"
    complete = events[-1][1]
    assert complete["kind"] == "answer"
    assert complete["handoff"] is False
    assert complete["citations"] == [{"asset_id": doc_id, "version_no": 1}]
    streamed = "".join(deltas)
    assert "净含量" in streamed
    assert "480ml" in streamed
    assert "《保温杯规格文档》" in streamed

    # 详情：消息全量，agent 消息带 citations/kind；回答文本与 SSE 拼接一致
    detail = client.get(f"/api/service/sessions/{sid}").json()
    assert [m["role"] for m in detail["messages"]] == ["customer", "agent"]
    customer_msg, agent_msg = detail["messages"]
    assert customer_msg["citations"] is None and customer_msg["kind"] is None
    assert agent_msg["citations"] == [{"asset_id": doc_id, "version_no": 1}]
    assert agent_msg["kind"] == "answer"
    assert agent_msg["handoff"] is False
    assert agent_msg["content"] == streamed
    assert agent_msg["id"] == complete["message_id"]

    # 3) 问待人洗独有内容（0004：只有已发布进索引）-> refusal + handoff，无引用
    pending_resp = _upload(client, _RETURN_DOC, title="保修与退货政策")
    assert pending_resp.status_code == 201
    assert pending_resp.json()["status"] == "pending_review"  # 登记即待人洗，未发布
    refusal_events = _ask(client, sid, "退货政策是怎样的？")
    refusal_complete = refusal_events[-1][1]
    assert refusal_complete["kind"] == "refusal"
    assert refusal_complete["handoff"] is True
    assert refusal_complete["citations"] == []
    # 第 27 刀拒答交接摘要：原「全等固定文案」断言按新语义更新——操作者通道
    # 拒答消息 = 固定文案 + 问句摘要 + 缺口 G-xxxx（id 与 complete.gap_id 同源）
    refusal_deltas = "".join(d["text"] for e, d in refusal_events if e == "delta")
    gap_id = refusal_complete["gap_id"]
    assert isinstance(gap_id, int)
    assert refusal_deltas == (
        "抱歉，已发布资产里没有能回答这个问题的证据。\n"
        f"问句摘要：退货政策是怎样的？\n缺口：G-{gap_id:04d}"
    )

    # 4) 回流登记：转写落对象存储 -> dialogue 资产待人洗 -> 会话 registered
    register_resp = client.post(f"/api/service/sessions/{sid}/register")
    assert register_resp.status_code == 201
    dialogue_asset = register_resp.json()
    dialogue_id = dialogue_asset["id"]
    assert dialogue_asset["kind"] == "dialogue"
    assert dialogue_asset["status"] == "pending_review"  # 对话种类：无字段抽取直接待人洗
    assert dialogue_asset["versions"][0]["version_no"] == 1
    object_key = dialogue_asset["versions"][0]["object_key"]
    assert re.fullmatch(r"dialogue/[0-9a-f]{32}/[0-9a-f]{16}\.txt", object_key)
    assert dialogue_asset["title"] == "保温杯的净含量是多少？"  # 首问做标题

    closed = client.get(f"/api/service/sessions/{sid}").json()
    assert closed["status"] == "registered"
    assert closed["registered_asset_id"] == dialogue_id
    assert closed["closed_at"] is not None
    # 已登记会话不能再发问/再登记
    assert (
        client.post(f"/api/service/sessions/{sid}/messages", json={"content": "再问"}).status_code
        == 409
    )
    assert client.post(f"/api/service/sessions/{sid}/register").status_code == 409

    # 登记不是 0005 治理动作：审计不因回流新增 action 类型（publish/confirm/rollback）
    audit_actions = {row["action"] for row in client.get("/api/audit").json()}
    assert audit_actions <= {"publish", "confirm", "rollback"}

    # 5) 发布对话资产 -> 再问命中引用该对话（闭环；0021 同一引擎）
    dialogue_published = client.post(f"/api/assets/{dialogue_id}/publish")
    assert dialogue_published.status_code == 200

    loop_session = client.post("/api/service/sessions").json()
    loop_events = _ask(client, loop_session["id"], "客服为什么说抱歉？")
    loop_complete = loop_events[-1][1]
    assert loop_complete["kind"] == "answer"
    assert {"asset_id": dialogue_id, "version_no": 1} in loop_complete["citations"]
    loop_answer = "".join(d["text"] for e, d in loop_events if e == "delta")
    assert "根据已发布的客服对话记录" in loop_answer

    # 会话列表：倒序 + 状态 + 首问摘要 + 消息数
    listing = client.get("/api/service/sessions").json()
    assert listing[0]["id"] == loop_session["id"]
    registered_row = next(row for row in listing if row["id"] == sid)
    assert registered_row["status"] == "registered"
    assert registered_row["first_question"] == "保温杯的净含量是多少？"
    assert registered_row["message_count"] == 4  # 两问两答


def test_version_follows_published_pointer(api: ApiFixture) -> None:
    """版本跟随指针：开修订期间仍引 v1；发布 v2 后引 v2；回滚后回到 v1。

    「刻度容量」是本资产独有关键词，不与其他已发布块串台。开修订不得把
    status 打回 pending_review（地雷：检索还滤 status==published）。
    """
    client, _ = api
    _login(client)
    resp = _upload(client, _POINTER_DOC, title="指针跟随文档")
    asset_id = resp.json()["id"]
    assert client.post(f"/api/assets/{asset_id}/publish").status_code == 200

    sid = client.post("/api/service/sessions").json()["id"]
    v1_events = _ask(client, sid, "刻度容量是多少？")
    assert v1_events[-1][1]["citations"] == [{"asset_id": asset_id, "version_no": 1}]

    opened = client.post(f"/api/assets/{asset_id}/revisions")
    assert opened.status_code == 201
    assert opened.json()["status"] == "published"
    assert opened.json()["current_published_version_no"] == 1
    # 修订中线上仍服务 v1，不得拒答
    still_v1 = _ask(client, client.post("/api/service/sessions").json()["id"], "刻度容量是多少？")
    assert still_v1[-1][1]["kind"] == "answer"
    assert still_v1[-1][1]["citations"] == [{"asset_id": asset_id, "version_no": 1}]

    assert client.post(f"/api/assets/{asset_id}/publish").status_code == 200
    v2_events = _ask(client, client.post("/api/service/sessions").json()["id"], "刻度容量是多少？")
    complete = v2_events[-1][1]
    assert complete["kind"] == "answer"
    assert complete["citations"] == [{"asset_id": asset_id, "version_no": 2}]
    answer = "".join(d["text"] for e, d in v2_events if e == "delta")
    assert "300ml" in answer

    session_factory = client.app.state.session_factory
    with session_factory() as db:
        v1_chunks = db.scalars(
            select(RetrievalChunk).where(
                RetrievalChunk.asset_id == asset_id, RetrievalChunk.version_no == 1
            )
        ).all()
        assert v1_chunks, "v1 块仍在表里（旧引用可回放），只是不再命中"

    rolled = client.post(f"/api/assets/{asset_id}/rollback", json={"version_no": 1})
    assert rolled.status_code == 200
    assert rolled.json()["current_published_version_no"] == 1
    back = _ask(client, client.post("/api/service/sessions").json()["id"], "刻度容量是多少？")
    assert back[-1][1]["citations"] == [{"asset_id": asset_id, "version_no": 1}]


def test_sse_disconnect_still_persists_full_answer(api: ApiFixture) -> None:
    """断连=客户端停止订阅（ask 路由 docstring 锁定的取舍）：读首个 thinking
    事件后即中断迭代、关闭响应，customer 消息与 agent 消息（完整回答文本 +
    citations）仍都已落库——回答在流式开始前已完整组装入库，SSE 只是传输。"""
    client, _ = api
    _login(client)
    resp = _upload(client, _DISCONNECT_DOC, title="断连落库文档")
    assert resp.status_code == 201
    asset_id = resp.json()["id"]
    assert client.post(f"/api/assets/{asset_id}/publish").status_code == 200

    sid = client.post("/api/service/sessions").json()["id"]
    with client.stream(
        "POST", f"/api/service/sessions/{sid}/messages", json={"content": "回放锚定是多少？"}
    ) as stream:
        assert stream.status_code == 200
        first = next(stream.iter_text())
        assert "thinking" in first  # 首个事件已到即断：delta/complete 不再读
    # with 退出即关闭响应——对服务端即客户端断连

    session_factory = client.app.state.session_factory
    with session_factory() as db:
        messages = list(
            db.scalars(
                select(ServiceMessage)
                .where(ServiceMessage.session_id == sid)
                .order_by(ServiceMessage.id)
            )
        )
        assert [m.role for m in messages] == ["customer", "agent"]
        customer_msg, agent_msg = messages
        assert customer_msg.content == "回放锚定是多少？"
        assert customer_msg.citations is None and customer_msg.kind is None
        assert agent_msg.kind == "answer"
        assert agent_msg.handoff is False
        assert agent_msg.citations == [{"asset_id": asset_id, "version_no": 1}]
        assert "回放锚定为77ml" in agent_msg.content  # 完整回答文本已落库


# ---------- 输入校验 ----------


def test_service_input_validation(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    assert client.get("/api/service/sessions/999999").status_code == 404
    assert (
        client.post("/api/service/sessions/999999/messages", json={"content": "你好"}).status_code
        == 404
    )
    assert client.post("/api/service/sessions/999999/register").status_code == 404

    sid = client.post("/api/service/sessions").json()["id"]
    blank = client.post(f"/api/service/sessions/{sid}/messages", json={"content": "   "})
    assert blank.status_code == 422
    # 空会话回流：转写为空不能登记（0013 精神）
    assert client.post(f"/api/service/sessions/{sid}/register").status_code == 422


# ---------- 知识缺口契约组（0024/0030，第 4 刀） ----------


def test_refusal_creates_gap_and_answer_does_not(api: ApiFixture) -> None:
    """契约（0024）：拒答产生缺口（question=顾客原问）；answer 路径不产生。
    complete 事件：拒答带 gap_id，answer 恒为 null。本刀无工具——工具失败
    转人工不产生缺口的契约即「只挂 refusal 路径」，由 answer 对照钉死。"""
    client, _ = api
    _login(client)
    # answer 对照：自备已发布文档（不依赖此前用例的执行顺序）
    resp = _upload(client, _SERVICE_DESK_DOC, title="维修网点说明")
    assert resp.status_code == 201
    assert client.post(f"/api/assets/{resp.json()['id']}/publish").status_code == 200
    sid = client.post("/api/service/sessions").json()["id"]

    answer_events = _ask(client, sid, "维修网点在哪里？")
    answer_complete = answer_events[-1][1]
    assert answer_complete["kind"] == "answer"
    assert answer_complete["gap_id"] is None
    assert all(
        g["question"] != "维修网点在哪里？" for g in client.get("/api/knowledge-gaps").json()
    )

    # 拒答：缺口落库（question=原问、open、不挂商品），gap_id 与列表行一致
    refusal_events = _ask(client, sid, "会员积分怎么兑换？")
    refusal_complete = refusal_events[-1][1]
    assert refusal_complete["kind"] == "refusal"
    gap_id = refusal_complete["gap_id"]
    assert isinstance(gap_id, int)
    gaps = client.get("/api/knowledge-gaps").json()
    row = next(g for g in gaps if g["id"] == gap_id)
    assert row["question"] == "会员积分怎么兑换？"
    assert row["status"] == "open"
    assert row["product"] is None
    assert row["resolved_by_asset_id"] is None
    assert row["resolved_at"] is None
    # bad status 过滤参数 422
    assert client.get("/api/knowledge-gaps", params={"status": "todo"}).status_code == 422


def test_refusal_gap_exact_idempotency(api: ApiFixture) -> None:
    """精确幂等（ADR 0030 工程裁决）：同 question 文本再拒答不新建（库内 count
    不变、前后 gap_id 相同）；不同问法各建各的。"""
    client, _ = api
    _login(client)
    question = "发票可以开企业抬头吗"
    first = _ask(client, client.post("/api/service/sessions").json()["id"], question)
    gap_id = first[-1][1]["gap_id"]

    session_factory = client.app.state.session_factory
    with session_factory() as db:
        count_before = db.scalar(select(func.count()).select_from(KnowledgeGap))

    second = _ask(client, client.post("/api/service/sessions").json()["id"], question)
    assert second[-1][1]["gap_id"] == gap_id  # 复用同一缺口

    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(KnowledgeGap)) == count_before

    other = _ask(client, client.post("/api/service/sessions").json()["id"], "发票丢失了能补开吗")
    assert other[-1][1]["gap_id"] != gap_id  # 不同问法不合并（只做精确幂等）


def test_refusal_gap_question_masked_on_exit(api: ApiFixture) -> None:
    """第 26 刀缺口路（0038 修订口径）：gaps.question 出口掩——拒答仍按顾客
    原问落库（同 service_messages 落库豁免，不回写），治理台列表视图（出口）
    呈掩码；幂等复用走库内原文比对，不受出口掩影响。"""
    client, _ = api
    _login(client)
    question = "以旧换新补贴是打款到 13812345678 吗"
    sid = client.post("/api/service/sessions").json()["id"]
    events = _ask(client, sid, question)
    assert events[-1][1]["kind"] == "refusal"
    gap_id = events[-1][1]["gap_id"]

    row = next(g for g in client.get("/api/knowledge-gaps").json() if g["id"] == gap_id)
    assert "13812345678" not in row["question"]
    assert "1********78" in row["question"]

    # 落库原文不动（出口掩不回写行）
    session_factory = client.app.state.session_factory
    with session_factory() as db:
        assert db.get(KnowledgeGap, gap_id).question == question

    # 第 27 刀交接摘要的问句段同口径出口掩（先掩后截）——agent 消息文本呈
    # 掩码；customer 消息本身按既有豁免口径裸存（同问句回显不新增暴露面）。
    agent_msg = next(
        m
        for m in client.get(f"/api/service/sessions/{sid}").json()["messages"]
        if m["role"] == "agent"
    )
    assert "1********78" in agent_msg["content"]
    assert "13812345678" not in agent_msg["content"]


def test_refusal_handoff_summary_operator(api: ApiFixture) -> None:
    """第 27 刀拒答交接摘要（操作者通道钉）：消息文本 = 固定文案 + 问句摘要 +
    缺口 G-xxxx（4 位补零，与 complete.gap_id 同源）；SSE delta 拼接与落库
    文本同源；拒答判定/缺口语义不动，只升级文本。"""
    client, _ = api
    _login(client)
    sid = client.post("/api/service/sessions").json()["id"]
    events = _ask(client, sid, "登山绳可以定制长度吗")
    complete = events[-1][1]
    assert complete["kind"] == "refusal"
    assert complete["handoff"] is True
    gap_id = complete["gap_id"]
    assert isinstance(gap_id, int)
    expected = (
        "抱歉，已发布资产里没有能回答这个问题的证据。\n"
        f"问句摘要：登山绳可以定制长度吗\n缺口：G-{gap_id:04d}"
    )
    assert "".join(d["text"] for e, d in events if e == "delta") == expected
    messages = client.get(f"/api/service/sessions/{sid}").json()["messages"]
    agent_msg = next(m for m in messages if m["role"] == "agent")
    assert agent_msg["content"] == expected
    assert agent_msg["kind"] == "refusal" and agent_msg["handoff"] is True


def test_refusal_summary_truncates_long_question(api: ApiFixture) -> None:
    """第 27 刀截断口径钉：问句摘要 60 字 + 「…」（与会话列表首问摘要同口径）；
    缺口落库仍是原问全文（0024/0030 精确幂等语义不动）。"""
    client, _ = api
    _login(client)
    question = "定制帆布袋" * 20  # 100 字，本文件独有关键词
    events = _ask(client, client.post("/api/service/sessions").json()["id"], question)
    complete = events[-1][1]
    assert complete["kind"] == "refusal"
    gap_id = complete["gap_id"]
    content = "".join(d["text"] for e, d in events if e == "delta")
    assert content == (
        "抱歉，已发布资产里没有能回答这个问题的证据。\n"
        f"问句摘要：{question[:60]}…\n缺口：G-{gap_id:04d}"
    )
    session_factory = client.app.state.session_factory
    with session_factory() as db:
        assert db.get(KnowledgeGap, gap_id).question == question


def test_gap_fill_register_publish_resolves(api: ApiFixture) -> None:
    """补文档闭环（0024）：拒答 -> 缺口 open -> 带缺口登记（发布前仍 open 但已
    指向资产）-> 发布事务内 resolved + resolved_by_asset_id -> 同问法再问命中
    （飞轮闭环）；缺口不存在 422、已解决再关联 409。"""
    client, _ = api
    _login(client)
    # 缺口不存在 -> 422（字节不落库）
    assert _upload(client, _GIFT_DOC, title="赠品口径", gap_id=999999).status_code == 422

    refusal = _ask(client, client.post("/api/service/sessions").json()["id"], "下单有赠品吗")
    gap_id = refusal[-1][1]["gap_id"]

    # 补文档：带缺口登记（预填标题只是前端便利），来源=upload（端点定值）
    registered = _upload(client, _GIFT_DOC, title="补口径 · 下单有赠品吗", gap_id=gap_id)
    assert registered.status_code == 201
    asset_id = registered.json()["id"]
    assert registered.json()["source_kind"] == "upload"
    row = next(g for g in client.get("/api/knowledge-gaps").json() if g["id"] == gap_id)
    assert row["status"] == "open"  # 登记不解决：发布事务内才置 resolved
    assert row["resolved_by_asset_id"] == asset_id

    # 发布 -> 缺口 resolved + 指向资产 + resolved_at 落值；open 待办出列
    assert client.post(f"/api/assets/{asset_id}/publish").status_code == 200
    resolved = next(
        g
        for g in client.get("/api/knowledge-gaps", params={"status": "resolved"}).json()
        if g["id"] == gap_id
    )
    assert resolved["status"] == "resolved"
    assert resolved["resolved_by_asset_id"] == asset_id
    assert resolved["resolved_at"] is not None
    assert all(g["id"] != gap_id for g in client.get("/api/knowledge-gaps").json())

    # 已解决的缺口不能再关联（409）
    assert _upload(client, _GIFT_DOC, title="赠品口径二", gap_id=gap_id).status_code == 409

    # 同一问法再问：命中补上的文档（飞轮闭环；gap_id=null）
    again = _ask(client, client.post("/api/service/sessions").json()["id"], "下单有赠品吗")
    again_complete = again[-1][1]
    assert again_complete["kind"] == "answer"
    assert {"asset_id": asset_id, "version_no": 1} in again_complete["citations"]
    assert again_complete["gap_id"] is None


def test_open_gap_allows_only_one_pending_fill_doc(api: ApiFixture) -> None:
    """同一 open 缺口禁止二次登记（0024 补文档独占）：拒答 -> 带 gap 登记 ->
    再带同 gap 登记 409（且指向不被覆盖——否则首份发布时
    resolve_gaps_for_asset 按 resolved_by_asset_id 查不到，缺口永远 open）->
    发布首份 -> 缺口 resolved。"""
    client, _ = api
    _login(client)
    refusal = _ask(client, client.post("/api/service/sessions").json()["id"], "延保服务怎么开通")
    gap_id = refusal[-1][1]["gap_id"]
    assert refusal[-1][1]["kind"] == "refusal"

    first = _upload(
        client, _EXTENDED_WARRANTY_DOC, title="补口径 · 延保服务怎么开通", gap_id=gap_id
    )
    assert first.status_code == 201
    first_id = first.json()["id"]

    # 二次登记同缺口 -> 409，指向仍是首份（不被静默覆盖成第二份）
    second = _upload(client, _EXTENDED_WARRANTY_DOC, title="延保口径二", gap_id=gap_id)
    assert second.status_code == 409
    assert "A-" in second.json()["detail"]
    row = next(g for g in client.get("/api/knowledge-gaps").json() if g["id"] == gap_id)
    assert row["status"] == "open"
    assert row["resolved_by_asset_id"] == first_id

    # 发布首份 -> 缺口 resolved（指向未被覆盖，发布事务才查得到它）
    assert client.post(f"/api/assets/{first_id}/publish").status_code == 200
    resolved = next(
        g
        for g in client.get("/api/knowledge-gaps", params={"status": "resolved"}).json()
        if g["id"] == gap_id
    )
    assert resolved["status"] == "resolved"
    assert resolved["resolved_by_asset_id"] == first_id


def test_source_kind_set_by_endpoint_semantics(api: ApiFixture) -> None:
    """来源=登记端点语义定值（0025）：上传端点=upload、回流端点=session_backflow；
    列表/详情响应都带 source_kind。"""
    client, _ = api
    _login(client)
    up = _upload(client, "来源校验文档\n校验口径：仅上传入口".encode(), title="来源校验")
    assert up.status_code == 201
    assert up.json()["source_kind"] == "upload"
    assert client.get(f"/api/assets/{up.json()['id']}").json()["source_kind"] == "upload"

    sid = client.post("/api/service/sessions").json()["id"]
    _ask(client, sid, "清仓尾货什么时候上架")  # 内容无关紧要：回流登记需要会话有消息
    backflow = client.post(f"/api/service/sessions/{sid}/register")
    assert backflow.status_code == 201
    assert backflow.json()["source_kind"] == "session_backflow"

    sources = {a["id"]: a["source_kind"] for a in client.get("/api/assets").json()}
    assert sources[up.json()["id"]] == "upload"
    assert sources[backflow.json()["id"]] == "session_backflow"
