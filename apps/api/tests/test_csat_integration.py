"""第 48 刀 CSAT + 反馈闭环集成测试（真 PG，见 conftest 的 SUITE_TEST_DATABASE_URL）。

覆盖：
- 会话评分：无令牌 401 / 分值越界 422 / 留言超长 422 / 非 active 409 / 一会话
  一评 409 / 成功落库（comment 库内原文）/ 低分不写任何东西。
- thumbs-up（40 刀 422 的反向）：200、`triaged_asset_ids` 空、资产
  `last_verified_at` 不变；thumbs-down 仍分诊（回归）；重复反馈 409。
- 打回理由：带 reason -> `人工打回：…`；不带 -> `人工打回`；超 200 字 422。
- 仪表 CSAT：空态 None / 分布恒含五档 / 评两条后均分与分布正确 / 留言**出口
  已掩**（库内仍是原文）/ 最近 3 条上限。
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from prometheus_client import REGISTRY
from sse_helpers import parse_sse_events

from suite_api.models import ServiceSession, SessionRating

ApiFixture = tuple[TestClient, Path]

_DOC = "保修政策说明\n钛钢保温杯自购买之日起保修一年，非人为损坏免费换新。".encode()


def _login(client: TestClient) -> None:
    assert (
        client.post(
            "/api/auth/login", json={"username": "operator", "password": "operator123"}
        ).status_code
        == 200
    )


def _publish(client: TestClient, title: str) -> int:
    _login(client)  # 登记/发布是操作者动作（顾客只发问/评分）
    existing = [a for a in client.get("/api/assets").json() if a.get("title") == title]
    if existing:
        return existing[0]["id"]
    resp = client.post(
        "/api/assets/register",
        files={"file": ("warranty.txt", _DOC, "text/plain")},
        data={"title": title},
    )
    assert resp.status_code == 201
    asset_id: int = resp.json()["id"]
    assert client.post(f"/api/assets/{asset_id}/publish").status_code == 200
    return asset_id


def _customer_session(client: TestClient, token: str) -> int:
    """直落一条顾客会话（绕过建会话 IP 闸；第 45 刀：过期时刻必须写）。"""
    factory = client.app.state.session_factory
    with factory() as db:
        session = ServiceSession(
            status="active",
            customer_token=token,
            customer_token_expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        db.add(session)
        db.commit()
        return session.id


def _rate(
    client: TestClient, session_id: int, token: str, score: int, comment: str | None = None
):
    body: dict[str, Any] = {"score": score}
    if comment is not None:
        body["comment"] = comment
    return client.post(
        f"/api/customer/sessions/{session_id}/rating",
        json=body,
        headers={"Authorization": f"Bearer {token}"},
    )


def _answer_with_citation(client: TestClient, session_id: int, token: str) -> dict[str, Any]:
    """问一句能有引用回答的问题，返回 complete 载荷（含 message_id）。"""
    _publish(client, "保修政策说明")
    with client.stream(
        "POST",
        f"/api/customer/sessions/{session_id}/messages",
        json={"content": "钛钢保温杯保修多久"},
        headers={"Authorization": f"Bearer {token}"},
    ) as resp:
        assert resp.status_code == 200
        raw = "".join(resp.iter_text())
    events = parse_sse_events(raw)
    complete = next(data for event, data in events if event == "complete")
    assert complete["citations"], "本用例需要一条带引用的回答"
    return complete


# ---------- 评分：闸门 ----------


def test_rating_requires_token(api: ApiFixture) -> None:
    client, _ = api
    session_id = _customer_session(client, "csat-token-auth")
    assert client.post(f"/api/customer/sessions/{session_id}/rating", json={"score": 5}).status_code == 401


def test_rating_rejects_score_out_of_range(api: ApiFixture) -> None:
    client, _ = api
    session_id = _customer_session(client, "csat-token-range")
    assert _rate(client, session_id, "csat-token-range", 0).status_code == 422
    assert _rate(client, session_id, "csat-token-range", 6).status_code == 422


def test_rating_rejects_overlong_comment(api: ApiFixture) -> None:
    client, _ = api
    session_id = _customer_session(client, "csat-token-long")
    resp = _rate(client, session_id, "csat-token-long", 4, "好" * 501)
    assert resp.status_code == 422
    assert "留言" in resp.json()["detail"]


def test_rating_requires_active_session(api: ApiFixture) -> None:
    client, _ = api
    session_id = _customer_session(client, "csat-token-closed")
    factory = client.app.state.session_factory
    with factory() as db:
        session = db.get(ServiceSession, session_id)
        session.status = "registered"
        db.commit()
    assert _rate(client, session_id, "csat-token-closed", 5).status_code == 409


def test_rating_is_one_per_session(api: ApiFixture) -> None:
    client, _ = api
    session_id = _customer_session(client, "csat-token-dup")
    assert _rate(client, session_id, "csat-token-dup", 5, "很好").status_code == 200
    second = _rate(client, session_id, "csat-token-dup", 1, "改主意了")
    assert second.status_code == 409
    # 第一次的评分没被改（v1 不做改评）
    factory = client.app.state.session_factory
    with factory() as db:
        row = db.query(SessionRating).filter_by(session_id=session_id).one()
        assert (row.score, row.comment) == (5, "很好")


def test_rating_stores_original_comment_and_low_score_writes_nothing(api: ApiFixture) -> None:
    """低分不撤销验证、不建缺口/工单（intake 裁决 9）；留言库内原文。"""
    client, _ = api
    token = "csat-token-low"
    session_id = _customer_session(client, token)
    complete = _answer_with_citation(client, session_id, token)
    asset_id = complete["citations"][0]["asset_id"]

    factory = client.app.state.session_factory
    with factory() as db:
        from suite_api.models import Asset, HandoffTicket, KnowledgeGap

        before_verified = db.get(Asset, asset_id).last_verified_at
        gaps_before = db.query(KnowledgeGap).count()
        tickets_before = db.query(HandoffTicket).count()

    assert _rate(client, session_id, token, 1, "物流太慢，联系我 13800138000").status_code == 200

    with factory() as db:
        from suite_api.models import Asset, HandoffTicket, KnowledgeGap

        assert db.get(Asset, asset_id).last_verified_at == before_verified  # 不撤销验证
        assert db.query(KnowledgeGap).count() == gaps_before  # 不建缺口
        assert db.query(HandoffTicket).count() == tickets_before  # 不建工单
        row = db.query(SessionRating).filter_by(session_id=session_id).one()
        assert row.score == 1
        assert row.comment == "物流太慢，联系我 13800138000"  # 库内原文（未掩）


# ---------- thumbs 正反两向 ----------


def test_thumbs_up_records_without_triage(api: ApiFixture) -> None:
    """第 48 刀：正反馈只记不诊——点赞不该把资产推进复审队列。"""
    client, _ = api
    token = "csat-token-up"
    session_id = _customer_session(client, token)
    complete = _answer_with_citation(client, session_id, token)
    asset_id = complete["citations"][0]["asset_id"]

    factory = client.app.state.session_factory
    with factory() as db:
        from suite_api.models import Asset

        verified_before = db.get(Asset, asset_id).last_verified_at

    resp = client.post(
        f"/api/customer/sessions/{session_id}/messages/{complete['message_id']}/feedback",
        json={"helpful": True},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["feedback"]["helpful"] is True
    assert body["triaged_asset_ids"] == []

    with factory() as db:
        from suite_api.models import Asset

        assert db.get(Asset, asset_id).last_verified_at == verified_before


def test_thumbs_down_still_triages(api: ApiFixture) -> None:
    """回归：负反馈仍走分诊（撤销验证）。"""
    client, _ = api
    token = "csat-token-down"
    session_id = _customer_session(client, token)
    complete = _answer_with_citation(client, session_id, token)
    asset_id = complete["citations"][0]["asset_id"]

    resp = client.post(
        f"/api/customer/sessions/{session_id}/messages/{complete['message_id']}/feedback",
        json={"helpful": False},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["triaged_asset_ids"] == [asset_id]

    factory = client.app.state.session_factory
    with factory() as db:
        from suite_api.models import Asset

        assert db.get(Asset, asset_id).last_verified_at is None


def test_feedback_is_one_per_message(api: ApiFixture) -> None:
    client, _ = api
    token = "csat-token-dup-fb"
    session_id = _customer_session(client, token)
    complete = _answer_with_citation(client, session_id, token)
    url = f"/api/customer/sessions/{session_id}/messages/{complete['message_id']}/feedback"
    headers = {"Authorization": f"Bearer {token}"}
    assert client.post(url, json={"helpful": True}, headers=headers).status_code == 200
    assert client.post(url, json={"helpful": False}, headers=headers).status_code == 409


# ---------- 打回理由 ----------


def _material_task(client: TestClient) -> int:
    _login(client)
    products = client.get("/api/products").json()
    product_id = products[0]["id"]
    created = client.post(
        "/api/material/tasks", json={"product_id": product_id, "brief": "CSAT 打回理由用例"}
    )
    assert created.status_code in (201, 200), created.text
    return created.json()["id"]


def _to_pending_qc(client: TestClient, task_id: int) -> None:
    """把任务推到待抽检（真跑生成，未配 LLM 会 failed——直接落库改状态更稳）。"""
    factory = client.app.state.session_factory
    with factory() as db:
        from suite_api.models import MaterialTask

        task = db.get(MaterialTask, task_id)
        task.status = "pending_qc"
        db.commit()


def test_reject_with_reason_writes_reason(api: ApiFixture) -> None:
    client, _ = api
    task_id = _material_task(client)
    _to_pending_qc(client, task_id)
    resp = client.post(f"/api/material/tasks/{task_id}/reject", json={"reason": "画面糊"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "failed"
    assert body["last_error"] == "人工打回：画面糊"


def test_reject_without_reason_keeps_plain_text(api: ApiFixture) -> None:
    client, _ = api
    task_id = _material_task(client)
    _to_pending_qc(client, task_id)
    resp = client.post(f"/api/material/tasks/{task_id}/reject", json={})
    assert resp.status_code == 200
    assert resp.json()["last_error"] == "人工打回"


def test_reject_without_body_still_works(api: ApiFixture) -> None:
    """老前端不带 body 也能打回（兼容优先于强制填理由）。"""
    client, _ = api
    task_id = _material_task(client)
    _to_pending_qc(client, task_id)
    resp = client.post(f"/api/material/tasks/{task_id}/reject")
    assert resp.status_code == 200
    assert resp.json()["last_error"] == "人工打回"


def test_reject_reason_length_is_capped(api: ApiFixture) -> None:
    client, _ = api
    task_id = _material_task(client)
    _to_pending_qc(client, task_id)
    resp = client.post(f"/api/material/tasks/{task_id}/reject", json={"reason": "糊" * 201})
    assert resp.status_code == 422
    assert "打回理由" in resp.json()["detail"]


# ---------- 仪表 CSAT ----------


def _overview(client: TestClient) -> dict[str, Any]:
    _login(client)
    resp = client.get("/api/stats/overview")
    assert resp.status_code == 200
    return resp.json()


def test_csat_shape_and_no_fake_zero_average(api: ApiFixture) -> None:
    """形状契约 + 「没样本就说没有」：分布恒给全五档（前端不用补键）；均值只在
    真没有样本时为 None（绝不返回 0.0 冒充均分）。空态本身的钉子在纯函数单测
    `test_build_csat_empty_is_honest`（本文件共用库，跑到这里通常已有样本）。"""
    client, _ = api
    csat = _overview(client)["csat"]
    assert set(csat["distribution"]) == {"1", "2", "3", "4", "5"}
    assert (csat["average_last_7d"] is None) == (csat["ratings_last_7d"] == 0)


def test_csat_aggregates_and_masks_comments(api: ApiFixture) -> None:
    client, _ = api
    before = _overview(client)["csat"]
    base_total = before["ratings_last_7d"]

    one = _customer_session(client, "csat-agg-1")
    two = _customer_session(client, "csat-agg-2")
    assert _rate(client, one, "csat-agg-1", 5, "客服很快，电话 13800138000").status_code == 200
    assert _rate(client, two, "csat-agg-2", 3).status_code == 200

    csat = _overview(client)["csat"]
    assert csat["ratings_last_7d"] == base_total + 2
    # 留言出口必掩（库内是原文，见 test_rating_stores_original_comment...）
    masked = "".join(csat["recent_comments"])
    assert "13800138000" not in masked
    assert "1********00" in masked


def test_recent_comments_are_capped_and_truncated(api: ApiFixture) -> None:
    client, _ = api
    for index in range(4):
        token = f"csat-cap-{index}"
        session_id = _customer_session(client, token)
        assert _rate(client, session_id, token, 5, f"第{index}条留言" + "长" * 80).status_code == 200

    csat = _overview(client)["csat"]
    assert len(csat["recent_comments"]) == 3  # 只给最近 3 条
    assert all(len(text) <= 60 for text in csat["recent_comments"])  # 单条截断 60 字


def test_session_list_exposes_rating_badge(api: ApiFixture) -> None:
    """客服页星级徽章的取数口：会话列表带 rating（未评为 None）。"""
    client, _ = api
    token = "csat-badge"
    session_id = _customer_session(client, token)
    _login(client)
    rows = {row["id"]: row for row in client.get("/api/service/sessions").json()}
    assert rows[session_id]["rating"] is None

    assert _rate(client, session_id, token, 4, "还行").status_code == 200
    rows = {row["id"]: row for row in client.get("/api/service/sessions").json()}
    assert rows[session_id]["rating"] == 4


# ---------- 边界与批量（评审补钉） ----------


def test_blank_comment_is_stored_as_none(api: ApiFixture) -> None:
    client, _ = api
    token = "csat-blank"
    session_id = _customer_session(client, token)
    assert _rate(client, session_id, token, 5, "   ").status_code == 200
    factory = client.app.state.session_factory
    with factory() as db:
        assert db.query(SessionRating).filter_by(session_id=session_id).one().comment is None


def test_comment_at_max_length_is_accepted(api: ApiFixture) -> None:
    """500 字是**收**的（边界不做 off-by-one）；501 才是 422。"""
    client, _ = api
    token = "csat-maxlen"
    session_id = _customer_session(client, token)
    assert _rate(client, session_id, token, 3, "好" * 500).status_code == 200


def test_empty_session_can_be_rated(api: ApiFixture) -> None:
    """后端不卡「有没有回答」（裁决 10 只是前端不显示评分条）——契约如实钉住。"""
    client, _ = api
    token = "csat-empty"
    session_id = _customer_session(client, token)
    assert _rate(client, session_id, token, 5).status_code == 200


def test_session_list_batches_rating_for_many_sessions(api: ApiFixture) -> None:
    """多会话批量取分（一次 IN 查询）：评一个不串到别人，未评的仍是 None。"""
    client, _ = api
    tokens = [f"csat-batch-{i}" for i in range(3)]
    session_ids = [_customer_session(client, token) for token in tokens]
    assert _rate(client, session_ids[1], tokens[1], 2).status_code == 200

    _login(client)
    rows = {row["id"]: row["rating"] for row in client.get("/api/service/sessions").json()}
    assert rows[session_ids[1]] == 2
    assert rows[session_ids[0]] is None
    assert rows[session_ids[2]] is None


def test_reject_reason_whitespace_is_treated_as_absent(api: ApiFixture) -> None:
    client, _ = api
    task_id = _material_task(client)
    _to_pending_qc(client, task_id)
    resp = client.post(f"/api/material/tasks/{task_id}/reject", json={"reason": "   "})
    assert resp.status_code == 200
    assert resp.json()["last_error"] == "人工打回"


def test_reject_reason_at_max_length_is_accepted(api: ApiFixture) -> None:
    client, _ = api
    task_id = _material_task(client)
    _to_pending_qc(client, task_id)
    resp = client.post(f"/api/material/tasks/{task_id}/reject", json={"reason": "糊" * 200})
    assert resp.status_code == 200
    assert resp.json()["last_error"] == "人工打回：" + "糊" * 200


def test_rating_records_csat_metric(api: ApiFixture) -> None:
    """审计刀 9：评分要进指标（47 刀的观测面此前对 48 刀的新面完全瞎）。"""
    client, _ = api
    token = "csat-metric"
    session_id = _customer_session(client, token)
    before = REGISTRY.get_sample_value("csat_ratings_total", {"score": "5"}) or 0.0
    assert _rate(client, session_id, token, 5, "很好").status_code == 200
    after = REGISTRY.get_sample_value("csat_ratings_total", {"score": "5"}) or 0.0
    assert after == before + 1
