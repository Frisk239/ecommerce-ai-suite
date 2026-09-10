"""直播切片集成测试（真 PG；第 18 刀/ADR 0014/0015/0039）。

契约：
- 种子 clip_candidates ≥3（保温杯/瓶装水各有覆盖、转写含可检索关键词），
  seed 重跑幂等不重复插；
- 候选列表（带商品名）→ 拣选 2 条 → 各登记 kind=video / source=clip_pick /
  挂商品 / 待人洗（机洗空字段集弃权推进）/ 对象键 clips/ 前缀 / 版本正文
  「[start-end] 转写」；
- video 无规格必填：无需确认任何字段直接发布 200（合法字段集为空——任意
  字段 PATCH 422）；
- 顾客问转写关键词（「钛钢内胆」）命中切片资产 v1 并引用；
- 素材中心「切片汇入」数据源：GET /api/assets 过滤 video+clip_pick 可见两条；
- 重复拣选 409（含混合批次整体拒绝）；不存在 404；空 ids/非数组 422；
  未登录 401。
"""

import os
from pathlib import Path

import psycopg
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sse_helpers import parse_sse_events

from suite_api.db import to_sqlalchemy_url
from suite_api.services.seed import SEED_CLIPS, seed_startup_data

ApiFixture = tuple[TestClient, Path]

_URL_ENV = "SUITE_TEST_DATABASE_URL"


def _login(client: TestClient) -> None:
    assert (
        client.post(
            "/api/auth/login", json={"username": "operator", "password": "operator123"}
        ).status_code
        == 200
    )


def _candidates(client: TestClient) -> list[dict]:
    resp = client.get("/api/clips/candidates")
    assert resp.status_code == 200
    return resp.json()


def _ask(client: TestClient, session_id: int, question: str) -> list[tuple[str, dict]]:
    with client.stream(
        "POST", f"/api/service/sessions/{session_id}/messages", json={"content": question}
    ) as resp:
        assert resp.status_code == 200
        raw = "".join(resp.iter_text())
    return parse_sse_events(raw)


def _clip_count(url: str) -> int:
    with psycopg.connect(url) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM clip_candidates")
        return int(cur.fetchone()[0])


def _reseed(url: str) -> None:
    engine = create_engine(to_sqlalchemy_url(url))
    try:
        seed_startup_data(engine, "operator123")
    finally:
        engine.dispose()


# ---------- 种子：形状与幂等 ----------


