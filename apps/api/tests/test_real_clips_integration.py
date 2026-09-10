"""直播切片真链路集成测试（真 PG + ffmpeg；第 46 刀）。

契约（intake 验收 1-5）：
- 上传源录像：.mp4 -> 201（落 recordings/ 前缀、记 label/size）；非 .mp4 -> 422；
  超上限 -> 413（monkeypatch 小阈值，见下）；空文件 -> 422；未登录 -> 401。
- 上传即绑定：pending 且 recording_id IS NULL 的候选绑到新录像；已登记候选不动；
  已绑过的候选不被后续上传改写（单源模型）。
- 真切：拣选落在录像时间窗内的候选 -> 资产字节是 mp4（ftyp 魔数）+ 对象键 .mp4，
  transcript 预置为版本字段；候选 -> registered。
- 旧路径：无源录像的候选拣选 -> 字节仍是时间码转写文本（既有行为不回归）。
- 检索：视频资产发布后切块来自 transcript 字段（发布事务不因 mp4 二进制报错）。

大小上限选择：不跳过——用 monkeypatch 把 routes.clips.MAX_RECORDING_BYTES 调小到
8 字节，触发真实的 413 分支（比造 200MB 文件快且不占带宽）。

真切用例依赖 ffmpeg（现造 3 秒测试 mp4）：本机/CI ubuntu-latest 自带；未装则
skip 这些用例（上传/绑定/旧路径用例不依赖 ffmpeg，仍跑）。
"""

import os
import shutil
import subprocess
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

import suite_api.routes.clips as clips_routes
from suite_api.services.seed import SEED_CLIPS

ApiFixture = tuple[TestClient, Path]

_URL_ENV = "SUITE_TEST_DATABASE_URL"

# 扩展名校验之外的字节内容不被校验：上传/绑定用例用这个假 mp4 头即可
_FAKE_MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64


def _login(client: TestClient) -> None:
    assert (
        client.post(
            "/api/auth/login", json={"username": "operator", "password": "operator123"}
        ).status_code
        == 200
    )


def _url() -> str:
    return os.environ[_URL_ENV]


def _fetch_one(sql: str, params: tuple = ()) -> tuple | None:
    with psycopg.connect(_url()) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def _insert_candidate(
    *,
    start: str,
    end: str,
    transcript: str,
    recording_id: int | None = None,
    status: str = "pending",
) -> int:
    with psycopg.connect(_url()) as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO clip_candidates
                (product_id, status, timecode_start, timecode_end, transcript,
                 source_video_label, recording_id)
            VALUES ((SELECT id FROM products ORDER BY id LIMIT 1), %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (status, start, end, transcript, "测试录像", recording_id),
        )
        return int(cur.fetchone()[0])


def _recording_id_of(candidate_id: int) -> int | None:
    row = _fetch_one("SELECT recording_id FROM clip_candidates WHERE id = %s", (candidate_id,))
    assert row is not None
    return row[0]


@pytest.fixture(scope="module")
def test_mp4(tmp_path_factory: pytest.TempPathFactory) -> bytes:
    """现造 3 秒测试 mp4（testsrc 160x120@10，3 秒覆盖 00:00:00-00:00:03）。"""
    if shutil.which("ffmpeg") is None:
        pytest.skip("需要 ffmpeg 生成测试 mp4（本机/CI 自带）")
    path = tmp_path_factory.mktemp("clipvideo") / "source.mp4"
    subprocess.run(  # noqa: S603 - 固定参数，无 shell
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=160x120:rate=10",
            "-t",
            "3",
            "-pix_fmt",
            "yuv420p",
            "-y",
            str(path),
        ],
        check=True,
    )
    return path.read_bytes()


# ---------- 1. 上传契约 ----------


