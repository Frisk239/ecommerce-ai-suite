"""云 ASR 转写端点集成测试（真 PG + 真 ffmpeg；第 93 刀，ADR 0050）。

契约（施工单 Must 4 + 10）：
- 未登录 401；无 ``ASR_API_KEY`` 状态 409「ASR 未配置」且**一行候选都不落**
  （fail-closed：不建客户端、不发请求）；
- 录像无音轨 422；云转写失败 502；录像不存在 404；指定商品不存在 404；
- 成功：候选落 clip_candidates（status=pending、transcript_source='cloud'、
  recording_id 直接绑定、timecode 由聚合段生成、transcript=句文本连接）；
- 提音轨真跑 ffmpeg：送给 ASR 的字节是 16kHz 单声道 wav（替身里断言）；
- 幂等口径：该录像有未拣选 cloud 候选时重跑 409（带现有条数，不追加）；全部
  拣选后可再生成一批；
- 总预算闸（审计 19）：慢转写穿 300s 预算 → 部分候选先落库 + 502 带已转块数；
- 46 刀绑定语义不动：转写候选带 recording_id，后续上传**不会**误绑它们。

云请求用替身（monkeypatch ``services.asr.transcribe_audio``）——不打真网；ffmpeg
提音轨、DB 落值、端点码位全走真路径。真云验收由 Owner 带 key 补（closeout 记）。
"""

import io
import os
import shutil
import subprocess
import wave
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

import suite_api.services.asr as asr_service
from suite_api.services.asr import ASRUnavailable
from suite_api.services.seed import SEED_CLIPS

ApiFixture = tuple[TestClient, Path]

_URL_ENV = "SUITE_TEST_DATABASE_URL"

# 替身 ASR 响应：两句、停顿 1.6s（≥1.2 -> 断成两条候选）——时间戳与「TTS 停顿
# 对齐」同形，端点侧的聚合与时间码由此可逐值断言
_FAKE_SEGMENTS = [
    {"start": 0.2, "end": 1.4, "text": "今天给大家介绍一款钛钢保温杯。"},
    {"start": 3.0, "end": 4.2, "text": "内胆是一体成型的316不锈钢。"},
]


def _login(client: TestClient) -> None:
    assert (
        client.post(
            "/api/auth/login", json={"username": "operator", "password": "operator123"}
        ).status_code
        == 200
    )


def _url() -> str:
    return os.environ[_URL_ENV]


def _fetch(sql: str, params: tuple = ()) -> list[tuple]:
    with psycopg.connect(_url()) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _product_id() -> int:
    rows = _fetch("SELECT id FROM products ORDER BY id LIMIT 1")
    assert rows
    return int(rows[0][0])


def _make_mp4(tmp_path: Path, *, with_audio: bool) -> bytes:
    """现造测试 mp4：有音轨版（sine 3s + 黑底）与无音轨版（testsrc 3s）。"""
    path = tmp_path / ("speech.mp4" if with_audio else "silent.mp4")
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i"]
    if with_audio:
        command += ["color=c=black:s=160x120:r=10", "-f", "lavfi", "-i", "sine=frequency=440:duration=3"]
    else:
        command += ["testsrc=size=160x120:rate=10"]
    command += ["-t", "3", "-pix_fmt", "yuv420p", "-y", str(path)]
    subprocess.run(  # noqa: S603 - 固定参数，无 shell
        command, check=True, capture_output=True
    )
    return path.read_bytes()


@pytest.fixture(scope="module")
def needs_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        pytest.skip("需要 ffmpeg（提音轨与测试素材生成；本机/CI 自带）")


@pytest.fixture(scope="module")
def speech_mp4(tmp_path_factory: pytest.TempPathFactory, needs_ffmpeg: None) -> bytes:
    return _make_mp4(tmp_path_factory.mktemp("asr-speech"), with_audio=True)


@pytest.fixture(scope="module")
def silent_mp4(tmp_path_factory: pytest.TempPathFactory, needs_ffmpeg: None) -> bytes:
    return _make_mp4(tmp_path_factory.mktemp("asr-silent"), with_audio=False)


