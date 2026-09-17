"""第 98b 刀：内容成片集成测试（真 PG + 真 ffmpeg；ADR 0056）。

契约：
- plan：商品 404/模板坏值 422/无已发布素材 422；真跑选材→时间线→预览合成
  （ffprobe 断言时长与流）→草稿 zip（结构+相对路径）→ planned 任务落库；
- TTS 三态：无 key=无声预览（with_tts=false、note 如实）；替身成功=有声流；
  替身失败=无声且不 fail 任务；
- publish 双闸复用（红线③）：LLM 未配置 fail-closed 422；规则闸（正文缺
  商品名）422；LLM 判定不过 422；过线登记 material 资产（kind=material、
  source_kind=upload、标题「{商品} · 内容成片」、挂商品）→ 治理台待人洗；
  成品 mp4 上传留档（final 端点可下）；registered 再 publish 422。
- publish CAS 占位（审计 19）：双闸失败回 planned 可重试；并发双 publish 的
  后来者 409「任务已被确认」；登记成功后清 preview/draft 暂存（final 留档）。
- 全部操作者鉴权（401 未登录）。

TTS/LLM 用替身（monkeypatch services.tts/material 的函数符号）——不打真网；
ffprobe/ffmpeg、DB 落值、端点码位全走真路径。真 TTS 验收由 Owner 带 key 补
（closeout 记）。
"""

import io
import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

import suite_api.services.material as material_module
import suite_api.services.tts as tts_module
from suite_api.services.video_compose import WATERMARK_TEXT

ApiFixture = tuple[TestClient, Path]

_URL_ENV = "SUITE_TEST_DATABASE_URL"

QC_PASS = '{"passed": true, "issues": []}'

MATERIAL_TITLE = "矿泉水：一整箱更划算"
MATERIAL_CONTENT = (
    "矿泉水天然低钠淡矿，整箱 24 瓶更划算。\n"
    "矿泉水瓶身轻但抗压，常温避光存放。\n"
    "净含量：480ml"
)


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


def _new_product(client: TestClient, name: str) -> int:
    created = client.post("/api/products", json={"name": name, "category": "食品"})
    assert created.status_code == 201, created.text
    return int(created.json()["id"])


def _make_png() -> bytes:
    """现造测试 PNG（纯红单帧——水印角标的像素断言以它为背景）。"""
    path = Path(__import__("tempfile").mkdtemp(prefix="compose-png-")) / "img.png"
    subprocess.run(  # noqa: S603 - 固定参数，无 shell
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "color=c=red:s=320x240:d=1",
         "-frames:v", "1", str(path)],
        check=True, capture_output=True,
    )
    return path.read_bytes()


def _make_mp4(seconds: int = 12) -> bytes:
    """现造测试 mp4（testsrc，短视频足够当切片源/成品上传件）。"""
    path = Path(__import__("tempfile").mkdtemp(prefix="compose-it-")) / "live.mp4"
    subprocess.run(  # noqa: S603 - 固定参数，无 shell
        ["ffmpeg", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10",
         "-t", str(seconds), "-pix_fmt", "yuv420p", "-y", str(path)],
        check=True, capture_output=True,
    )
    return path.read_bytes()


