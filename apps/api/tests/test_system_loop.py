"""第 96 刀：体系闭环 e2e 剧本测试（真 PG；风格同 test_image_integration）。

一条测试走完治理飞轮**全环**（施工单 Must 3，演示手册第一/三/四幕的机器版）：

    问「XX 吗」（无证据）→ refusal + 缺口落库
    → 上传补口径文档（挂 knowledgeGapId，挂独立商品域）
    → 人洗确认必填（三态治理+必填闸）→ 发布（缺口事务内 resolved，审计/写回）
    → 同问法再问 → answer 带引用二元组
    → 会话回流登记 → dialogue 资产待人洗（机洗弃权=无 LLM 诚实面）
    → 人洗确认 qa_pairs → 发布（QA 块进索引）
    → 新问法命中（引用 dialogue 资产）

断言落在**库内状态**（缺口 status/资产指针/引用二元组/回流资产 kind/审计行/
商品写回），不只看 HTTP 回执。隔离纪律：module 独库（conftest）+ 独立商品域
（专用商品名与问法词汇均不与种子语料相交——种子只有保温杯/瓶装水/政策语料）。
"""

import os
from pathlib import Path

import psycopg
from fastapi.testclient import TestClient
from sse_helpers import parse_sse_events

ApiFixture = tuple[TestClient, Path]

_URL_ENV = "SUITE_TEST_DATABASE_URL"

# 独立商品域：专用商品名 + 专用问法词（换轴），与种子语料（保温杯/瓶装水/
# 退货政策/上班口径）零词汇交集——检索命中只可能来自本测试自己发布的资产。
_PRODUCT_NAME = "星轨演示键盘"
_Q1 = "星轨键盘能换轴吗"  # 首问：无证据 → refusal + 缺口
_DOC = (
    "品牌：星轨\n"
    "星轨键盘支持热插拔换轴，出厂附赠拔轴器。\n"
    "换轴服务收费 20 元。"
)
_Q2 = "星轨键盘换轴多少钱"  # 新问法：只与回流 QA 块全 bigram 相交
_QA = [{"q": _Q2, "a": "换轴服务收费 20 元。"}]


def _login(client: TestClient) -> None:
    assert (
        client.post(
            "/api/auth/login", json={"username": "operator", "password": "operator123"}
        ).status_code
        == 200
    )


def _fetch(sql: str, params: tuple = ()) -> list[tuple]:
    with psycopg.connect(os.environ[_URL_ENV]) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _ask_in(client: TestClient, session_id: int, question: str) -> dict:
    """在既有会话里问一句，返回 complete 事件载荷（引用由服务端定，0007）。"""
    with client.stream(
        "POST", f"/api/service/sessions/{session_id}/messages", json={"content": question}
    ) as resp:
        assert resp.status_code == 200, resp.text
        raw = "".join(resp.iter_text())
    events = parse_sse_events(raw)
    complete = [payload for event, payload in events if event == "complete"]
    assert complete, f"没有 complete 事件: {events}"
    return complete[0]


