"""第 94c 刀：直播洗帧集成测试（真 PG + 真 ffmpeg；ADR 0053）。

契约（施工单 Must 1/2/7）：
- 闸门码位：未登录 401；非视频资产 422；未发布视频 409；**无 VLM key 409**
  （fail-closed，不建客户端不发请求）；旧时间码文本切片（键 .txt）422；
- 候选生成（VLM 替身打分）：真跑 ffprobe/ffmpeg——采样数、候选只含 ≥6 分、
  缩略图 base64 data URL 形状、at_time 形态；VLM 请求失败 502；
- 确认登记：kind=image、source_kind=clip_frame、标题「{商品名} · 实拍帧 mm:ss」、
  挂商品透传、字节是真 jpeg 全尺寸（非缩略图上限）、键 .jpg、VLM 描述草稿落
  extracted（94a 复用）、回执 cut_from="A-xxxx · v1"；
- 全链：确认 → 人洗 PATCH 图片描述 → 发布 → 顾客问图片内容词命中带引用
  （94b 链路通）。

云 VLM 用替身（monkeypatch ``services.vlm`` 的 is_configured/chat_with_image/
describe_image）——不打真网；ffprobe/ffmpeg、DB 落值、端点码位全走真路径。
真 VLM 验收由 Owner 带 key 补（closeout 记）。
"""

import os
import shutil
import subprocess
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from sse_helpers import parse_sse_events

import suite_api.services.vlm as vlm_service
from suite_api.services.vlm import VLMUnavailable

ApiFixture = tuple[TestClient, Path]

_URL_ENV = "SUITE_TEST_DATABASE_URL"

_DESCRIPTION = "画面中央是渐变色测试图案的商品展示帧"


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
    rows = _fetch("SELECT id, name FROM products ORDER BY id LIMIT 1")
    assert rows
    return int(rows[0][0])


def _product_name() -> str:
    rows = _fetch("SELECT name FROM products ORDER BY id LIMIT 1")
    assert rows
    return str(rows[0][0])


def _insert_candidate(
    *, start: str, end: str, transcript: str, recording_id: int | None = None
) -> int:
    with psycopg.connect(_url()) as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO clip_candidates
                (product_id, status, timecode_start, timecode_end, transcript,
                 source_video_label, recording_id)
            VALUES ((SELECT id FROM products ORDER BY id LIMIT 1), 'pending', %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (start, end, transcript, "洗帧测试录像", recording_id),
        )
        return int(cur.fetchone()[0])


@pytest.fixture(scope="module")
def needs_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("需要 ffmpeg/ffprobe（抽帧与测试素材生成；本机/CI 自带）")


@pytest.fixture(scope="module")
def live_mp4(tmp_path_factory: pytest.TempPathFactory, needs_ffmpeg: None) -> bytes:
    """现造 12 秒测试 mp4（testsrc 160x120@10）：采样点 [0, 5, 10]。"""
    path = tmp_path_factory.mktemp("frame-wash") / "live.mp4"
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
            "12",
            "-pix_fmt",
            "yuv420p",
            "-y",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path.read_bytes()


def _make_published_video(client: TestClient, mp4: bytes, *, transcript: str) -> int:
    """上传源录像 → 插候选绑上 → 拣选（真 mp4 切片）→ 发布。返回资产 id。

    候选窗口取整段（12s 源 → 12s 片段）：洗帧采样按片段时长算点，切太短会
    采不到 [5, 10] 两个采样点。
    """
    candidate_id = _insert_candidate(start="00:00:00", end="00:00:12", transcript=transcript)
    uploaded = client.post(
        "/api/clips/recordings", files={"file": ("wash.mp4", mp4, "video/mp4")}
    )
    assert uploaded.status_code == 201, uploaded.text
    picked = client.post("/api/clips/candidates/pick", json={"ids": [candidate_id]})
    assert picked.status_code == 200, picked.text
    asset_id = int(picked.json()[0]["id"])
    assert client.post(f"/api/assets/{asset_id}/publish").status_code == 200
    return asset_id


def _enable_vlm(
    monkeypatch: pytest.MonkeyPatch,
    scores: list[str] | None = None,
    *,
    draft: str | None = None,
) -> list[bytes]:
    """配置态替身：is_configured=True + 打分/描述按序回固定输出（不打真网）。

    scores 为 None 时不 patch chat_with_image（打分走真实调用路径，仅本模块
    不用）；draft 给了才 patch describe_image（确认登记的描述草稿）。
    """
    monkeypatch.setattr(vlm_service, "is_configured", lambda: True)
    calls: list[bytes] = []
    if scores is not None:
        replies = list(scores)

        def fake_chat(system_prompt: str, user_prompt: str, image_bytes: bytes) -> str:
            del system_prompt, user_prompt
            calls.append(image_bytes)
            return replies.pop(0) if replies else '{"score": 1, "note": "没了"}'

        monkeypatch.setattr(vlm_service, "chat_with_image", fake_chat)
    if draft is not None:
        monkeypatch.setattr(vlm_service, "describe_image", lambda _b: draft)
    return calls