def _make_tts_mp3(seconds: int = 6) -> bytes:
    path = Path(__import__("tempfile").mkdtemp(prefix="compose-tts-")) / "tts.mp3"
    subprocess.run(  # noqa: S603 - 固定参数，无 shell
        ["ffmpeg", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-c:a", "libmp3lame", "-q:a", "5", "-y", str(path)],
        check=True, capture_output=True,
    )
    return path.read_bytes()


def _insert_candidate(product_id: int, *, start: str, end: str, transcript: str) -> int:
    with psycopg.connect(_url()) as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO clip_candidates
                (product_id, status, timecode_start, timecode_end, transcript,
                 source_video_label)
            VALUES (%s, 'pending', %s, %s, %s, %s)
            RETURNING id
            """,
            (product_id, start, end, transcript, "成片测试录像"),
        )
        return int(cur.fetchone()[0])


def _make_published_clip(client: TestClient, product_id: int, transcript: str) -> int:
    """上传源录像 → 插候选 → 拣选（真 mp4 切片+预置 transcript）→ 发布。"""
    candidate_id = _insert_candidate(product_id, start="00:00:00", end="00:00:12",
                                     transcript=transcript)
    uploaded = client.post(
        "/api/clips/recordings", files={"file": ("compose.mp4", _make_mp4(), "video/mp4")}
    )
    assert uploaded.status_code == 201, uploaded.text
    picked = client.post("/api/clips/candidates/pick", json={"ids": [candidate_id]})
    assert picked.status_code == 200, picked.text
    asset_id = int(picked.json()[0]["id"])
    assert client.post(f"/api/assets/{asset_id}/publish").status_code == 200
    return asset_id


def _make_published_image(client: TestClient, product_id: int) -> int:
    """登记图片 → 人洗确认图片描述 → 发布。"""
    created = client.post(
        "/api/assets/register",
        files={"file": ("shot.png", _make_png(), "image/png")},
        data={"productId": str(product_id), "title": "矿泉水商品图"},
    )
    assert created.status_code == 201, created.text
    asset = created.json()
    patched = client.patch(
        f"/api/assets/{asset['id']}/versions/1/fields",
        json={"图片描述": "矿泉水整箱展示图"},
    )
    assert patched.status_code == 200, patched.text
    assert client.post(f"/api/assets/{asset['id']}/publish").status_code == 200
    return int(asset["id"])


def _make_published_material(client: TestClient, product_id: int) -> int:
    """素材任务（LLM 生成+质检两连替身）→ 抽检通过 → 发布。fixture 内自管替换。"""
    from suite_api.services import llm as llm_module

    responses = [
        json.dumps({"title": MATERIAL_TITLE, "content": MATERIAL_CONTENT}, ensure_ascii=False),
        QC_PASS,
    ]

    async def fake_chat(system_prompt: str, user_prompt: str) -> str:
        del system_prompt, user_prompt
        assert responses, "意外的额外 LLM 调用"
        return responses.pop(0)

    original = llm_module.complete_chat
    llm_module.complete_chat = fake_chat
    try:
        created = client.post("/api/material/tasks", json={"product_id": product_id})
        assert created.status_code == 201, created.text
        task_id = created.json()["id"]
        assert created.json()["status"] == "pending_qc", created.text
        approved = client.post(f"/api/material/tasks/{task_id}/approve")
        assert approved.status_code == 200, approved.text
        asset_id = approved.json()["asset_id"]
    finally:
        llm_module.complete_chat = original
    assert client.post(f"/api/assets/{asset_id}/publish").status_code == 200
    return int(asset_id)


def _patch_llm_qc(monkeypatch: pytest.MonkeyPatch, result: str) -> list[str]:
    """替身 LLM 质检（publish 二道闸复用点）：捕获 prompt、回固定判定。"""
    prompts: list[str] = []

    def fake_qc(title: str, content: str, product: object) -> tuple[bool, list[str]]:
        prompts.append(content)
        data = json.loads(result)
        return bool(data["passed"]), list(data["issues"])

    monkeypatch.setattr(material_module, "run_llm_qc", fake_qc)
    return prompts


def _ffprobe(path: Path) -> dict:
    completed = subprocess.run(  # noqa: S603 - 固定参数，无 shell
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration", "-show_entries", "stream=codec_type,width,height",
         "-of", "json", str(path)],
        check=True, capture_output=True,
    )
    return json.loads(completed.stdout.decode())


@pytest.fixture(scope="module")
def needs_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("需要 ffmpeg/ffprobe（预览合成与测试素材生成；本机/CI 自带）")


@pytest.fixture(scope="module")
def rich_product(api: ApiFixture, needs_ffmpeg: None) -> int:
    """「三材齐备」商品：已发布切片（转写含商品名）+ 图（带描述）+ material 文案。"""
    client, _ = api
    _login(client)
    product_id = _new_product(client, "矿泉水")
    _make_published_clip(client, product_id, "这矿泉水是海拔 3800 米的天然低钠淡矿，整箱 24 瓶更划算")
    _make_published_image(client, product_id)
    _make_published_material(client, product_id)
    return product_id


# ---------- 闸门码位 ----------


def test_requires_login(api: ApiFixture) -> None:
    client, _ = api
    client.cookies.clear()
    assert client.post("/api/video-compose/plan", json={"product_id": 1}).status_code == 401
    assert client.get("/api/video-compose/1/preview").status_code == 401
    assert client.post("/api/video-compose/1/publish").status_code == 401


def test_plan_bad_template_and_missing_product(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    assert client.post(
        "/api/video-compose/plan", json={"product_id": 1, "template": "nope"}
    ).status_code == 422
    assert client.post(
        "/api/video-compose/plan", json={"product_id": 10**9}
    ).status_code == 404


def test_plan_without_published_material_422(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    empty_id = _new_product(client, "成片测试·空商品")
    resp = client.post("/api/video-compose/plan", json={"product_id": empty_id})
    assert resp.status_code == 422
    assert "没有已发布的切片" in resp.json()["detail"]


# ---------- 主链路：plan → 预览/草稿 → publish（无 TTS 形态） ----------


def test_full_loop_plan_preview_draft_publish(
    api: ApiFixture, rich_product: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = api
    # 无替身：conftest 空 TTS key → 无声预览（with_tts=false 如实）
    planned = client.post(
        "/api/video-compose/plan",
        json={"product_id": rich_product, "template": "product_intro"},
    )
    assert planned.status_code == 201, planned.text
    task = planned.json()
    task_id = task["id"]
    assert task["status"] == "planned"
    assert task["template_name"] == "商品介绍"
    assert task["with_tts"] is False
    assert task["note"] and "TTS 未配置" in task["note"]

    # 时间线形状：clip（真实切片）+ image + text 三类齐备、butt-joint、15-60s
    timeline = task["timeline"]
    types = [item["type"] for item in timeline]
    assert "clip" in types and "image" in types and "text" in types
    cursor = 0.0
    for item in timeline:
        assert item["start"] == pytest.approx(cursor, abs=0.02)
        cursor += item["dur"]
    assert 15.0 <= task["duration_seconds"] <= 60.0

    # 预览成片：mp4 字节 + ffprobe 时长≈回执时长 + 无音轨 + AIGC 水印角标像素
    preview = client.get(f"/api/video-compose/{task_id}/preview")
    assert preview.status_code == 200
    assert preview.headers["content-type"] == "video/mp4"
    probe_path = Path(__import__("tempfile").mkdtemp()) / "preview.mp4"
    probe_path.write_bytes(preview.content)
    meta = _ffprobe(probe_path)
    assert float(meta["format"]["duration"]) == pytest.approx(
        task["duration_seconds"], abs=0.5
    )
    assert not any(s.get("codec_type") == "audio" for s in meta["streams"])
    assert _corner_has_watermark(probe_path)
    # 审计 19 P2-5：像素钉对水印窗口化盲（首帧在窗内才验）——补一帧**非首帧**
    # 的水印像素证据：取时间线里最后一张文案卡（深底色画面，非首素材）窗口
    # 中点抽帧，角标仍在（红线①「常驻」到字节面，drawtext 无 enable= 窗口）。
    last_text = max(
        (item for item in timeline if item["type"] == "text"), key=lambda i: i["start"]
    )
    assert last_text["start"] > 0, "rich_product 时间线应有中段文案卡"
    assert _corner_has_watermark(
        probe_path, at=last_text["start"] + last_text["dur"] / 2
    )

    # 剪映草稿 zip：结构 + 媒体字节随包 + 相对路径
    draft = client.get(f"/api/video-compose/{task_id}/draft")
    assert draft.status_code == 200
    assert draft.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(draft.content)) as archive:
        names = archive.namelist()
        assert "draft_content.json" in names and "draft_meta_info.json" in names
        media = [n for n in names if n.startswith("materials/")]
        assert media, "草稿包应内嵌素材字节"
        content = json.loads(archive.read("draft_content.json"))
        assert all(v["path"].startswith("materials/") for v in content["materials"]["videos"])
        texts = [json.loads(t["content"])["text"] for t in content["materials"]["texts"]]
        assert WATERMARK_TEXT in texts  # 红线①随草稿走
    assert content["duration"] == int(round(task["duration_seconds"] * 1_000_000))

    # publish 无 LLM（conftest 空凭证）：fail-closed 422，任务停在 planned
    denied = client.post(f"/api/video-compose/{task_id}/publish")
    assert denied.status_code == 422
    assert "质检" in denied.json()["detail"]

    # publish 过线（LLM 质检替身）：登记 material 资产（source_kind=upload 定值）
    prompts = _patch_llm_qc(monkeypatch, QC_PASS)
    published = client.post(
        f"/api/video-compose/{task_id}/publish",
        files={"final_video": ("final.mp4", _make_mp4(3), "video/mp4")},
    )
    assert published.status_code == 200, published.text
    body = published.json()
    assert body["task"]["status"] == "registered"
    assert body["task"]["has_final_video"] is True
    # 双闸复用：LLM 质检拿到的正文含文案要点（AI 排的版、人确认的稿）
    assert any("矿泉水" in p for p in prompts)

    asset = client.get(f"/api/assets/{body['asset_id']}").json()
    assert asset["kind"] == "material"
    assert asset["source_kind"] == "upload"  # 服务端定值（ADR 0056）
    assert asset["status"] == "pending_review"  # 待人洗：治理台可见
    assert asset["title"] == "矿泉水 · 内容成片"
    assert asset["product"]["id"] == rich_product

    # 成品留档可下；registered 是终态：再 publish 422
    final = client.get(f"/api/video-compose/{task_id}/final")
    assert final.status_code == 200 and final.headers["content-type"] == "video/mp4"
    assert client.post(f"/api/video-compose/{task_id}/publish").status_code == 422

    # 任务列表与详情
    listed = client.get("/api/video-compose/tasks").json()
    assert listed[0]["id"] == task_id
    assert client.get(f"/api/video-compose/tasks/{task_id}").json()["status"] == "registered"


def test_publish_without_upload_uses_preview_and_no_final(
    api: ApiFixture, rich_product: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = api
    task_id = client.post(
        "/api/video-compose/plan", json={"product_id": rich_product, "template": "highlight"}
    ).json()["id"]
    assert client.get(f"/api/video-compose/{task_id}/final").status_code == 404
    _patch_llm_qc(monkeypatch, QC_PASS)
    published = client.post(f"/api/video-compose/{task_id}/publish")
    assert published.status_code == 200
    assert published.json()["task"]["has_final_video"] is False  # 不传=认可预览


# ---------- publish 双闸（红线③） ----------


def test_publish_rule_gate_blocks_content_without_product_name(
    api: ApiFixture, needs_ffmpeg: None
) -> None:
    client, _ = api
    _login(client)
    product_id = _new_product(client, "成片测试·无名商品")
    # 只有一条转写不含商品名的切片：正文=转写串联 → 规则闸「正文必须含商品名」
    _make_published_clip(client, product_id, "今天随便聊聊天气还不错")
    task_id = client.post(
        "/api/video-compose/plan", json={"product_id": product_id, "template": "highlight"}
    ).json()["id"]
    denied = client.post(f"/api/video-compose/{task_id}/publish")
    assert denied.status_code == 422
    assert "商品名" in denied.json()["detail"]
    assert client.get(f"/api/video-compose/tasks/{task_id}").json()["status"] == "planned"


def test_publish_llm_gate_blocks_contradiction(
    api: ApiFixture, rich_product: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = api
    task_id = client.post(
        "/api/video-compose/plan", json={"product_id": rich_product}
    ).json()["id"]
    _patch_llm_qc(
        monkeypatch, '{"passed": false, "issues": ["正文称净含量 990ml，与规格 480ml 矛盾"]}'
    )
    denied = client.post(f"/api/video-compose/{task_id}/publish")
    assert denied.status_code == 422
    assert "990ml" in denied.json()["detail"]
    assert client.get(f"/api/video-compose/tasks/{task_id}").json()["status"] == "planned"


# ---------- publish CAS 占位 + 暂存清理（审计 19） ----------


def test_publish_gate_failure_releases_claim_and_retry_succeeds(
    api: ApiFixture, rich_product: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """双闸失败分支的占位回滚：LLM 判定不过 → 任务回 planned（不是卡死在
    registering 瞬态），换一次过线判定即可重试登记成功。"""
    client, _ = api
    task_id = client.post(
        "/api/video-compose/plan", json={"product_id": rich_product}
    ).json()["id"]
    _patch_llm_qc(
        monkeypatch, '{"passed": false, "issues": ["正文与规格矛盾，不放行"]}'
    )
    denied = client.post(f"/api/video-compose/{task_id}/publish")
    assert denied.status_code == 422
    assert client.get(f"/api/video-compose/tasks/{task_id}").json()["status"] == "planned"

    _patch_llm_qc(monkeypatch, QC_PASS)
    published = client.post(f"/api/video-compose/{task_id}/publish")
    assert published.status_code == 200, published.text
    assert published.json()["task"]["status"] == "registered"


def test_concurrent_publish_second_claim_is_409(
    api: ApiFixture, rich_product: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """并发形态：读检查与 CAS 之间行被并发写者迁走（规则闸替身里用另一连接把
    行翻成 registered，模拟并发赢家已登记）——后来者条件更新 0 行 → 409
    「任务已被确认」，且后来者没有登记出任何 material 资产。"""
    client, _ = api
    task_id = client.post(
        "/api/video-compose/plan", json={"product_id": rich_product}
    ).json()["id"]

    def racing_qc(title: str, content: str, product_name: str) -> list[str]:
        del title, content, product_name
        with psycopg.connect(_url()) as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE compose_tasks SET status = 'registered' WHERE id = %s",
                (task_id,),
            )
            conn.commit()
        return []  # 规则闸放行，让流程走到 CAS

    monkeypatch.setattr(material_module, "qc_check", racing_qc)
    _patch_llm_qc(monkeypatch, QC_PASS)
    # 同名资产在早前用例已存在：断言「后来者没有新增登记」，不是「全库没有」
    assets_before = _fetch(
        "SELECT id FROM assets WHERE title = %s ORDER BY id", ("矿泉水 · 内容成片",)
    )
    loser = client.post(f"/api/video-compose/{task_id}/publish")
    assert loser.status_code == 409, loser.text
    assert "已被确认" in loser.json()["detail"]
    # 后来者零登记：任务没有资产锚、库里没有新增同名 material 资产
    assert _fetch("SELECT asset_id FROM compose_tasks WHERE id = %s", (task_id,)) == [
        (None,)
    ]
    assert _fetch(
        "SELECT id FROM assets WHERE title = %s ORDER BY id", ("矿泉水 · 内容成片",)
    ) == assets_before


def test_publish_success_cleans_preview_draft_staging_keeps_final(
    api: ApiFixture, rich_product: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """转正清理（审计 19）：登记成功后 preview/draft 暂存字节删除（端点 404、
    存储根下文件消失）；final 留任务档（ADR 0056）继续可下。"""
    client, storage_root = api
    task_id = client.post(
        "/api/video-compose/plan", json={"product_id": rich_product}
    ).json()["id"]
    preview_key, draft_key = _fetch(
        "SELECT preview_object_key, draft_object_key FROM compose_tasks WHERE id = %s",
        (task_id,),
    )[0]
    assert (storage_root / preview_key).is_file()
    assert (storage_root / draft_key).is_file()

    _patch_llm_qc(monkeypatch, QC_PASS)
    published = client.post(
        f"/api/video-compose/{task_id}/publish",
        files={"final_video": ("final.mp4", _make_mp4(3), "video/mp4")},
    )
    assert published.status_code == 200, published.text

    assert not (storage_root / preview_key).exists()
    assert not (storage_root / draft_key).exists()
    assert client.get(f"/api/video-compose/{task_id}/preview").status_code == 404
    assert client.get(f"/api/video-compose/{task_id}/draft").status_code == 404
    final_key = _fetch(
        "SELECT final_video_object_key FROM compose_tasks WHERE id = %s", (task_id,)
    )[0][0]
    assert final_key and (storage_root / final_key).is_file()
    assert client.get(f"/api/video-compose/{task_id}/final").status_code == 200


def test_publish_succeeds_even_if_staging_cleanup_fails(
    api: ApiFixture, rich_product: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """清理失败不 fail：storage.delete 抛错时登记已 commit——publish 照常 200、
    任务终态 registered（孤儿暂存键待存储侧兜底，只 log warning）。"""
    client, _ = api
    task_id = client.post(
        "/api/video-compose/plan", json={"product_id": rich_product}
    ).json()["id"]

    def broken_delete(self: object, key: str) -> None:
        del self, key
        raise OSError("存储下线（模拟清理失败）")

    from suite_platform.storage.local import LocalDirectoryStorage

    monkeypatch.setattr(LocalDirectoryStorage, "delete", broken_delete)
    _patch_llm_qc(monkeypatch, QC_PASS)
    published = client.post(f"/api/video-compose/{task_id}/publish")
    assert published.status_code == 200, published.text
    assert published.json()["task"]["status"] == "registered"


# ---------- TTS 三态 ----------


def test_tts_status_unconfigured(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    assert client.get("/api/video-compose/tts/status").json() == {"configured": False}


def test_plan_with_tts_stub_produces_audio(
    api: ApiFixture, rich_product: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = api
    mp3 = _make_tts_mp3(4)
    monkeypatch.setattr(tts_module, "synthesize_speech", lambda _text: mp3)
    planned = client.post(
        "/api/video-compose/plan", json={"product_id": rich_product}
    )
    assert planned.status_code == 201, planned.text
    task = planned.json()
    assert task["with_tts"] is True
    assert task["note"] is None or "TTS" not in (task["note"] or "")
    preview = client.get(f"/api/video-compose/{task['id']}/preview")
    probe_path = Path(__import__("tempfile").mkdtemp()) / "preview.mp4"
    probe_path.write_bytes(preview.content)
    meta = _ffprobe(probe_path)
    assert any(s.get("codec_type") == "audio" for s in meta["streams"])
    # 草稿包带口播轨
    with zipfile.ZipFile(io.BytesIO(client.get(f"/api/video-compose/{task['id']}/draft").content)) as archive:
        content = json.loads(archive.read("draft_content.json"))
    assert content["materials"]["audios"] and any(
        t["type"] == "audio" for t in content["tracks"]
    )


def test_plan_tts_failure_degrades_to_silent(
    api: ApiFixture, rich_product: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = api

    def broken(_text: str) -> bytes:
        raise tts_module.TTSUnavailable("TTS 服务暂时不可用")

    monkeypatch.setattr(tts_module, "synthesize_speech", broken)
    planned = client.post("/api/video-compose/plan", json={"product_id": rich_product})
    assert planned.status_code == 201  # 口播失败不 fail 任务（增值项）
    task = planned.json()
    assert task["with_tts"] is False
    assert task["note"] and "口播合成失败" in task["note"]


# ---------- 下载边界 ----------


def test_unknown_task_404(api: ApiFixture) -> None:
    client, _ = api
    _login(client)
    assert client.get("/api/video-compose/999999/preview").status_code == 404
    assert client.get("/api/video-compose/999999/draft").status_code == 404


def _corner_has_watermark(video: Path, *, at: float = 1.0) -> bool:
    """抽 ``at`` 秒帧（默认 1.0）：右上角水印区出现亮像素（黑 letterbox/深色
    画面上的白色「AI 生成」+ 半透明黑底框——红线①的字节面证据）。审计 19
    P2-5 起 ``at`` 可指定非首帧位（如中段文案卡窗口中点）。"""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        frame = Path(tmp) / "frame.rgb"
        meta = _ffprobe(video)
        stream = next(s for s in meta["streams"] if s.get("codec_type") == "video")
        width, height = int(stream["width"]), int(stream["height"])
        subprocess.run(  # noqa: S603 - 固定参数，无 shell
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-ss", str(at), "-i", str(video), "-frames:v", "1",
             "-vf", "format=rgb24", "-f", "rawvideo", str(frame)],
            check=True, capture_output=True,
        )
        data = frame.read_bytes()
    bright = 0
    for y in range(0, max(1, height // 12)):  # 水印区：y<8% 高、x>85% 宽
        for x in range(int(width * 0.85), width):
            offset = (y * width + x) * 3
            r, g, b = data[offset], data[offset + 1], data[offset + 2]
            if r > 160 and g > 160 and b > 160:
                bright += 1
    return bright > 20