def test_upload_recording_contract(api: ApiFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = api
    _login(client)

    ok = client.post(
        "/api/clips/recordings",
        files={"file": ("live-0904.mp4", _FAKE_MP4, "video/mp4")},
    )
    assert ok.status_code == 201
    body = ok.json()
    assert body["label"] == "live-0904.mp4"
    assert body["size_bytes"] == len(_FAKE_MP4)
    assert body["created_at"]
    recording_id = body["id"]
    row = _fetch_one("SELECT object_key, size_bytes FROM clip_recordings WHERE id = %s", (recording_id,))
    assert row is not None
    assert row[0].startswith("recordings/") and row[0].endswith(".mp4")
    assert row[1] == len(_FAKE_MP4)

    # 非 .mp4 扩展名 -> 422
    assert (
        client.post(
            "/api/clips/recordings",
            files={"file": ("transcript.txt", _FAKE_MP4, "text/plain")},
        ).status_code
        == 422
    )
    # 空文件 -> 422
    assert (
        client.post(
            "/api/clips/recordings", files={"file": ("empty.mp4", b"", "video/mp4")}
        ).status_code
        == 422
    )
    # 超上限 -> 413（小阈值 monkeypatch，触发真实分支）
    monkeypatch.setattr(clips_routes, "MAX_RECORDING_BYTES", 8)
    assert (
        client.post(
            "/api/clips/recordings",
            files={"file": ("big.mp4", _FAKE_MP4, "video/mp4")},
        ).status_code
        == 413
    )
    monkeypatch.undo()

    # 未登录 -> 401
    client.cookies.clear()
    assert (
        client.post(
            "/api/clips/recordings", files={"file": ("live.mp4", _FAKE_MP4, "video/mp4")}
        ).status_code
        == 401
    )


# ---------- 2. 上传即绑定 ----------


def test_upload_binds_only_unbound_pending(api: ApiFixture) -> None:
    client, _ = api
    _login(client)

    # 已绑过的候选：本用例自造一份录像把它绑上（不依赖别的用例的执行顺序）。
    # 顺序要紧：先造 pre 候选再上传，上传会把它（以及库里其余无源录像的 pending）
    # 一次绑掉；本用例要断言的两个候选在第一次上传之后才插，才是「尚无源录像」态。
    pre_id = _insert_candidate(start="00:22:00", end="00:22:10", transcript="已绑候选不改写")
    first = client.post(
        "/api/clips/recordings", files={"file": ("first.mp4", _FAKE_MP4, "video/mp4")}
    )
    assert first.status_code == 201
    pre_recording = first.json()["id"]
    assert _recording_id_of(pre_id) == pre_recording

    pending_id = _insert_candidate(
        start="00:20:00", end="00:20:10", transcript="绑定测试待拣候选"
    )
    registered_id = _insert_candidate(
        start="00:21:00", end="00:21:10", transcript="已登记候选不回改", status="registered"
    )

    resp = client.post(
        "/api/clips/recordings",
        files={"file": ("bind.mp4", _FAKE_MP4, "video/mp4")},
    )
    assert resp.status_code == 201
    new_recording_id = resp.json()["id"]

    assert _recording_id_of(pending_id) == new_recording_id  # 无源录像的 pending 被绑
    assert _recording_id_of(registered_id) is None  # 已登记不动
    assert _recording_id_of(pre_id) == pre_recording  # 已绑过的候选不改写


# ---------- 3. 真切 mp4 ----------


def test_pick_with_recording_cuts_real_mp4(api: ApiFixture, test_mp4: bytes) -> None:
    client, storage_root = api
    _login(client)

    transcript = "真片段测试：钛钢内胆一体成型没有焊缝。"
    candidate_id = _insert_candidate(start="00:00:00", end="00:00:01", transcript=transcript)
    uploaded = client.post(
        "/api/clips/recordings", files={"file": ("cut.mp4", test_mp4, "video/mp4")}
    )
    assert uploaded.status_code == 201
    assert _recording_id_of(candidate_id) == uploaded.json()["id"]

    picked = client.post("/api/clips/candidates/pick", json={"ids": [candidate_id]})
    assert picked.status_code == 200
    asset_id = picked.json()[0]["id"]

    detail = client.get(f"/api/assets/{asset_id}").json()
    assert detail["kind"] == "video"
    assert detail["status"] == "pending_review"  # 预置字段路径照常推进待人洗
    version = detail["versions"][0]
    assert version["object_key"].endswith(".mp4")
    assert version["extracted_fields"]["transcript"] == {
        "value": transcript,
        "source": "machine",
    }
    # 资产字节是真 mp4：ftyp 魔数（通常 offset 4）
    raw = (storage_root / version["object_key"]).read_bytes()
    assert raw[4:8] == b"ftyp"

    # 候选收口：registered + 回执锚
    after = {r["id"]: r for r in client.get("/api/clips/candidates").json()}
    assert after[candidate_id]["status"] == "registered"
    assert after[candidate_id]["registered_asset_id"] == asset_id
    assert after[candidate_id]["recording"]["id"] == uploaded.json()["id"]


# ---------- 4. 旧路径（无源录像） ----------


def test_pick_without_recording_keeps_timecode_text(api: ApiFixture) -> None:
    client, storage_root = api
    _login(client)

    transcript = "旧路径测试：无源录像仍写时间码文本。"
    candidate_id = _insert_candidate(start="00:30:00", end="00:30:12", transcript=transcript)
    assert _recording_id_of(candidate_id) is None  # 后续插入的候选不回溯绑定

    picked = client.post("/api/clips/candidates/pick", json={"ids": [candidate_id]})
    assert picked.status_code == 200
    detail = client.get(f"/api/assets/{picked.json()[0]['id']}").json()
    version = detail["versions"][0]
    assert set(version["extracted_fields"]) == {"transcript"}  # 旧路径不预置字段
    raw = (storage_root / version["object_key"]).read_bytes()
    assert raw.decode() == f"[00:30:00-00:30:12] {transcript}"


# ---------- 5. 切失败：422 + 失败候选保持 pending，此前候选完整保留 ----------


def test_cut_failure_keeps_candidate_pending_and_previous_registered(
    api: ApiFixture, test_mp4: bytes
) -> None:
    client, _ = api
    _login(client)

    good_id = _insert_candidate(start="00:00:00", end="00:00:01", transcript="原子性好片段")
    assert (
        client.post(
            "/api/clips/recordings", files={"file": ("good.mp4", test_mp4, "video/mp4")}
        ).status_code
        == 201
    )
    # 坏候选绑到一份「扩展名 .mp4 但字节不是视频」的录像：ffmpeg 报 moov atom not found
    bad_id = _insert_candidate(start="00:00:00", end="00:00:01", transcript="原子性坏片段")
    assert (
        client.post(
            "/api/clips/recordings", files={"file": ("bad.mp4", _FAKE_MP4, "video/mp4")}
        ).status_code
        == 201
    )
    good_recording = _recording_id_of(good_id)
    bad_recording = _recording_id_of(bad_id)
    assert good_recording is not None and bad_recording is not None
    assert good_recording != bad_recording

    picked = client.post("/api/clips/candidates/pick", json={"ids": [good_id, bad_id]})
    assert picked.status_code == 422

    after = {r["id"]: r for r in client.get("/api/clips/candidates").json()}
    # 前一个候选完整保留：资产 + 回执锚都收口（逐候选 commit，不随 422 回滚）
    assert after[good_id]["status"] == "registered"
    assert after[good_id]["registered_asset_id"] is not None
    # 失败候选保持 pending，可重拣
    assert after[bad_id]["status"] == "pending"
    assert after[bad_id]["registered_asset_id"] is None


# ---------- 6. 检索：发布视频不读 mp4 字节，切块来自 transcript 字段 ----------


def test_published_video_chunks_come_from_transcript_field(
    api: ApiFixture, test_mp4: bytes
) -> None:
    client, _ = api
    _login(client)

    transcript = "检索来源测试：钛钢内胆是316不锈钢材质。"
    candidate_id = _insert_candidate(start="00:00:01", end="00:00:02", transcript=transcript)
    uploaded = client.post(
        "/api/clips/recordings", files={"file": ("retrieval.mp4", test_mp4, "video/mp4")}
    )
    assert uploaded.status_code == 201

    picked = client.post("/api/clips/candidates/pick", json={"ids": [candidate_id]})
    assert picked.status_code == 200
    asset_id = picked.json()[0]["id"]
    # 字节是 mp4 二进制：发布若仍读字节切块会 ChunkingError（409）——200 即证明走字段
    assert client.post(f"/api/assets/{asset_id}/publish").status_code == 200

    with psycopg.connect(_url()) as conn, conn.cursor() as cur:
        cur.execute("SELECT chunk FROM retrieval_chunks WHERE asset_id = %s", (asset_id,))
        chunks = [row[0] for row in cur.fetchall()]
    assert chunks, "视频资产发布后应有切块"
    assert any("钛钢内胆是316不锈钢材质" in chunk for chunk in chunks)


def test_seed_candidates_available(api: ApiFixture) -> None:
    """种子里仍有候选（本模块自建库，前面用例插入的都是额外行）。"""
    client, _ = api
    _login(client)
    rows = client.get("/api/clips/candidates").json()
    assert len(rows) >= len(SEED_CLIPS)
    assert all("recording" in row for row in rows)  # 列表出口带源录像字段