def _ask(client: TestClient, question: str) -> dict:
    created = client.post("/api/service/sessions")
    assert created.status_code == 201, created.text
    session_id = int(created.json()["id"])
    with client.stream(
        "POST", f"/api/service/sessions/{session_id}/messages", json={"content": question}
    ) as resp:
        assert resp.status_code == 200
        raw = "".join(resp.iter_text())
    events = parse_sse_events(raw)
    complete = [payload for event, payload in events if event == "complete"]
    assert complete, f"没有 complete 事件: {events}"
    return complete[0]


# ---------- 1. 闸门码位 ----------


def test_frame_wash_requires_login(api: ApiFixture, live_mp4: bytes) -> None:
    client, _ = api
    client.cookies.clear()
    assert client.post("/api/assets/1/frame-candidates").status_code == 401
    assert client.post("/api/assets/1/frames", json={"at_second": 0}).status_code == 401


def test_frame_candidates_reject_non_video_and_unpublished(
    api: ApiFixture, live_mp4: bytes
) -> None:
    client, _ = api
    _login(client)
    # 非视频资产：上传文档
    doc = client.post(
        "/api/assets/register",
        files={"file": ("spec.txt", "净含量：550毫升".encode(), "text/plain")},
    )
    assert doc.status_code == 201
    non_video = (
        client.post(f"/api/assets/{doc.json()['id']}/frame-candidates").status_code == 422
    )
    assert non_video

    # 视频但未发布：拣选后不 publish
    candidate_id = _insert_candidate(
        start="00:00:00", end="00:00:01", transcript="未发布视频洗帧测试"
    )
    uploaded = client.post(
        "/api/clips/recordings", files={"file": ("unpub.mp4", live_mp4, "video/mp4")}
    )
    assert uploaded.status_code == 201
    picked = client.post("/api/clips/candidates/pick", json={"ids": [candidate_id]})
    assert picked.status_code == 200
    unpublished = client.post(
        f"/api/assets/{picked.json()[0]['id']}/frame-candidates"
    )
    assert unpublished.status_code == 409
    assert "已发布" in unpublished.json()["detail"]


def test_frame_candidates_without_vlm_key_409(api: ApiFixture, live_mp4: bytes) -> None:
    """无 key（conftest 强制空 VLM_API_KEY）= 409 诚实拒绝，一行候选不出。"""
    client, _ = api
    _login(client)
    asset_id = _make_published_video(client, live_mp4, transcript="无 key 洗帧测试")
    resp = client.post(f"/api/assets/{asset_id}/frame-candidates")
    assert resp.status_code == 409
    assert "VLM" in resp.json()["detail"]
    # 状态端点同步给前端禁用判据（同 asr/status 形态）
    status = client.get("/api/clips/frames/status")
    assert status.status_code == 200
    assert status.json() == {"configured": False}


def test_frame_candidates_reject_legacy_text_clip(api: ApiFixture) -> None:
    """旧时间码文本切片（键 .txt、字节非 mp4）不可洗帧：422 入口说清。"""
    client, _ = api
    _login(client)
    candidate_id = _insert_candidate(
        start="00:30:00", end="00:30:10", transcript="旧路径洗帧测试：无源录像"
    )
    picked = client.post("/api/clips/candidates/pick", json={"ids": [candidate_id]})
    assert picked.status_code == 200
    asset_id = int(picked.json()[0]["id"])
    assert client.post(f"/api/assets/{asset_id}/publish").status_code == 200
    resp = client.post(f"/api/assets/{asset_id}/frame-candidates")
    assert resp.status_code == 422
    assert "mp4" in resp.json()["detail"]


# ---------- 2. 候选生成（VLM 替身 + 真 ffmpeg） ----------


