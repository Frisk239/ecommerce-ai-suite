"""血缘视图集成测试（真 PG；第 20 刀/ADR 0026）。

契约（全走 HTTP，数据源全部来自既有写路径的真实动作）：
- 发布挂商品的文档（确认字段->发布=写回事件）+ 客服问句引用 -> lineage：
  origin=upload、时间线含 confirm/publish（操作者名）、writebacks 带版本+
  action+操作者+商品锚+fields（按版本从 confirmed_fields 派生）、citations
  计数=引用消息数、样例带问句/版本/会话/时间且倒序；未被引用的其他资产不串
  （containment 按 asset_id 精确）；
- 开修订->发布 v2->回滚 v1 -> writebacks 出现 action=rollback 行（0034 回滚
  即回口径，_write_back_product 随移指针事务），fields 指向回滚目标版；
- 发布对话 + 考核作答（题源锚指向该资产）-> coaching 块出现对应记录带版本号；
  同一资产被引用+被抽题时三块各现各的；
- 未使用资产 -> 三块全空（空态）+ 时间线空，仍 200；
- 401（未登录）/404（资产不存在）。

citations 下推的形状由单测编译断言钉死（test_lineage.py）；这里做行为验证：
多个资产的引用消息同库并存时，各资产 lineage 互不混入。
"""

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sse_helpers import parse_sse_events

ApiFixture = tuple[TestClient, Path]

# 口味行只在检索时有用（不进规格确认）：保证问句 top 命中本文档
_DOC_WATER = (
    "【产品规格】\n净含量：777毫升\n保质期：18个月\n储存条件：常温避光\n口味：海盐荔枝"
).encode()
_DOC_TI = "【产品规格】\n材质：钛钢\n颜色：杏粉".encode()


def _login(client: TestClient) -> None:
    assert client.post(
        "/api/auth/login", json={"username": "operator", "password": "operator123"}
    ).status_code == 200


def _ask(client: TestClient, session_id: int, question: str) -> dict[str, Any]:
    """操作者预览发问，返回 complete 事件负载（citations 供断言）。"""
    with client.stream(
        "POST", f"/api/service/sessions/{session_id}/messages", json={"content": question}
    ) as resp:
        assert resp.status_code == 200
        events = parse_sse_events("".join(resp.iter_text()))
    assert events[-1][0] == "complete"
    return events[-1][1]


def _product_id_by_name(client: TestClient, name: str) -> int:
    products = client.get("/api/products").json()
    matching = [p for p in products if p["name"] == name]
    assert matching, f"种子商品缺失: {name}"
    return matching[0]["id"]


def _register(client: TestClient, content: bytes, *, product_id: int | None = None) -> dict:
    files = {"file": ("spec.txt", content, "text/plain")}
    data = {"productId": str(product_id)} if product_id is not None else {}
    resp = client.post("/api/assets/register", files=files, data=data)
    assert resp.status_code == 201
    return resp.json()


def _publish_water_doc(client: TestClient) -> tuple[int, int]:
    """登记挂瓶装水的规格文档 -> 按机洗结果逐字段确认 -> 发布；返回 (资产, 商品)。"""
    water_id = _product_id_by_name(client, "瓶装水")
    asset = _register(client, _DOC_WATER, product_id=water_id)
    extracted = asset["versions"][0]["extracted_fields"]
    confirmed = {
        field: entry["value"]
        for field, entry in extracted.items()
        if isinstance(entry, dict) and isinstance(entry.get("value"), str)
    }
    assert client.patch(
        f"/api/assets/{asset['id']}/versions/1/fields", json=confirmed
    ).status_code == 200
    assert client.post(f"/api/assets/{asset['id']}/publish").status_code == 200
    return asset["id"], water_id


def _publish_dialogue(client: TestClient, question: str, answer: str) -> int:
    """会话->登记回流->人洗确认 QA 对->发布，返回 dialogue 资产 id。"""
    sid = client.post("/api/service/sessions").json()["id"]
    _ask(client, sid, question)
    reg = client.post(f"/api/service/sessions/{sid}/register")
    assert reg.status_code == 201
    asset_id = reg.json()["id"]
    assert client.patch(
        f"/api/assets/{asset_id}/versions/1/fields", json={"qa_pairs": [{"q": question, "a": answer}]}
    ).status_code == 200
    assert client.post(f"/api/assets/{asset_id}/publish").status_code == 200
    return asset_id


