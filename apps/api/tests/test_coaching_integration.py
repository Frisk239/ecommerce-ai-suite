"""销售考核集成测试（真 PG；complete_chat 替身不发外网；第 19 刀/ADR 0040）。

契约：
- 发布带 confirmed qa_pairs 的 dialogue 资产 -> 题库逐对成题（source=qa、
  standard_answer 下发、题源锚 A-xxxx·v1）；作答 patch 打分 JSON -> 记录含
  三维分 + model_name 快照 + operator_name，records 倒序；已评分重评 409、
  重评不存在 404、坏题锚 404；
- qa 弃权/确认空 -> 转写首问兜底题（source=transcript、standard_answer=None）；
- 空 key（无替身）-> attempt 落未评分行（HTTP 200 + unscored + last_error），
  配好替身后 rescore 成功转 scored（last_error 清空）；
- GET /api/assets?kind=dialogue 过滤与 status 并存；四端点未登录 401；
- 事务边界钉测（debt-1 评审处置）：score_attempt / rescore_record 调
  complete_chat 的时刻真 session.in_transaction() 恒为 False——LLM ≤20s
  等待不 idle-in-transaction（先例 test_registration 的 _TxnRecordingDb 断言，
  此处服务需要真查询，改用真 session 直接钉）。

资产全程在空 key 环境登记（机洗 QA 降级弃权，不触网），confirmed qa_pairs
由人洗 PATCH 直接给定（纯函数校验，不调 LLM），故造数据无需替身。
"""

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sse_helpers import parse_sse_events

from suite_api.services import llm as llm_module
from suite_api.services.coaching import rescore_record, score_attempt
from suite_api.settings import get_settings

ApiFixture = tuple[TestClient, Path]

GOOD_SCORE_JSON = '{"accurate": 36, "evidence": 25, "tone": 28, "comment": "口径准，语气亲切"}'


def _login(client: TestClient) -> None:
    assert (
        client.post(
            "/api/auth/login", json={"username": "operator", "password": "operator123"}
        ).status_code
        == 200
    )


def _ask(client: TestClient, session_id: int, question: str) -> None:
    with client.stream(
        "POST", f"/api/service/sessions/{session_id}/messages", json={"content": question}
    ) as resp:
        assert resp.status_code == 200
        parse_sse_events("".join(resp.iter_text()))


def _publish_dialogue(client: TestClient, question: str, confirmed: list[dict[str, str]]) -> int:
    """登记（空 key 机洗弃权）-> 人洗确认给定 qa_pairs -> 发布，返回资产 id。

    confirmed=[] 表示人洗确认「没有 QA」，题库将走转写首问兜底。"""
    sid = client.post("/api/service/sessions").json()["id"]
    _ask(client, sid, question)
    reg = client.post(f"/api/service/sessions/{sid}/register")
    assert reg.status_code == 201
    asset_id = reg.json()["id"]
    assert (
        client.patch(
            f"/api/assets/{asset_id}/versions/1/fields", json={"qa_pairs": confirmed}
        ).status_code
        == 200
    )
    assert client.post(f"/api/assets/{asset_id}/publish").status_code == 200
    return asset_id


def _patch_complete_chat(
    monkeypatch: pytest.MonkeyPatch,
    result: str | None = None,
    error: Exception | None = None,
) -> list[dict[str, str]]:
    calls: list[dict[str, str]] = []

    async def fake(system_prompt: str, user_prompt: str) -> Any:
        calls.append({"system": system_prompt, "user": user_prompt})
        if error is not None:
            raise error
        assert result is not None
        return result

    monkeypatch.setattr(llm_module, "complete_chat", fake)
    return calls


def _qa_question(client: TestClient, asset_id: int) -> dict[str, Any]:
    """从题库取该资产展开出的首道 qa 题（按锚 asset_id 定位）。"""
    questions = client.get("/api/coach/questions").json()
    return next(
        q for q in questions if q["key"]["asset_id"] == asset_id and q["key"]["source"] == "qa"
    )