def test_frame_candidates_sampling_scoring_and_shape(
    api: ApiFixture, live_mp4: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = api
    _login(client)
    asset_id = _make_published_video(client, live_mp4, transcript="洗帧候选形状测试")
    # 12s 视频 → 采样 [0, 5, 10]；替身按序给 8 / 3 / 7 分 → 候选 [0, 10]
    calls = _enable_vlm(
        monkeypatch,
        scores=[
            '{"score": 8, "note": "商品居中，画面清晰"}',
            '{"score": 3, "note": "转场模糊"}',
            '{"score": 7, "note": "特写完整"}',
        ],
    )
    resp = client.post(f"/api/assets/{asset_id}/frame-candidates")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert 11.5 < body["duration_seconds"] < 12.5  # ffprobe 真跑（容器精度）
    assert body["sampled"] == 3  # 采样三帧（含被淘汰的，回执如实）
    assert len(calls) == 3  # 每个采样帧都送了 VLM（真 ffmpeg 缩略字节）
    assert all(c.startswith(b"\xff\xd8\xff") for c in calls)  # 缩略图是真 jpeg
    assert [c["at_second"] for c in body["candidates"]] == [0.0, 10.0]
    assert [c["at_time"] for c in body["candidates"]] == ["00:00", "00:10"]
    assert [c["score"] for c in body["candidates"]] == [8, 7]
    assert body["candidates"][0]["note"] == "商品居中，画面清晰"
    for candidate in body["candidates"]:
        assert candidate["thumbnail_data_url"].startswith("data:image/jpeg;base64,")
    # 候选不落库（请求态）：库表无新行、无新对象键
    assert _fetch("SELECT count(*) FROM assets WHERE source_kind = 'clip_frame'") == [(0,)]


def test_frame_candidates_vlm_failure_502(
    api: ApiFixture, live_mp4: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = api
    _login(client)
    asset_id = _make_published_video(client, live_mp4, transcript="VLM 失败洗帧测试")

    def _boom(system_prompt: str, user_prompt: str, image_bytes: bytes) -> str:
        raise VLMUnavailable("看图服务暂时不可用")

    monkeypatch.setattr(vlm_service, "is_configured", lambda: True)
    monkeypatch.setattr(vlm_service, "chat_with_image", _boom)
    resp = client.post(f"/api/assets/{asset_id}/frame-candidates")
    assert resp.status_code == 502


# ---------- 3. 确认登记（人工闸门后的写路径） ----------


def test_register_frame_creates_image_asset_with_lineage(
    api: ApiFixture, live_mp4: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, storage_root = api
    _login(client)
    asset_id = _make_published_video(client, live_mp4, transcript="确认登记测试")
    _enable_vlm(
        monkeypatch,
        scores=['{"score": 9, "note": "清晰"}'],
        draft="VLM 草稿：画面是渐变色测试图案",
    )
    resp = client.post(f"/api/assets/{asset_id}/frames", json={"at_second": 5})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    frame_asset = body["asset"]
    assert frame_asset["kind"] == "image"
    assert frame_asset["source_kind"] == "clip_frame"
    assert frame_asset["status"] == "pending_review"  # 走 94a 待人洗，不自动发布
    # 标题形态：{商品名} · 实拍帧 mm:ss（源资产挂商品 → 基名=商品名）
    assert frame_asset["title"] == f"{_product_name()} · 实拍帧 00:05"
    assert frame_asset["product"]["id"] == _product_id()  # 挂商品透传
    # 回执血缘锚：切自 A-xxxx · v1（0047「回执指名切自哪份」同款）
    assert body["cut_from"] == f"A-{asset_id} · v1"

    detail = client.get(f"/api/assets/{frame_asset['id']}").json()
    version = detail["versions"][0]
    assert version["object_key"].endswith(".jpg")  # 键跟字节走
    raw = (storage_root / version["object_key"]).read_bytes()
    assert raw.startswith(b"\xff\xd8\xff")  # 字节是真 jpeg（全尺寸帧）
    # 94a 复用：描述草稿落 extracted（source=machine），待人洗
    assert version["extracted_fields"]["图片描述"] == {
        "value": "VLM 草稿：画面是渐变色测试图案",
        "source": "machine",
    }
    # 血缘视图 origin 环显示新来源词（0026 派生视图，零新字段）
    lineage = client.get(f"/api/assets/{frame_asset['id']}/lineage").json()
    assert lineage["origin"]["source_kind"] == "clip_frame"


def test_register_frame_rejects_non_video(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    doc = client.post(
        "/api/assets/register",
        files={"file": ("spec.txt", "净含量：550毫升".encode(), "text/plain")},
    )
    assert doc.status_code == 201
    resp = client.post(f"/api/assets/{doc.json()['id']}/frames", json={"at_second": 1})
    assert resp.status_code == 422


# ---------- 4. 全链：确认 → 人洗 → 发布 → 顾客命中引用（94b 出图） ----------


def test_frame_asset_full_chain_to_customer_citation(
    api: ApiFixture, live_mp4: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = api
    _login(client)
    asset_id = _make_published_video(
        client, live_mp4, transcript="全链测试：渐变色测试图案展示。"
    )
    _enable_vlm(monkeypatch, scores=['{"score": 9, "note": "清晰"}'], draft="草稿")
    registered = client.post(f"/api/assets/{asset_id}/frames", json={"at_second": 0})
    assert registered.status_code == 201, registered.text
    frame_id = int(registered.json()["asset"]["id"])

    # 人洗：确认图片描述（描述=图片的检索文本面，索引只认 confirmed）
    patched = client.patch(
        f"/api/assets/{frame_id}/versions/1/fields", json={"图片描述": _DESCRIPTION}
    )
    assert patched.status_code == 200, patched.text
    assert client.post(f"/api/assets/{frame_id}/publish").status_code == 200

    # 顾客问图片内容词：命中该帧资产带引用；94b 媒体引用同步出图
    complete = _ask(client, "有渐变色测试图案的展示帧吗")
    cited = [c["asset_id"] for c in complete["citations"]]
    assert frame_id in cited, f"问句未命中洗帧资产: {complete}"
    media_ids = [(m["asset_id"], m["mime"]) for m in complete["media_citations"]]
    assert (frame_id, "image/jpeg") in media_ids