# ---------- 文档：引用 + 写回 + 时间线 ----------


def test_lineage_document_citations_and_writebacks(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    asset_id, water_id = _publish_water_doc(client)

    sid = client.post("/api/service/sessions").json()["id"]
    complete_a = _ask(client, sid, "海盐荔枝是什么口味")
    assert any(c["asset_id"] == asset_id for c in complete_a["citations"])
    # 第二问也引用同一资产：计数=消息数（2），样例倒序（最新在前）
    _ask(client, sid, "净含量777毫升的保质期")

    resp = client.get(f"/api/assets/{asset_id}/lineage")
    assert resp.status_code == 200
    body = resp.json()

    # 从哪来：来源如实；登记时间 assets 没存列 -> 恒 null（不发明时间）
    assert body["origin"] == {"source_kind": "upload", "created_at": None}

    # 版本与审计：确认+发布两条，倒序（发布在最新），操作者名 join 出来
    timeline = [(e["action"], e["version_no"], e["operator"]) for e in body["versions_audit"]]
    assert timeline == [("publish", 1, "operator"), ("confirm", 1, "operator")]

    # 写回=发布/回滚事件；fields 按版本号从该版 confirmed_fields 派生（瓶装水
    # schema=净含量+保质期；正文的「口味/储存条件」行非 schema 字段、不入确认
    # 集）；带商品锚
    (writeback,) = body["usages"]["writebacks"]
    assert writeback["version_no"] == 1
    assert writeback["action"] == "publish"
    assert writeback["operator"] == "operator"
    assert writeback["product_id"] == water_id
    assert set(writeback["fields"]) == {"净含量", "保质期"}
    assert writeback["at"]

    # 引用：计数=引用本资产的消息条数；样例带问句截断/版本/会话/时间，倒序
    citations = body["usages"]["citations"]
    assert citations["total"] == 2
    assert [s["question"] for s in citations["samples"]] == ["净含量777毫升的保质期", "海盐荔枝是什么口味"]
    assert all(s["version_no"] == 1 and s["session_id"] == sid for s in citations["samples"])
    assert all(s["at"] for s in citations["samples"])


def test_lineage_citations_isolated_across_assets(api: ApiFixture) -> None:
    """containment 按 asset_id 精确：另一资产的同库引用消息不串进来（下推行为验证）。"""
    client, _ = api
    _login(client)
    water_id, _ = _publish_water_doc(client)
    ti = _register(client, _DOC_TI)  # 不挂商品：无必填闸门直接发布
    assert client.post(f"/api/assets/{ti['id']}/publish").status_code == 200

    sid = client.post("/api/service/sessions").json()["id"]
    _ask(client, sid, "材质是钛钢吗")

    ti_lineage = client.get(f"/api/assets/{ti['id']}/lineage").json()
    assert ti_lineage["usages"]["citations"]["total"] == 1
    assert ti_lineage["usages"]["citations"]["samples"][0]["question"] == "材质是钛钢吗"
    # 水文档此前被别的用例引用过也与我无关：钛钢 lineage 不含水问句
    assert all("海盐" not in s["question"] for s in ti_lineage["usages"]["citations"]["samples"])
    # 写回只随发布：钛钢文档未挂商品也记发布事件（product_id=null）
    (wb,) = ti_lineage["usages"]["writebacks"]
    assert wb["product_id"] is None
    # 水文档自己的计数不受钛钢消息污染（本用例内水文档未被问）
    water_lineage = client.get(f"/api/assets/{water_id}/lineage").json()
    assert all("钛钢" not in s["question"] for s in water_lineage["usages"]["citations"]["samples"])


def test_lineage_writebacks_include_rollback_with_target_version_fields(api: ApiFixture) -> None:
    """回滚即回口径（0034）：发布与回滚同走 _write_back_product——lineage 的
    writebacks 必须含 action=rollback 行，fields 指向回滚目标版的确认字段。"""
    client, _ = api
    _login(client)
    asset_id, _ = _publish_water_doc(client)
    # 开修订 -> 发布 v2（继承确认直接过闸）-> 回滚到 v1
    assert client.post(f"/api/assets/{asset_id}/revisions").status_code == 201
    assert client.post(f"/api/assets/{asset_id}/publish").status_code == 200
    rolled = client.post(f"/api/assets/{asset_id}/rollback", json={"version_no": 1})
    assert rolled.status_code == 200

    body = client.get(f"/api/assets/{asset_id}/lineage").json()
    assert [
        (w["version_no"], w["action"]) for w in body["usages"]["writebacks"]
    ] == [(1, "rollback"), (2, "publish"), (1, "publish")]
    # 回滚行字段=目标 v1 的确认字段（净含量+保质期）；时间线倒序首行即回滚
    assert set(body["usages"]["writebacks"][0]["fields"]) == {"净含量", "保质期"}
    assert body["versions_audit"][0]["action"] == "rollback"


# ---------- 对话：考核抽题 + 客服引用 三块各现各的 ----------


def test_lineage_dialogue_coaching_and_citations(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    asset_id = _publish_dialogue(client, "盲盒可以指定款式吗", "盲盒随机发货，不能指定")

    # 考核：从题库拿该资产的题作答（空凭证进程=未评分行，记录仍落库）
    question = next(
        q
        for q in client.get("/api/coach/questions").json()
        if q["key"]["asset_id"] == asset_id and q["key"]["source"] == "qa"
    )
    attempt = client.post(
        "/api/coach/attempts", json={"question_key": question["key"], "answer": "随机发的哦"}
    )
    assert attempt.status_code == 200
    record_id = attempt.json()["id"]

    # 客服引用：新会话问同一句，命中发布的 QA 块
    sid = client.post("/api/service/sessions").json()["id"]
    _ask(client, sid, "盲盒可以指定款式吗")

    body = client.get(f"/api/assets/{asset_id}/lineage").json()
    assert body["origin"] == {"source_kind": "session_backflow", "created_at": None}

    (coach_row,) = body["usages"]["coaching"]
    assert coach_row["record_id"] == record_id
    assert coach_row["question"] == "盲盒可以指定款式吗"
    assert coach_row["version_no"] == 1  # 题源锚里的版本号原样带出
    assert coach_row["at"]

    assert body["usages"]["citations"]["total"] == 1
    assert body["usages"]["citations"]["samples"][0]["session_id"] == sid
    # 回流登记不写审计（登记不是 0005 治理动作）：时间线只有确认+发布
    assert [e["action"] for e in body["versions_audit"]] == ["publish", "confirm"]
    # 写回=发布/回滚事件：对话未挂商品也记发布行，product_id 如实 null；
    # fields 派生该版 confirmed_fields 键——对话的就是 qa_pairs（如实非现编）
    (wb,) = body["usages"]["writebacks"]
    assert wb["version_no"] == 1 and wb["product_id"] is None
    assert wb["action"] == "publish"
    assert wb["fields"] == ["qa_pairs"]


# ---------- 未使用资产：空态 ----------


def test_lineage_unused_asset_empty_state(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    asset = _register(client, _DOC_TI, product_id=None)

    resp = client.get(f"/api/assets/{asset['id']}/lineage")
    assert resp.status_code == 200
    body = resp.json()
    assert body["versions_audit"] == []
    assert body["usages"]["citations"] == {"total": 0, "samples": []}
    assert body["usages"]["writebacks"] == []
    assert body["usages"]["coaching"] == []


# ---------- 鉴权与 404 ----------


def test_lineage_requires_login_and_404(api: ApiFixture) -> None:
    client, _ = api
    client.cookies.clear()
    assert client.get("/api/assets/1/lineage").status_code == 401
    _login(client)
    assert client.get("/api/assets/999999/lineage").status_code == 404
    assert client.get("/api/assets/999999/lineage").json()["detail"] == "资产不存在"