def _enable_asr(monkeypatch: pytest.MonkeyPatch, calls: list[bytes] | None = None) -> None:
    """配置态替身：is_configured=True + transcribe_audio 返回固定句级段（不打真网）。"""
    monkeypatch.setattr(asr_service, "is_configured", lambda: True)

    def fake_transcribe(audio_bytes: bytes, *, filename: str = "audio.wav") -> list[dict]:
        del filename
        if calls is not None:
            calls.append(audio_bytes)
        return [dict(segment) for segment in _FAKE_SEGMENTS]

    monkeypatch.setattr(asr_service, "transcribe_audio", fake_transcribe)


def _upload(client: TestClient, name: str, payload: bytes) -> int:
    response = client.post(
        "/api/clips/recordings", files={"file": (name, payload, "video/mp4")}
    )
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


# ---------- 1. 码位：401 / 409 / 404 ----------


def test_transcribe_requires_login(api: ApiFixture, speech_mp4: bytes) -> None:
    client, _ = api
    _login(client)
    recording_id = _upload(client, "asr-auth.mp4", speech_mp4)
    client.cookies.clear()
    assert client.post(f"/api/clips/recordings/{recording_id}/transcribe").status_code == 401


def test_transcribe_without_key_is_409_and_writes_nothing(
    api: ApiFixture, speech_mp4: bytes
) -> None:
    """空 key = 诚实拒绝：409 + 0 候选（conftest 强制空 ASR_API_KEY）。"""
    client, _ = api
    _login(client)
    status_body = client.get("/api/clips/asr/status")
    assert status_body.status_code == 200
    assert status_body.json() == {"configured": False}

    recording_id = _upload(client, "asr-nokey.mp4", speech_mp4)
    response = client.post(f"/api/clips/recordings/{recording_id}/transcribe")
    assert response.status_code == 409
    assert "ASR 未配置" in response.json()["detail"]
    assert _fetch("SELECT id FROM clip_candidates WHERE recording_id = %s", (recording_id,)) == []


