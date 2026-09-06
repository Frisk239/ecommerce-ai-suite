"""客服引用全链路集成测试（需真 PG，见 conftest 的 SUITE_TEST_DATABASE_URL）。

用户路径全程（任务锁定）：
登录 -> 上传保温杯规格文档 -> 确认 -> 发布（发布事务切块入索引）->
新会话问「净含量」-> SSE 流（thinking/delta/complete）引用 {asset_id, version:1} ->
问待人洗独有内容（退货政策，未发布）-> refusal+handoff 无引用（0004/0018）->
回流登记 -> kind=dialogue 资产待人洗 -> 发布 -> 再问命中引用该对话（闭环）->
版本跟随指针（发布 v2 后命中 v2 块而非 v1，0017 派生视图；修订流未做，
测试直接造修订发布的产物验证 join 语义）。
"""

import json
import re
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from suite_api.models import Asset, AssetVersion, RetrievalChunk

ApiFixture = tuple[TestClient, Path]

_CUP_DOC = "钛钢保温杯产品说明\n净含量：480ml\n材质牌号未标注，详见吊牌。".encode()
_RETURN_DOC = "保修与退货政策\n七天无理由退货；退货需保持吊牌完整。".encode()
_POINTER_DOC = "专用刻度杯说明\n刻度容量：300ml".encode()


def _login(client: TestClient) -> None:
    assert client.post("/api/auth/login", json={"username": "operator", "password": "operator123"}).status_code == 200


def _upload(client: TestClient, content: bytes, *, product_id: int | None = None, title: str | None = None) -> Any:
    files = {"file": ("spec.txt", content, "text/plain")}
    data: dict[str, str] = {}
    if product_id is not None:
        data["productId"] = str(product_id)
    if title is not None:
        data["title"] = title
    return client.post("/api/assets/register", files=files, data=data)


def _parse_events(raw: str) -> list[tuple[str, dict]]:
    """解析 SSE 文本为 [(event, data)]。"""
    events: list[tuple[str, dict]] = []
    for block in raw.strip().split("\n\n"):
        if not block:
            continue
        lines = block.splitlines()
        event = next(line.removeprefix("event: ") for line in lines if line.startswith("event: "))
        data = json.loads(next(line.removeprefix("data: ") for line in lines if line.startswith("data: ")))
        events.append((event, data))
    return events


def _ask(client: TestClient, session_id: int, question: str) -> list[tuple[str, dict]]:
    with client.stream(
        "POST", f"/api/service/sessions/{session_id}/messages", json={"content": question}
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        raw = "".join(resp.iter_text())
    return _parse_events(raw)


# ---------- 鉴权：会话/消息/检索全部需登录 ----------


def test_service_endpoints_require_login(api: ApiFixture) -> None:
    client, _ = api
    client.cookies.clear()
    assert client.post("/api/service/sessions").status_code == 401
    assert client.get("/api/service/sessions").status_code == 401
    assert client.get("/api/service/sessions/1").status_code == 401
    assert client.post("/api/service/sessions/1/messages", json={"content": "你好"}).status_code == 401
    assert client.post("/api/service/sessions/1/register").status_code == 401


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
    refusal_deltas = "".join(d["text"] for e, d in refusal_events if e == "delta")
    assert refusal_deltas == "抱歉，已发布资产里没有能回答这个问题的证据。"

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
    assert client.post(f"/api/service/sessions/{sid}/messages", json={"content": "再问"}).status_code == 409
    assert client.post(f"/api/service/sessions/{sid}/register").status_code == 409

    # 登记不是 0005 三类治理动作：审计不新增 action 类型
    audit_actions = {row["action"] for row in client.get("/api/audit").json()}
    assert audit_actions <= {"publish", "confirm"}

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
    """版本跟随指针：指针前移到 v2 后，检索命中 v2 块而非 v1（0017 派生视图）。

    修订流未做（后续刀），此处直接造出「修订已发布」的库内产物：v2 版本行 +
    v2 切块 + 指针前移，与发布事务产物同构，验证 retrieve 的 join 语义。
    「刻度容量」是本资产独有关键词，不与其他已发布块串台。
    """
    client, _ = api
    _login(client)
    resp = _upload(client, _POINTER_DOC, title="指针跟随文档")
    asset_id = resp.json()["id"]
    assert client.post(f"/api/assets/{asset_id}/publish").status_code == 200

    session_factory = client.app.state.session_factory
    with session_factory() as db:
        asset = db.get(Asset, asset_id)
        v1 = db.get(AssetVersion, asset.current_published_version_id)
        assert v1.version_no == 1
        v2 = AssetVersion(asset_id=asset_id, version_no=2, object_key=v1.object_key)
        db.add(v2)
        db.flush()
        db.add_all(
            [
                RetrievalChunk(asset_id=asset_id, version_no=2, seq=0, chunk="刻度容量：500ml"),
                RetrievalChunk(asset_id=asset_id, version_no=2, seq=1, chunk="材质：玻璃"),
            ]
        )
        asset.current_published_version_id = v2.id  # 模拟修订发布移动指针
        db.commit()

    sid = client.post("/api/service/sessions").json()["id"]
    events = _ask(client, sid, "刻度容量是多少？")
    complete = events[-1][1]
    assert complete["kind"] == "answer"
    assert complete["citations"] == [{"asset_id": asset_id, "version_no": 2}]
    answer = "".join(d["text"] for e, d in events if e == "delta")
    assert "500ml" in answer  # 命中 v2 块
    assert "300ml" not in answer  # v1 块随指针出榜，不漂移

    with session_factory() as db:
        v1_chunks = db.scalars(
            select(RetrievalChunk).where(
                RetrievalChunk.asset_id == asset_id, RetrievalChunk.version_no == 1
            )
        ).all()
        assert v1_chunks, "v1 块仍在表里（旧引用可回放），只是不再命中"


# ---------- 输入校验 ----------


def test_service_input_validation(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    assert client.get("/api/service/sessions/999999").status_code == 404
    assert client.post("/api/service/sessions/999999/messages", json={"content": "你好"}).status_code == 404
    assert client.post("/api/service/sessions/999999/register").status_code == 404

    sid = client.post("/api/service/sessions").json()["id"]
    blank = client.post(f"/api/service/sessions/{sid}/messages", json={"content": "   "})
    assert blank.status_code == 422
    # 空会话回流：转写为空不能登记（0013 精神）
    assert client.post(f"/api/service/sessions/{sid}/register").status_code == 422