def test_seed_clips_shape_and_idempotent(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    rows = _candidates(client)
    assert len(rows) == len(SEED_CLIPS) >= 3
    # 保温杯/瓶装水各有候选；全部 pending、无回执锚、带源录像名与商品名
    assert {r["product_name"] for r in rows} == {"钛钢保温杯", "瓶装水"}
    assert all(r["status"] == "pending" for r in rows)
    assert all(r["registered_asset_id"] is None for r in rows)
    assert all(r["source_video_label"] for r in rows)
    assert all(r["timecode_start"] < r["timecode_end"] for r in rows)
    # 演示检索的关键词在种子里
    joined = "\n".join(r["transcript"] for r in rows)
    assert "钛钢内胆" in joined and "316不锈钢" in joined and "整箱24瓶" in joined

    url = os.environ[_URL_ENV]
    before = _clip_count(url)
    _reseed(url)
    _reseed(url)
    assert _clip_count(url) == before  # 幂等：按 (timecode_start, transcript) 跳过


# ---------- 列表批取（debt-2 N+1 收口） ----------


def test_candidates_list_batches_product_names(api: ApiFixture) -> None:
    """N+1 钉测：候选列表一次 IN 查询批取商品名——请求期间对 products 的
    SELECT 恰为 1（旧逐行 db.get 在种子跨 2 商品下为 2 次）；行为等价
    （每行商品名与 /api/products 视图一致）。"""
    client, _ = api
    _login(client)
    from sqlalchemy import event

    session = client.app.state.session_factory()
    engine = session.get_bind()
    product_selects: list[str] = []

    def _listen(
        conn: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        del conn, cursor, parameters, context, executemany
        if "FROM products" in statement:
            product_selects.append(statement)

    event.listen(engine, "before_cursor_execute", _listen)
    try:
        rows = _candidates(client)
    finally:
        event.remove(engine, "before_cursor_execute", _listen)
        session.close()

    assert len(rows) >= 3 and len({r["product_id"] for r in rows}) >= 2
    assert len(product_selects) == 1  # 批取：全列表只一条 products 查询
    name_by_id = {p["id"]: p["name"] for p in client.get("/api/products").json()}
    assert all(r["product_name"] == name_by_id[r["product_id"]] for r in rows)


# ---------- 全闭环：拣选 → 登记 → 发布 → 检索引用 → 汇入视图 ----------


def test_pick_publish_retrieval_and_surface(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    rows = _candidates(client)
    cup = next(r for r in rows if "钛钢内胆" in r["transcript"])
    water = next(r for r in rows if "整箱24瓶" in r["transcript"])

    picked = client.post("/api/clips/candidates/pick", json={"ids": [cup["id"], water["id"]]})
    assert picked.status_code == 200
    asset_outs = picked.json()
    assert len(asset_outs) == 2
    assert {a["kind"] for a in asset_outs} == {"video"}
    assert {a["source_kind"] for a in asset_outs} == {"clip_pick"}

    for out, candidate in ((asset_outs[0], cup), (asset_outs[1], water)):
        asset = client.get(f"/api/assets/{out['id']}").json()
        assert asset["kind"] == "video"
        assert asset["source_kind"] == "clip_pick"
        assert asset["status"] == "pending_review"  # 机洗弃权推进待人洗（0039）
        assert asset["last_error"] is None
        assert asset["product"]["id"] == candidate["product_id"]
        assert asset["title"] == candidate["transcript"][:60]
        version = asset["versions"][0]
        assert version["object_key"].startswith("clips/")
        # 第 46 刀：video 字段集不再是空集——转写预置为 transcript 字段（检索正文源，
        # roadmap 明写「转写字段保留」）；仍不跑规格正则（类目字段集为空，preset
        # 与机洗无关，故不会有类目字段）
        assert set(version["extracted_fields"]) == {"transcript"}
        assert version["extracted_fields"]["transcript"]["source"] == "machine"
        # 无源录像的旧路径：登记字节仍是 [start-end] 转写 文本（0039；真切 mp4
        # 见 test_real_clips_integration，本文件的种子候选没有被上传绑定过）
        text = client.get(f"/api/assets/{out['id']}/versions/1/text")
        assert text.status_code == 200
        assert (
            text.text
            == f"[{candidate['timecode_start']}-{candidate['timecode_end']}] {candidate['transcript']}"
        )

    # video 合法字段集为空（人洗侧同分派口径）：任何字段 PATCH 都 422……
    assert (
        client.patch(
            f"/api/assets/{asset_outs[0]['id']}/versions/1/fields", json={"材质": "钛钢"}
        ).status_code
        == 422
    )
    # ……PATCH 空体 200（无字段可确认），随后不确认任何字段直接发布（闸门只卡
    # document+挂商品）
    assert (
        client.patch(f"/api/assets/{asset_outs[0]['id']}/versions/1/fields", json={}).status_code
        == 200
    )
    for out in asset_outs:
        assert client.post(f"/api/assets/{out['id']}/publish").status_code == 200

    # 候选行收口：registered + 回执锚（对照素材任务 asset_id 先例）
    after = {r["id"]: r for r in _candidates(client)}
    assert (after[cup["id"]]["status"], after[cup["id"]]["registered_asset_id"]) == (
        "registered",
        asset_outs[0]["id"],
    )
    assert (after[water["id"]]["status"], after[water["id"]]["registered_asset_id"]) == (
        "registered",
        asset_outs[1]["id"],
    )

    # 顾客问转写关键词 -> 命中切片资产 v1 并引用
    sid = client.post("/api/service/sessions").json()["id"]
    events = _ask(client, sid, "保温杯的钛钢内胆是什么材质")
    complete = events[-1][1]
    assert complete["kind"] == "answer"
    assert {"asset_id": asset_outs[0]["id"], "version_no": 1} in complete["citations"]
    answer = "".join(d["text"] for e, d in events if e == "delta")
    assert "316不锈钢" in answer or "钛钢内胆" in answer

    # 素材中心「切片汇入」数据源：全量资产过滤 video+clip_pick 看到这两条
    surface = [
        a
        for a in client.get("/api/assets").json()
        if a["kind"] == "video" and a["source_kind"] == "clip_pick"
    ]
    assert {a["id"] for a in surface} == {a["id"] for a in asset_outs}

    # 已发布素材资产的 kind 徽章口径：KIND_LABELS 补 video 是前端契约，此处钉后端值
    assert {a["status"] for a in surface} == {"published"}


# ---------- 重复拣选/入参/鉴权 ----------


def test_repick_and_error_contract(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    rows = _candidates(client)
    pending = [r for r in rows if r["status"] == "pending"]
    registered = [r for r in rows if r["status"] == "registered"]
    assert registered, "上一用例已登记两条"

    # 重复拣选 409（单向状态机，0039）
    assert (
        client.post("/api/clips/candidates/pick", json={"ids": [registered[0]["id"]]}).status_code
        == 409
    )
    # 混合批次：含已登记整体 409，pending 那条不被登记（事务不落）
    mixed = client.post(
        "/api/clips/candidates/pick", json={"ids": [pending[0]["id"], registered[0]["id"]]}
    )
    assert mixed.status_code == 409
    after = {r["id"]: r for r in _candidates(client)}
    assert after[pending[0]["id"]]["status"] == "pending"
    assert after[pending[0]["id"]]["registered_asset_id"] is None

    # 不存在的 id 404（批量任一不存在整体拒绝）
    assert client.post("/api/clips/candidates/pick", json={"ids": [999999]}).status_code == 404
    # 空数组 / 非数组 / 缺字段 422
    assert client.post("/api/clips/candidates/pick", json={"ids": []}).status_code == 422
    assert client.post("/api/clips/candidates/pick", json={"ids": "not-a-list"}).status_code == 422
    assert client.post("/api/clips/candidates/pick", json={}).status_code == 422

    # 未登录 401（切片是操作者动作）
    client.cookies.clear()
    assert client.get("/api/clips/candidates").status_code == 401
    assert client.post("/api/clips/candidates/pick", json={"ids": [1]}).status_code == 401
    _login(client)