def test_transcribe_unknown_recording_is_404(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    assert client.post("/api/clips/recordings/999999/transcribe").status_code == 404


def test_transcribe_status_endpoint_reports_configured(api: ApiFixture, monkeypatch) -> None:
    client, _ = api
    _login(client)
    _enable_asr(monkeypatch)
    assert client.get("/api/clips/asr/status").json() == {"configured": True}


# ---------- 2. 无音轨 422 / 云失败 502 ----------


def test_transcribe_without_audio_track_is_422(
    api: ApiFixture, silent_mp4: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = api
    _login(client)
    _enable_asr(monkeypatch)
    recording_id = _upload(client, "asr-silent.mp4", silent_mp4)
    response = client.post(f"/api/clips/recordings/{recording_id}/transcribe")
    assert response.status_code == 422
    assert "音轨" in response.json()["detail"]
    assert _fetch("SELECT id FROM clip_candidates WHERE recording_id = %s", (recording_id,)) == []


def test_transcribe_upstream_failure_is_502(
    api: ApiFixture, speech_mp4: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = api
    _login(client)
    monkeypatch.setattr(asr_service, "is_configured", lambda: True)

    def fail(audio_bytes: bytes, *, filename: str = "audio.wav") -> list[dict]:
        raise ASRUnavailable("云转写服务暂时不可用")

    monkeypatch.setattr(asr_service, "transcribe_audio", fail)
    recording_id = _upload(client, "asr-502.mp4", speech_mp4)
    response = client.post(f"/api/clips/recordings/{recording_id}/transcribe")
    assert response.status_code == 502
    assert "云转写服务暂时不可用" in response.json()["detail"]
    assert _fetch("SELECT id FROM clip_candidates WHERE recording_id = %s", (recording_id,)) == []


# ---------- 3. 成功路径：真 ffmpeg 提音轨 + 聚合落候选 ----------


def test_transcribe_success_writes_cloud_candidates(
    api: ApiFixture, speech_mp4: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = api
    _login(client)
    calls: list[bytes] = []
    _enable_asr(monkeypatch, calls)
    recording_id = _upload(client, "asr-ok.mp4", speech_mp4)

    response = client.post(f"/api/clips/recordings/{recording_id}/transcribe")
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt["candidates_created"] == 2
    assert receipt["segments"] == 2  # 聚合前句数
    assert receipt["duration_ms"] >= 0
    assert receipt["note"] is None

    # 送给 ASR 的字节是 ffmpeg 提的 16kHz 单声道 wav（真提音轨，不是原始 mp4）
    assert len(calls) == 1
    assert calls[0][:4] == b"RIFF"
    with wave.open(io.BytesIO(calls[0]), "rb") as reader:
        assert reader.getnchannels() == 1 and reader.getframerate() == 16000
        assert 2.5 <= reader.getnframes() / reader.getframerate() <= 3.5

    rows = _fetch(
        "SELECT status, transcript, transcript_source, timecode_start, timecode_end,"
        " product_id, recording_id FROM clip_candidates WHERE recording_id = %s"
        " ORDER BY timecode_start",
        (recording_id,),
    )
    assert len(rows) == 2
    assert rows[0] == (
        "pending",
        "今天给大家介绍一款钛钢保温杯。",
        "cloud",
        "00:00:00",
        "00:00:02",
        None,  # 无商品归属（不编造）
        recording_id,
    )
    assert rows[1][1] == "内胆是一体成型的316不锈钢。"
    assert rows[1][3:6] == ("00:00:03", "00:00:05", None)


def test_transcribed_candidates_visible_in_candidate_list(
    api: ApiFixture, speech_mp4: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = api
    _login(client)
    _enable_asr(monkeypatch)
    recording_id = _upload(client, "asr-list.mp4", speech_mp4)
    assert client.post(f"/api/clips/recordings/{recording_id}/transcribe").status_code == 200

    rows = client.get("/api/clips/candidates").json()
    mine = [row for row in rows if row["recording"] is not None and row["recording"]["id"] == recording_id]
    assert len(mine) == 2
    assert all(row["transcript_source"] == "cloud" for row in mine)
    assert all(row["status"] == "pending" for row in mine)
    assert all(row["product_id"] is None and row["product_name"] == "—" for row in mine)
    # 种子候选（非 ASR 通道，0030 存量回填口径）一律 manual——列默认值也走通
    by_transcript = {row["transcript"]: row["transcript_source"] for row in rows}
    assert [by_transcript[clip["transcript"]] for clip in SEED_CLIPS] == ["manual"] * len(SEED_CLIPS)


def test_transcribe_with_product_attribution(api: ApiFixture, speech_mp4: bytes, monkeypatch) -> None:
    client, _ = api
    _login(client)
    _enable_asr(monkeypatch)
    recording_id = _upload(client, "asr-product.mp4", speech_mp4)
    product_id = _product_id()

    assert (
        client.post(
            f"/api/clips/recordings/{recording_id}/transcribe", json={"product_id": 999999}
        ).status_code
        == 404
    )
    response = client.post(
        f"/api/clips/recordings/{recording_id}/transcribe", json={"product_id": product_id}
    )
    assert response.status_code == 200
    owners = _fetch(
        "SELECT DISTINCT product_id FROM clip_candidates WHERE recording_id = %s", (recording_id,)
    )
    assert owners == [(product_id,)]


# ---------- 4. 幂等：重跑拒绝（带条数）；拣选后可再生成 ----------


def test_rerun_with_pending_cloud_candidates_is_409(
    api: ApiFixture, speech_mp4: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = api
    _login(client)
    _enable_asr(monkeypatch)
    recording_id = _upload(client, "asr-rerun.mp4", speech_mp4)
    assert client.post(f"/api/clips/recordings/{recording_id}/transcribe").status_code == 200

    again = client.post(f"/api/clips/recordings/{recording_id}/transcribe")
    assert again.status_code == 409
    assert "2 条未拣选" in again.json()["detail"]
    assert len(_fetch("SELECT id FROM clip_candidates WHERE recording_id = %s", (recording_id,))) == 2


def test_rerun_allowed_after_all_candidates_picked(
    api: ApiFixture, speech_mp4: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = api
    _login(client)
    _enable_asr(monkeypatch)
    recording_id = _upload(client, "asr-repick.mp4", speech_mp4)
    assert client.post(f"/api/clips/recordings/{recording_id}/transcribe").status_code == 200
    ids = [row[0] for row in _fetch(
        "SELECT id FROM clip_candidates WHERE recording_id = %s ORDER BY id", (recording_id,)
    )]

    picked = client.post("/api/clips/candidates/pick", json={"ids": ids})
    assert picked.status_code == 200, picked.text
    # 未归属候选拣选出的资产也没有商品（不编造归属），字节仍从该录像真切
    assert picked.json()[0]["product"] is None

    second = client.post(f"/api/clips/recordings/{recording_id}/transcribe")
    assert second.status_code == 200, second.text
    assert second.json()["candidates_created"] == 2
    statuses = _fetch(
        "SELECT status, count(*) FROM clip_candidates WHERE recording_id = %s GROUP BY status"
        " ORDER BY status",
        (recording_id,),
    )
    assert dict(statuses) == {"pending": 2, "registered": 2}


def test_upload_after_transcribe_does_not_rebind_cloud_candidates(
    api: ApiFixture, speech_mp4: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """转写候选直接带 recording_id（46 刀「上传绑无源候选」只圈 recording_id IS NULL）。"""
    client, _ = api
    _login(client)
    _enable_asr(monkeypatch)
    first = _upload(client, "asr-bind-a.mp4", speech_mp4)
    assert client.post(f"/api/clips/recordings/{first}/transcribe").status_code == 200

    second = _upload(client, "asr-bind-b.mp4", speech_mp4)
    assert second != first
    bound = _fetch(
        "SELECT DISTINCT recording_id FROM clip_candidates WHERE transcript_source = 'cloud'"
        " AND status = 'pending' AND recording_id = %s",
        (first,),
    )
    assert bound == [(first,)]  # 转写候选没被后续上传改绑
    assert _fetch("SELECT id FROM clip_candidates WHERE recording_id = %s", (second,)) == []


# ---------- 5. 总预算闸（审计 19）：慢转写穿预算 → 部分候选落库 + 502 ----------


def test_transcribe_over_budget_saves_partial_candidates_and_502(
    api: ApiFixture, speech_mp4: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """三块慢转写（假钟每块 +200s）穿 300s 总预算：首块成功后，第二块开转前
    「累计 200s + 预估 200s」超限即停——**已成功块聚合出的部分候选先落库**，
    端点 502 带「已转 1/3 块」与保留条数；重跑被既有未拣选 cloud 候选 409 挡住
    （带条数——幂等口径不因部分失败破例：先拣选，再整段重转或切段上传）。"""
    client, _ = api
    _login(client)
    monkeypatch.setattr(asr_service, "is_configured", lambda: True)
    # 假钟（审计 19 的时钟缝）：只在假转写里推进——提音轨真跑 ffmpeg 但计时
    # 走假钟，预算判定确定性可断言，不必真等 300s
    clock = {"now": 1000.0}
    monkeypatch.setattr(asr_service, "_now_seconds", lambda: clock["now"])

    def slow_transcribe(audio_bytes: bytes, *, filename: str = "audio.wav") -> list[dict]:
        del audio_bytes, filename
        clock["now"] += 200.0  # 每块「耗时」200s：均速预估下第二块必穿预算
        return [dict(segment) for segment in _FAKE_SEGMENTS]

    monkeypatch.setattr(asr_service, "transcribe_audio", slow_transcribe)
    monkeypatch.setattr(
        asr_service,
        "split_wav_chunks",
        lambda wav_bytes: [(0.0, b"c1"), (600.0, b"c2"), (1200.0, b"c3")],
    )
    recording_id = _upload(client, "asr-budget.mp4", speech_mp4)

    response = client.post(f"/api/clips/recordings/{recording_id}/transcribe")
    assert response.status_code == 502, response.text
    detail = response.json()["detail"]
    assert "转写超总预算" in detail
    assert "已转 1/3 块" in detail
    assert "2 条部分候选" in detail

    # 部分成果保留：首块的两条候选已落 pending（cloud、直接绑该录像）
    rows = _fetch(
        "SELECT status, transcript_source FROM clip_candidates WHERE recording_id = %s",
        (recording_id,),
    )
    assert len(rows) == 2
    assert all(row == ("pending", "cloud") for row in rows)

    # 幂等口径自洽：重跑 409 带现有条数（不是追加一批）
    again = client.post(f"/api/clips/recordings/{recording_id}/transcribe")
    assert again.status_code == 409
    assert "2 条未拣选" in again.json()["detail"]
