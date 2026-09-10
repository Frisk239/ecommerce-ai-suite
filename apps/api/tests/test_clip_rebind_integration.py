"""第 49 刀：源录像可改绑 + 上传回执真值（真 PG + ffmpeg 的集成用例在同目录
`test_real_clips_integration.py`；这里放不依赖 ffmpeg 的契约钉子）。

覆盖：
- `POST /clips/recordings/{id}/bind`：改绑全部待拣 / 只改指定 / 指定的候选不存在
  404 / 指定的候选已登记 409 且一行不写 / 录像不存在 404 / 幂等（重复 bind 同份
  仍然 200 且计数照给）。
- 上传回执带 `bound_count`（后端真值，不再是前端猜）。
- `GET /clips/recordings`：降序、字段齐、未登录 401。
"""

import os

import pytest
from fastapi.testclient import TestClient

ApiFixture = tuple[TestClient, object]


@pytest.fixture(scope="module")
def api(tmp_path_factory: pytest.TempPathFactory) -> ApiFixture:
    import psycopg
    from conftest import _split_url  # noqa: PLC0415

    from suite_api.main import create_app
    from suite_api.settings import Settings

    url = os.environ.get("SUITE_TEST_DATABASE_URL")
    if not url:
        pytest.skip("需真 Postgres：设 SUITE_TEST_DATABASE_URL")
    admin_url, dbname = _split_url(url)

    def _run(sql: str) -> None:
        with psycopg.connect(admin_url, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(sql)

    _run(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
    _run(f'CREATE DATABASE "{dbname}"')
    storage_root = tmp_path_factory.mktemp("objects")
    settings = Settings(database_url=url, storage_root=storage_root, llm_api_key="")
    app = create_app(settings)
    from suite_api.services.rate_limit import SlidingWindowLimiter

    app.state.login_limiter = SlidingWindowLimiter(10_000, 60.0)
    with TestClient(app) as client:
        yield client, storage_root
    _run(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')


_FAKE_MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64


def _login(client: TestClient) -> None:
    assert (
        client.post(
            "/api/auth/login", json={"username": "operator", "password": "operator123"}
        ).status_code
        == 200
    )


def _candidate_ids(client: TestClient, *, status: str = "pending") -> list[int]:
    rows = client.get("/api/clips/candidates").json()
    return [row["id"] for row in rows if row["status"] == status]


def _upload(client: TestClient, name: str) -> dict:
    resp = client.post("/api/clips/recordings", files={"file": (name, _FAKE_MP4, "video/mp4")})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _bind(client: TestClient, recording_id: int, ids: list[int] | None = None):
    body = {} if ids is None else {"candidate_ids": ids}
    return client.post(f"/api/clips/recordings/{recording_id}/bind", json=body)


def test_upload_receipt_carries_real_bound_count(api: ApiFixture) -> None:
    """回执的绑定条数是后端真值（不是前端上传前数的候选数）。"""
    client, _ = api
    _login(client)
    pending = _candidate_ids(client)
    assert pending, "种子候选应有 pending"

    first = _upload(client, "receipt-a.mp4")
    # 第一次上传：尚无源录像的 pending 全被顺手绑上
    assert first["bound_count"] == len(pending)

    # 第二次上传：已全部绑过 -> 顺手绑 0 条（真值就该是 0，而不是「候选总数」）
    second = _upload(client, "receipt-b.mp4")
    assert second["bound_count"] == 0


def test_bind_all_pending_rebinds_previous_recording(api: ApiFixture) -> None:
    """本刀的核心：已绑的待拣候选**可以改绑**（第 46 刀「绑过不改」的修订）。"""
    client, _ = api
    _login(client)
    _upload(client, "bind-a.mp4")
    second = _upload(client, "bind-b.mp4")
    # 本文件共享一个库，前面的用例可能已把候选绑到别的录像上——这里只断言
    # 「改绑后**全部**待拣都指向目标」这个不依赖前序状态的终态。
    pending = [row for row in client.get("/api/clips/candidates").json() if row["status"] == "pending"]
    assert pending, "种子候选应有 pending"

    resp = _bind(client, second["id"])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["recording_id"] == second["id"]
    assert body["label"] == "bind-b.mp4"
    assert body["bound_count"] == len(pending)

    after = client.get("/api/clips/candidates").json()
    moved = [row for row in after if row["status"] == "pending"]
    assert all(row["recording"]["id"] == second["id"] for row in moved)
    # 已登记候选一条都没动
    registered = [row for row in after if row["status"] == "registered"]
    assert all(row["recording"]["id"] != second["id"] for row in registered)


def test_bind_subset_only_touches_given_candidates(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    target = _upload(client, "bind-subset.mp4")
    pending = [row for row in client.get("/api/clips/candidates").json() if row["status"] == "pending"]
    chosen = [pending[0]["id"], pending[1]["id"]]

    resp = _bind(client, target["id"], chosen)
    assert resp.status_code == 200
    assert resp.json()["bound_count"] == 2

    rows = {row["id"]: row for row in client.get("/api/clips/candidates").json()}
    assert rows[chosen[0]]["recording"]["id"] == target["id"]
    assert rows[chosen[1]]["recording"]["id"] == target["id"]


def test_bind_is_idempotent(api: ApiFixture) -> None:
    """对已绑在这份录像上的候选再 bind：200 且计数照给（不是错误）。"""
    client, _ = api
    _login(client)
    target = _upload(client, "bind-idem.mp4")
    first = _bind(client, target["id"])
    assert first.status_code == 200 and first.json()["bound_count"] > 0
    again = _bind(client, target["id"])
    assert again.status_code == 200
    assert again.json()["bound_count"] == first.json()["bound_count"]


def test_bind_unknown_recording_is_404(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    assert client.post("/api/clips/recordings/999999/bind", json={}).status_code == 404


def test_bind_unknown_candidate_is_404(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    target = _upload(client, "bind-404.mp4")
    assert _bind(client, target["id"], [999999]).status_code == 404


def test_bind_registered_candidate_is_409_and_writes_nothing(api: ApiFixture) -> None:
    """已登记候选的绑定不可追改：409，且**一行都不写**（含同批里的待拣候选）。"""
    client, _ = api
    _login(client)
    target = _upload(client, "bind-409.mp4")
    # 造一条「已登记」候选（种子库全是 pending；直接改状态，不必真拣选——本用例
    # 钉的是改绑对已登记候选的拒绝，与拣选路径无关）
    from sqlalchemy import select

    from suite_api.models import ClipCandidate

    factory = client.app.state.session_factory
    with factory() as db:
        registered_row = db.scalars(
            select(ClipCandidate).where(ClipCandidate.status == "pending").order_by(ClipCandidate.id)
        ).first()
        registered_row.status = "registered"
        registered_row.registered_asset_id = None
        db.commit()
        registered_id = registered_row.id
        registered_recording_id = registered_row.recording_id
        # 给它先绑一份别的录像，用来证明 409 时一行都没写
        if registered_recording_id is None:
            registered_row.recording_id = target["id"] - 1 if target["id"] > 1 else None
            db.commit()
            registered_recording_id = registered_row.recording_id
    pending_id = [
        row["id"] for row in client.get("/api/clips/candidates").json() if row["status"] == "pending"
    ][0]

    resp = _bind(client, target["id"], [pending_id, registered_id])
    assert resp.status_code == 409
    assert "已登记" in resp.json()["detail"]

    rows = {row["id"]: row for row in client.get("/api/clips/candidates").json()}
    # 同批里的待拣候选一行都没写（拒绝原子性）
    assert rows[pending_id]["recording"] is None or rows[pending_id]["recording"]["id"] != target["id"]
    assert (rows[registered_id]["recording"] or {}).get("id") == registered_recording_id  # 原样


def test_bind_requires_login(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    target = _upload(client, "bind-auth.mp4")
    client.cookies.clear()
    assert client.post(f"/api/clips/recordings/{target['id']}/bind", json={}).status_code == 401


def test_recording_list_is_newest_first(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    newest = _upload(client, "list-newest.mp4")
    rows = client.get("/api/clips/recordings").json()
    assert rows[0]["id"] == newest["id"]
    assert {"id", "label", "size_bytes", "created_at"} <= set(rows[0])
    assert all("bound_count" not in row or row["bound_count"] is None for row in rows)
    client.cookies.clear()
    assert client.get("/api/clips/recordings").status_code == 401


def test_recording_list_is_capped_at_20(api: ApiFixture) -> None:
    """列表上限 20（验收 5）：多传一份就多一条，第 21 条不该出现。"""
    client, _ = api
    _login(client)
    for index in range(21):
        _upload(client, f"cap-{index:02d}.mp4")
    rows = client.get("/api/clips/recordings").json()
    assert len(rows) == 20
    labels = [row["label"] for row in rows]
    assert "cap-20.mp4" in labels  # 最新的在
    assert "cap-00.mp4" not in labels  # 最早的被挤出


def test_list_and_candidate_payloads_carry_no_bound_count(api: ApiFixture) -> None:
    """`bound_count` 只属于上传回执（评审 P2：别溢到列表与候选行的 recording 里）。"""
    client, _ = api
    _login(client)
    rows = client.get("/api/clips/recordings").json()
    assert rows and all("bound_count" not in row for row in rows)
    candidates = client.get("/api/clips/candidates").json()
    for row in candidates:
        if row["recording"] is not None:
            assert "bound_count" not in row["recording"]