def test_governance_flywheel_full_loop(api: ApiFixture) -> None:
    client, _ = api
    _login(client)

    # ---- 0. 独立商品域（键盘类目：品牌 required——必填闸的素材） ----
    created = client.post(
        "/api/products", json={"name": _PRODUCT_NAME, "category": "键盘", "price_cents": 39900}
    )
    assert created.status_code == 201, created.text
    product_id = int(created.json()["id"])

    session_resp = client.post("/api/service/sessions")
    assert session_resp.status_code == 201, session_resp.text
    session_id = int(session_resp.json()["id"])

    # ---- 1. 无证据问句：refusal + 缺口落库（0018/0024） ----
    first = _ask_in(client, session_id, _Q1)
    assert first["kind"] == "refusal"
    gap_id = first["gap_id"]  # 操作者口径：complete 运行时带缺口 id（0030）
    assert isinstance(gap_id, int)
    [(g_status, g_question, g_session, g_asset)] = _fetch(
        "SELECT status, question, session_id, resolved_by_asset_id"
        " FROM knowledge_gaps WHERE id = %s",
        (gap_id,),
    )
    assert g_status == "open"
    assert g_question == _Q1  # question 列存原问（归一化只做查重键）
    assert g_session == session_id
    assert g_asset is None

    # ---- 2. 补口径文档：登记挂缺口（knowledgeGapId），缺口指向资产但仍 open ----
    registered = client.post(
        "/api/assets/register",
        files={"file": ("axis.txt", _DOC.encode(), "text/plain")},
        data={
            "productId": str(product_id),
            "title": "星轨键盘换轴服务口径",
            "knowledgeGapId": str(gap_id),
        },
    )
    assert registered.status_code == 201, registered.text
    doc_id = int(registered.json()["id"])
    assert registered.json()["kind"] == "document"
    assert registered.json()["status"] == "pending_review"  # 三态：待人洗
    [(g_status, g_asset)] = _fetch(
        "SELECT status, resolved_by_asset_id FROM knowledge_gaps WHERE id = %s", (gap_id,)
    )
    assert g_status == "open"  # 解决只随发布（0024）——登记不算数
    assert g_asset == doc_id

    # ---- 3. 必填闸：品牌 required，未确认直接发布 422；人洗确认后过闸 ----
    gated = client.post(f"/api/assets/{doc_id}/publish")
    assert gated.status_code == 422
    assert gated.json()["detail"]["code"] == "publish_gate_failed"
    confirmed = client.patch(
        f"/api/assets/{doc_id}/versions/1/fields", json={"品牌": "星轨"}
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["confirmed_fields"]["品牌"] == {
        "value": "星轨",
        "source": "human",
    }

    # ---- 4. 发布：缺口事务内 resolved + 指针 v1 + 审计行 + 商品写回 ----
    published = client.post(f"/api/assets/{doc_id}/publish")
    assert published.status_code == 200, published.text
    [(g_status, g_asset, g_at)] = _fetch(
        "SELECT status, resolved_by_asset_id, resolved_at FROM knowledge_gaps WHERE id = %s",
        (gap_id,),
    )
    assert g_status == "resolved"  # 发布事务内解决（回滚则保持 open）
    assert g_asset == doc_id and g_at is not None
    [(a_status, v_no)] = _fetch(
        "SELECT a.status, pv.version_no FROM assets a"
        " JOIN asset_versions pv ON pv.id = a.current_published_version_id WHERE a.id = %s",
        (doc_id,),
    )
    assert a_status == "published" and v_no == 1
    audit_rows = _fetch(
        "SELECT action FROM audit_log WHERE asset_id = %s ORDER BY id", (doc_id,)
    )
    assert [r[0] for r in audit_rows] == ["confirm", "publish"]  # 双审计行（0005）
    [(written, w_source)] = _fetch(
        "SELECT spec_values->'品牌'->>'value', spec_values->'品牌'->'source'->>'asset_id'"
        " FROM products WHERE id = %s",
        (product_id,),
    )
    assert written == "星轨" and int(w_source) == doc_id  # 写回带血缘（ADR 0034）

    # ---- 5. 同问法再问：answer 带引用二元组（缺口已 resolved，不留悬空） ----
    second = _ask_in(client, session_id, _Q1)
    assert second["kind"] == "answer", second
    assert {"asset_id": doc_id, "version_no": 1} in second["citations"]
    assert second["media_citations"] == []  # 文档证据无媒体姊妹键（94b 形态固定）
    [(m_citations, m_kind)] = _fetch(
        "SELECT citations::text, kind FROM service_messages WHERE id = %s",
        (second["message_id"],),
    )
    assert m_kind == "answer" and f'"asset_id": {doc_id}' in m_citations  # 随消息落库

    # ---- 6. 会话回流登记：dialogue 资产待人洗（机洗弃权=无 LLM 诚实面） ----
    backflow = client.post(f"/api/service/sessions/{session_id}/register")
    assert backflow.status_code == 201, backflow.text
    dialogue_id = int(backflow.json()["id"])
    [(s_status, s_asset)] = _fetch(
        "SELECT status, registered_asset_id FROM service_sessions WHERE id = %s",
        (session_id,),
    )
    assert s_status == "registered" and s_asset == dialogue_id
    [(d_kind, d_source, d_status, d_title)] = _fetch(
        "SELECT kind, source_kind, status, title FROM assets WHERE id = %s", (dialogue_id,)
    )
    assert d_kind == "dialogue" and d_source == "session_backflow"
    assert d_status == "pending_review"
    assert d_title == _Q1  # 标题=顾客首问摘要
    [(qa_entry,)] = _fetch(
        "SELECT extracted_fields->'qa_pairs' FROM asset_versions"
        " WHERE asset_id = %s AND version_no = 1",
        (dialogue_id,),
    )
    assert qa_entry.get("abstained") is True  # 无 LLM：弃权不写假值（0009）

    # ---- 7. 人洗 QA → 发布：「问：…/答：…」块进索引 ----
    qa_confirmed = client.patch(
        f"/api/assets/{dialogue_id}/versions/1/fields", json={"qa_pairs": _QA}
    )
    assert qa_confirmed.status_code == 200, qa_confirmed.text
    assert qa_confirmed.json()["confirmed_fields"]["qa_pairs"]["value"][0]["q"] == _Q2
    assert qa_confirmed.json()["confirmed_fields"]["qa_pairs"]["source"] == "human"
    assert client.post(f"/api/assets/{dialogue_id}/publish").status_code == 200
    chunks = [
        row[0]
        for row in _fetch(
            "SELECT chunk FROM retrieval_chunks WHERE asset_id = %s ORDER BY seq",
            (dialogue_id,),
        )
    ]
    assert any(f"问：{_Q2}" in chunk for chunk in chunks)

    # ---- 8. 新问法命中：引用指向回流资产（对话变知识的闭环收口） ----
    third_session = client.post("/api/service/sessions").json()
    third = _ask_in(client, int(third_session["id"]), _Q2)
    assert third["kind"] == "answer", third
    assert {"asset_id": dialogue_id, "version_no": 1} in third["citations"]