# ---------- 全链路：发布 QA 对 -> 题库 -> 作答打分 -> 回放/重评闸门 ----------


def test_question_bank_and_scored_attempt(api: ApiFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = api
    _login(client)
    asset_id = _publish_dialogue(
        client,
        "盲盒可以指定款式吗",
        [{"q": "盲盒可以指定款式吗", "a": "盲盒随机发货，不能指定"}],
    )

    q = _qa_question(client, asset_id)
    assert q["question"] == "盲盒可以指定款式吗"
    assert q["standard_answer"] == "盲盒随机发货，不能指定"  # v1 下发参照（单操作者兼考官）
    assert q["key"] == {"asset_id": asset_id, "version_no": 1, "source": "qa", "pair_index": 0}

    calls = _patch_complete_chat(monkeypatch, result=GOOD_SCORE_JSON)
    created = client.post(
        "/api/coach/attempts",
        json={"question_key": q["key"], "answer": "亲，盲盒是随机发货，暂不支持指定款式哦"},
    )
    assert created.status_code == 200
    rec = created.json()
    assert rec["status"] == "scored"
    assert rec["score"] == {
        "accurate": 36,
        "evidence": 25,
        "tone": 28,
        "comment": "口径准，语气亲切",
    }
    assert rec["model_name"] == get_settings().llm_model  # 打分时刻底座名快照
    assert rec["operator_name"] == "operator"
    assert rec["question_text"] == "盲盒可以指定款式吗"
    assert rec["trainee_answer"] == "亲，盲盒是随机发货，暂不支持指定款式哦"

    # rubric 三要素 + 三输入进 prompt
    assert len(calls) == 1
    assert "口径准确 40" in calls[0]["system"]
    assert "盲盒随机发货，不能指定" in calls[0]["user"]
    assert "盲盒是随机发货" in calls[0]["user"]

    # 记录倒序：新记录在首位
    records = client.get("/api/coach/records").json()
    assert records[0]["id"] == rec["id"]

    # 已评分不可重评（分数不可覆盖）；不存在的记录 404；坏题锚 404
    assert client.post(f"/api/coach/records/{rec['id']}/rescore").status_code == 409
    assert client.post("/api/coach/records/999999/rescore").status_code == 404
    assert (
        client.post(
            "/api/coach/attempts",
            json={
                "question_key": {
                    "asset_id": 999999,
                    "version_no": 1,
                    "source": "qa",
                    "pair_index": 0,
                },
                "answer": "答",
            },
        ).status_code
        == 404
    )
    # 空答案 422
    assert (
        client.post(
            "/api/coach/attempts", json={"question_key": q["key"], "answer": "   "}
        ).status_code
        == 422
    )


# ---------- 兜底：confirmed 空/弃权 -> 转写首问题 ----------


def test_transcript_fallback_question(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    asset_id = _publish_dialogue(client, "能否顺丰发货呢", [])  # 人洗确认「没有 QA」

    questions = client.get("/api/coach/questions").json()
    q = next(x for x in questions if x["key"]["asset_id"] == asset_id)
    assert q["key"]["source"] == "transcript"
    assert q["key"]["pair_index"] is None
    assert q["question"] == "能否顺丰发货呢"  # 转写首个「顾客：」行
    assert q["standard_answer"] is None


# ---------- 空 key：未评分行可重评 ----------


def test_unscored_when_no_key_then_rescore(
    api: ApiFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = api
    _login(client)
    asset_id = _publish_dialogue(
        client, "支持退换货吗", [{"q": "支持退换货吗", "a": "签收后 7 天无理由退换"}]
    )
    q = _qa_question(client, asset_id)

    # 不 patch：空凭证进程 complete_chat 抛 LLMNotConfigured -> 未评分行，不抛 500
    att = client.post(
        "/api/coach/attempts", json={"question_key": q["key"], "answer": "支持 7 天无理由"}
    )
    assert att.status_code == 200
    rec = att.json()
    assert rec["status"] == "unscored"
    assert rec["score"] is None
    assert rec["model_name"] is None
    assert "未配置 LLM_API_KEY" in rec["last_error"]

    # 配好底座 -> 重评成功、原因清空、落底座名快照
    _patch_complete_chat(monkeypatch, result=GOOD_SCORE_JSON)
    rescored = client.post(f"/api/coach/records/{rec['id']}/rescore")
    assert rescored.status_code == 200
    r2 = rescored.json()
    assert r2["status"] == "scored"
    assert r2["score"]["accurate"] == 36
    assert r2["last_error"] is None
    assert r2["model_name"] == get_settings().llm_model


# ---------- assets kind 过滤 + 考核四端点鉴权 401 ----------


def test_assets_kind_filter_and_auth(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    asset_id = _publish_dialogue(
        client, "发票怎么开", [{"q": "发票怎么开", "a": "订单页申请电子发票"}]
    )

    dialogues = client.get("/api/assets?kind=dialogue&status=published").json()
    assert dialogues and all(a["kind"] == "dialogue" for a in dialogues)
    assert any(a["id"] == asset_id for a in dialogues)
    documents = client.get("/api/assets?kind=document").json()
    assert all(a["kind"] == "document" for a in documents)
    # 未知 kind 不报错，自然空列表
    assert client.get("/api/assets?kind=nope").json() == []

    client.cookies.clear()
    assert client.get("/api/coach/questions").status_code == 401
    assert (
        client.post("/api/coach/attempts", json={"question_key": {}, "answer": "x"}).status_code
        == 401
    )
    assert client.get("/api/coach/records").status_code == 401
    assert client.post("/api/coach/records/1/rescore").status_code == 401
    _login(client)


# ---------- 单资产推导（debt-2）：行为等价 + prompt 逐字节 + 不走全量 ----------


def test_find_question_single_asset_equivalence_and_prompt_unchanged(
    api: ApiFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """钉三件事：

    1. 行为等价：find_question 每题结果与全量 derive_questions 里同 key 题
       dict 逐字段相等（题面/参照字符串不动，兜底与 qa 两路都覆盖）；
    2. 不走全量：derive_questions 换成必炸桩后，find_question 与打分链路
       （经它找题）仍通——定位只按锚的 asset_id 推导单资产；
    3. prompt 逐字节：同 key 同答案，打分 user prompt 与 build_score_prompt
       对题库同一条目的输出完全相等（推导改造不动 prompt 漏斗）。
    另钉坏锚口径不变：pair_index 越界 404、已发布非 dialogue 资产 404。
    """
    client, _ = api
    _login(client)
    a1 = _publish_dialogue(
        client, "盲盒可以指定款式吗", [{"q": "盲盒可以指定款式吗", "a": "盲盒随机发货，不能指定"}]
    )
    a2 = _publish_dialogue(client, "能否顺丰发货呢", [])  # 兜底 transcript 题也进对照集
    doc = client.post(
        "/api/assets/register", files={"file": ("spec.txt", "材质：钛钢".encode(), "text/plain")}
    ).json()
    assert client.post(f"/api/assets/{doc['id']}/publish").status_code == 200

    from fastapi import HTTPException

    from suite_api.deps import ensure_storage
    from suite_api.services import coaching as coaching_module

    session = client.app.state.session_factory()
    storage = ensure_storage(client.app)
    target: dict[str, Any] = {}
    try:
        bank = coaching_module.derive_questions(session, storage)
        assert {a1, a2} <= {q["key"]["asset_id"] for q in bank}
        for q in bank:
            assert coaching_module.find_question(session, storage, q["key"]) == q
        target = next(q for q in bank if q["key"]["asset_id"] == a1 and q["key"]["source"] == "qa")
        assert any(q["key"]["asset_id"] == a2 and q["key"]["source"] == "transcript" for q in bank)

        with pytest.raises(HTTPException) as ei:
            coaching_module.find_question(session, storage, {**target["key"], "pair_index": 999})
        assert ei.value.status_code == 404
        with pytest.raises(HTTPException) as ei:
            coaching_module.find_question(
                session, storage, {"asset_id": doc["id"], "version_no": 1, "source": "transcript"}
            )
        assert ei.value.status_code == 404  # 非 dialogue 不参与题库（口径同全量时代）

        def _boom(*args: object, **kwargs: object) -> list:
            raise AssertionError("找题路径不得再走全量 derive_questions")

        monkeypatch.setattr(coaching_module, "derive_questions", _boom)
        assert coaching_module.find_question(session, storage, target["key"]) == target
    finally:
        session.close()

    # 作答链路经 find_question 找题：桩仍在位（derive 全量禁走），打分照旧 200
    answer = "亲，盲盒是随机发货，暂不支持指定款式哦"
    calls = _patch_complete_chat(monkeypatch, result=GOOD_SCORE_JSON)
    att = client.post("/api/coach/attempts", json={"question_key": target["key"], "answer": answer})
    assert att.status_code == 200
    assert att.json()["status"] == "scored"
    assert len(calls) == 1
    assert calls[0]["user"] == coaching_module.build_score_prompt(
        target["question"], target["standard_answer"], answer
    )


# ---------- 事务边界钉测：LLM 等待不 idle-in-transaction（debt-1，评审处置） ----------


def test_score_attempt_llm_call_not_in_transaction(
    api: ApiFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """直接喂真 session 调 score_attempt：complete_chat 时刻 in_transaction 恒 False。

    find_question 的 SELECT 会 autobegin——服务必须先 commit 结束该事务再等 LLM
    （先例 register_asset 机洗前 commit / material running 先落库）。
    """
    client, _ = api
    _login(client)
    asset_id = _publish_dialogue(
        client, "会员积分能抵现吗", [{"q": "会员积分能抵现吗", "a": "积分暂不支持抵现"}]
    )
    q = _qa_question(client, asset_id)

    from suite_api.deps import ensure_storage

    session_factory = client.app.state.session_factory
    seen: list[bool] = []

    async def fake(_system: str, _user: str) -> str:
        # 由服务在同一 session 上 commit 之后调用；此刻不得持事务
        seen.append(session.in_transaction())
        return GOOD_SCORE_JSON

    monkeypatch.setattr(llm_module, "complete_chat", fake)
    session = session_factory()
    try:
        record = score_attempt(
            session, ensure_storage(client.app), "operator", q["key"], "积分暂不支持抵现"
        )
    finally:
        session.close()
    assert seen and all(flag is False for flag in seen), (
        "complete_chat 时刻不得 idle-in-transaction"
    )
    assert record.id is not None  # 未评分 INSERT 先落库，打分后同 session UPDATE 收口
    assert record.score is not None and record.model_name == get_settings().llm_model


def test_rescore_record_llm_call_not_in_transaction(
    api: ApiFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """未评分记录走 HTTP 产生，再直接喂真 session 调 rescore_record：同钉 in_transaction False。"""
    client, _ = api
    _login(client)
    asset_id = _publish_dialogue(
        client, "礼盒能装两瓶吗", [{"q": "礼盒能装两瓶吗", "a": "标准礼盒装两瓶"}]
    )
    q = _qa_question(client, asset_id)
    # 不 patch：空凭证 -> 未评分行（score NULL，可重评）
    rec = client.post(
        "/api/coach/attempts", json={"question_key": q["key"], "answer": "能装两瓶"}
    ).json()
    assert rec["status"] == "unscored"

    session_factory = client.app.state.session_factory
    seen: list[bool] = []

    async def fake(_system: str, _user: str) -> str:
        seen.append(session.in_transaction())
        return GOOD_SCORE_JSON

    monkeypatch.setattr(llm_module, "complete_chat", fake)
    session = session_factory()
    try:
        record = rescore_record(session, rec["id"])
    finally:
        session.close()
    assert seen and all(flag is False for flag in seen), (
        "rescore complete_chat 时刻不得 idle-in-transaction"
    )
    assert record.score is not None and record.last_error is None
