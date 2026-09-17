"""第 98b 刀：内容成片纯函数与合成单测（选材/时长/时间线/滤镜/草稿包）。

产品语义的钉子（ADR 0056）：改任何默认值都该在这里先红。预览合成用**真
ffmpeg**（CI 与镜像同装 fonts-noto-cjk，本机 Windows 走系统字体）——2 图+1
切片+1 文案卡的短合成，ffprobe 断言时长与流；AIGC 水印钉在 filter 命令断言
（drawtext 常驻、无开关——红线①）。TTS 走替身（不打真网）。
"""

import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from suite_api.services import video_compose as vc
from suite_api.services.video_compose import (
    ClipOption,
    ComposeManifest,
    ImageOption,
    build_draft_zip,
    build_preview_filter,
    build_timeline,
    clip_seconds,
    compose_title,
    estimate_clip_seconds,
    rank_clips,
    timeline_content,
    validate_template,
)

ffmpeg_available = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

# ---------- 模板校验 ----------


def test_validate_template_accepts_two_keys() -> None:
    assert validate_template("highlight") == "highlight"
    assert validate_template("product_intro") == "product_intro"


def test_validate_template_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="成片模板"):
        validate_template("dsl_template")


# ---------- 时长估算 ----------


def test_estimate_clip_seconds_clamps_to_window() -> None:
    assert estimate_clip_seconds("") == vc.CLIP_MIN_SECONDS
    assert estimate_clip_seconds("三") == vc.CLIP_MIN_SECONDS  # 1 字 < 3s → 兜底 3
    long_text = "这一瓶矿泉水特别甘甜清爽解渴值得整箱囤" * 3  # 60 字 → 60/4.5≈13s → 截 8
    assert estimate_clip_seconds(long_text) == vc.CLIP_MAX_SECONDS
    mid = estimate_clip_seconds("这一瓶矿泉水特别甘甜")  # 10 字 / 4.5 ≈ 2.2 → 兜底 3
    assert mid == vc.CLIP_MIN_SECONDS


def test_clip_seconds_prefers_measured() -> None:
    option = ClipOption(1, 1, "clips/x.mp4", "转写", duration_seconds=25.0)
    assert clip_seconds(option) == vc.CLIP_MAX_SECONDS  # 实测 25s → 截前 8s
    assert clip_seconds(ClipOption(1, 1, "clips/x.mp4", "二十个字以上的转写文本用来估算时长")) > 0


# ---------- 切片排序（高光=讲到这件商品的优先） ----------


def _clip(asset_id: int, transcript: str) -> ClipOption:
    return ClipOption(asset_id, 1, f"clips/{asset_id}.mp4", transcript)


def test_rank_clips_matched_first_stable() -> None:
    clips = [
        _clip(1, "今天给大家看看别的东西"),
        _clip(2, "这瓶装水是 316 不锈钢内胆"),
        _clip(3, "瓶装水整箱 24 瓶更划算"),
        _clip(4, "随便聊聊天气"),
    ]
    ranked = rank_clips(clips, "瓶装水", ("316 不锈钢",))
    assert [c.asset_id for c in ranked[:2]] == [2, 3]  # 命中商品名/卖点词的先入选
    assert [c.asset_id for c in ranked[2:]] == [1, 4]  # 未命中的殿后（保持原序）


# ---------- 选材+排版主函数 ----------


def _manifest(
    clips: list[ClipOption] | None = None,
    images: list[ImageOption] | None = None,
    texts: list[tuple[int, str]] | None = None,
) -> ComposeManifest:
    return ComposeManifest(
        product_name="瓶装水",
        selling_points=("480ml",),
        clips=tuple(clips or []),
        images=tuple(images or []),
        text_points=tuple(texts or []),
    )


def test_build_timeline_highlight_leads_with_clips() -> None:
    clips = [_clip(10, "瓶装水整箱 24 瓶"), _clip(11, "瓶装水甘甜")]
    images = [ImageOption(20, 1, "documents/a.png", "商品图")]
    texts = [(30, "瓶装水 · 一整箱更划算")]
    plan = build_timeline(_manifest(clips, images, texts), "highlight")
    types = [item.type for item in plan.timeline]
    assert types[0] == "clip"  # 高光=切片打头
    assert "image" in types and "text" in types
    # butt-joint：start 逐项累加，无重叠无空洞
    cursor = 0.0
    for item in plan.timeline:
        assert item.start == pytest.approx(cursor, abs=0.01)
        cursor += item.dur
    assert plan.duration_seconds == pytest.approx(cursor, abs=0.01)


def test_build_timeline_min_duration_padded_by_images_texts() -> None:
    # 只有 1 图 1 文案（4+3.5=7.5s < 15）：拉长图/文案卡补到 15，切片不注水
    plan = build_timeline(
        _manifest(images=[ImageOption(1, 1, "documents/a.png", "")], texts=[(2, "要点")]),
        "product_intro",
    )
    assert plan.duration_seconds >= vc.TARGET_MIN_SECONDS - 0.01
    assert all(item.dur <= vc.IMAGE_SECONDS + 6 for item in plan.timeline)  # 拉长有限


def test_build_timeline_insufficient_clips_note_honest() -> None:
    # 只有 1 条 8s 切片（无图无文案可拉长）：不硬凑，note 如实说明不足 15s
    plan = build_timeline(
        _manifest(clips=[_clip(9, "很长的转写" * 10)]), "highlight"
    )
    assert plan.duration_seconds < vc.TARGET_MIN_SECONDS
    assert plan.note and "不足 15s" in plan.note


def test_build_timeline_caps_at_60s_by_dropping_tail() -> None:
    clips = [_clip(i, f"瓶装水卖点讲解第{i}段这是很长的转写" * 3) for i in range(1, 9)]
    images = [ImageOption(100 + i, 1, f"documents/{i}.png", "") for i in range(4)]
    texts = [(200 + i, f"瓶装水要点{i}") for i in range(5)]
    plan = build_timeline(_manifest(clips, images, texts), "highlight")
    assert plan.duration_seconds <= vc.TARGET_MAX_SECONDS + 0.01
    assert plan.note and "60s" in plan.note  # 裁了要如实说
    # 让位序：先裁文案（一条不剩）、图让位、切片最后动
    types = {item.type for item in plan.timeline}
    assert "clip" in types


def test_build_timeline_product_intro_interleaves_text_image() -> None:
    images = [ImageOption(1, 1, "documents/1.png", ""), ImageOption(2, 1, "documents/2.png", "")]
    texts = [(3, "开场要点"), (4, "卖点要点")]
    plan = build_timeline(_manifest(images=images, texts=texts), "product_intro")
    types = [item.type for item in plan.timeline]
    # 图文交替：两类各 2 条且不相邻连排（interleave 图先出：image,text,image,text）
    assert types == ["image", "text", "image", "text"]


def test_build_timeline_no_material_raises() -> None:
    with pytest.raises(vc.NoMaterialError, match="没有已发布的切片"):
        build_timeline(_manifest(), "highlight")


def test_build_timeline_fill_with_images_texts_when_clips_missing() -> None:
    # 无切片：图+文案补足（不足额如实 note）
    plan = build_timeline(
        _manifest(images=[ImageOption(1, 1, "documents/1.png", "")], texts=[(2, "要点一")]),
        "highlight",
    )
    assert plan.note and "没有已发布切片" in plan.note


def test_timeline_item_shape() -> None:
    plan = build_timeline(_manifest(texts=[(5, "要点")], images=[ImageOption(6, 1, "d/a.png", "")]),
                          "product_intro")
    for item in plan.timeline:
        data = item.to_json()
        assert data["type"] in ("clip", "image", "text")
        assert isinstance(data["asset_id"], int)
        assert data["dur"] > 0 and data["start"] >= 0
        if data["type"] == "text":
            assert data["text"]


# ---------- publish 文案面 ----------


def test_compose_title_preset() -> None:
    assert compose_title("瓶装水") == "瓶装水 · 内容成片"


def test_timeline_content_joins_text_points() -> None:
    timeline = [
        {"type": "clip", "asset_id": 1, "start": 0, "dur": 5},
        {"type": "text", "asset_id": 2, "start": 5, "dur": 3.5, "text": "瓶装水整箱更划算"},
        {"type": "text", "asset_id": 2, "start": 8.5, "dur": 3.5, "text": "316 不锈钢内胆"},
    ]
    assert timeline_content(timeline) == "瓶装水整箱更划算\n316 不锈钢内胆"


def test_timeline_content_falls_back_to_clips() -> None:
    timeline = [{"type": "clip", "asset_id": 1, "start": 0, "dur": 5, "text": "瓶装水甘甜"}]
    assert "瓶装水甘甜" in timeline_content(timeline)


# ---------- 滤镜命令断言（水印红线①/字幕/叠化） ----------


def _render_items() -> list[dict]:
    return [
        {"type": "image", "input_index": 0, "start": 0.0, "dur": 4.0, "path": "a.png"},
        {"type": "text", "input_index": 1, "start": 4.0, "dur": 3.5, "text": "瓶装水 · 整箱更划算"},
        {"type": "clip", "input_index": 2, "start": 7.5, "dur": 5.0, "path": "c.mp4"},
    ]


def test_filter_contains_watermark_subtitles_and_crossfade(tmp_path: Path) -> None:
    graph = build_preview_filter(
        _render_items(), total=12.5, has_audio=False, audio_input=None,
        workdir=tmp_path, font_file="C:/Windows/Fonts/msyh.ttc",
    )
    # 红线①：AIGC 水印 drawtext 常驻（无 enable 窗口限制=全程），位置右上角
    assert "watermark.txt" in graph
    assert "x=w-tw-24:y=24" in graph
    assert "enable" not in graph.split("watermark.txt")[1].split("[vout]")[0]
    # 字幕按窗口 enable
    assert "enable='between(t,4.000,7.500)'" in graph
    # 图→文案卡（相邻非切片）有 xfade 叠化；offset=文案卡的时线起点 4.0
    assert "xfade=transition=fade" in graph and "offset=4.000" in graph
    # 切片与任何边界硬拼：只有一处 xfade（image-text），text-clip 走 concat
    assert graph.count("xfade=") == 1
    assert "concat=n=2:v=1:a=0" in graph
    # 字幕/卡片文本走 textfile（免转义），路径「单引号+转义盘符冒号」（Windows）
    assert "fontfile='C\\:/Windows/Fonts/msyh.ttc'" in graph


def test_filter_audio_chain_only_when_present(tmp_path: Path) -> None:
    silent = build_preview_filter(
        _render_items(), total=12.5, has_audio=False, audio_input=None,
        workdir=tmp_path, font_file="font.ttf",
    )
    assert "[aout]" not in silent and "apad" not in silent
    voiced = build_preview_filter(
        _render_items(), total=12.5, has_audio=True, audio_input=3,
        workdir=tmp_path, font_file="font.ttf",
    )
    assert "[3:a]aresample=44100,apad,atrim=duration=12.500,asetpts=PTS-STARTPTS[aout]" in voiced


# ---------- 草稿包结构（自写最小 JSON 形态） ----------


def _draft_items() -> list[dict]:
    return [
        {"type": "image", "start": 0.0, "dur": 4.0, "text": None,
         "draft_name": "materials/image-A20-v1.png", "bytes": b"PNGDATA",
         "width": 800, "height": 600, "duration": 4.0},
        {"type": "text", "start": 4.0, "dur": 3.5, "text": "瓶装水 · 整箱更划算",
         "draft_name": None, "bytes": None, "width": 0, "height": 0, "duration": 0.0},
        {"type": "clip", "start": 7.5, "dur": 5.0, "text": None,
         "draft_name": "materials/clip-A264-v1.mp4", "bytes": b"MP4DATA",
         "width": 1280, "height": 720, "duration": 5.0},
    ]


def test_draft_zip_structure_and_relative_paths() -> None:
    payload = build_draft_zip(_draft_items(), total=12.5, draft_name="瓶装水 · 内容成片",
                              tts_bytes=b"ID3FAKEDATA")
    with zipfile.ZipFile(__import__("io").BytesIO(payload)) as archive:
        names = archive.namelist()
        assert "draft_content.json" in names and "draft_meta_info.json" in names
        assert "materials/image-A20-v1.png" in names
        assert "materials/clip-A264-v1.mp4" in names
        assert "materials/tts.mp3" in names
        content = json.loads(archive.read("draft_content.json"))
        meta = json.loads(archive.read("draft_meta_info.json"))
    # 素材 path 是包内相对路径（下载到操作者机器可解析/可重链接）
    video_paths = [v["path"] for v in content["materials"]["videos"]]
    assert video_paths == ["materials/image-A20-v1.png", "materials/clip-A264-v1.mp4"]
    assert content["materials"]["audios"][0]["path"] == "materials/tts.mp3"
    # 微秒时基：主轨=媒体项按时间线定位（text 项走文本轨，主轨留空隙待人补）
    seg = content["tracks"][0]["segments"]
    assert [s["target_timerange"]["start"] for s in seg] == [0, 7_500_000]
    assert seg[1]["target_timerange"]["duration"] == 5_000_000
    # 文本轨第一段=文案要点字幕（窗口 4.0-7.5s）
    subtitle_segment = content["tracks"][1]["segments"][0]
    assert subtitle_segment["target_timerange"] == {"start": 4_000_000, "duration": 3_500_000}
    assert content["duration"] == 12_500_000 and content["fps"] == 30
    # 文本轨：1 条文案要点字幕 + AIGC 水印常驻（红线①随草稿走）
    texts = content["materials"]["texts"]
    assert len(texts) == 2
    assert json.loads(texts[1]["content"])["text"] == vc.WATERMARK_TEXT
    watermark_segment = content["tracks"][1]["segments"][-1]
    assert watermark_segment["target_timerange"] == {"start": 0, "duration": 12_500_000}
    # meta：草稿名/时长
    assert meta["draft_name"] == "瓶装水 · 内容成片"
    assert meta["tm_duration"] == 12_500_000
    assert len(meta["draft_id"]) == 36  # UUID 形


def test_draft_zip_without_tts_has_no_audio_track() -> None:
    payload = build_draft_zip(_draft_items(), total=12.5, draft_name="x", tts_bytes=None)
    with zipfile.ZipFile(__import__("io").BytesIO(payload)) as archive:
        content = json.loads(archive.read("draft_content.json"))
    assert content["materials"]["audios"] == []
    assert all(track["type"] != "audio" for track in content["tracks"])


# ---------- 真 ffmpeg 合成（时长/流/AIGC 帧存在性） ----------


@pytest.fixture(scope="module")
def media(tmp_path_factory: pytest.TempPathFactory) -> dict:
    workdir = tmp_path_factory.mktemp("compose-media")
    png_path = workdir / "img.png"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "color=c=red:s=320x240:d=1",
         "-frames:v", "1", str(png_path)],
        check=True, capture_output=True,
    )
    clip_path = workdir / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc=duration=6:size=320x240:rate=15",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip_path)],
        check=True, capture_output=True,
    )
    return {"png": str(png_path), "mp4": str(clip_path), "dir": workdir}


@pytest.mark.skipif(not ffmpeg_available, reason="需要本机 ffmpeg/ffprobe")
class TestRealRender:
    """真合成：2 图 + 1 切片 + 1 文案卡的 15s 成片（预览契约的最小代表）。"""

    def _items(self, media: dict) -> list[dict]:
        return [
            {"type": "image", "input_index": 0, "start": 0.0, "dur": 4.0, "path": media["png"]},
            {"type": "text", "input_index": 1, "start": 4.0, "dur": 4.0, "text": "瓶装水整箱更划算"},
            {"type": "clip", "input_index": 2, "start": 8.0, "dur": 4.0, "path": media["mp4"]},
        ]

    def test_render_without_tts_produces_silent_mp4(self, media: dict, tmp_path: Path) -> None:
        payload = vc.render_preview(
            self._items(media), None, total=12.0, workdir=tmp_path,
            font_file=vc._find_cjk_font(),
        )
        out = tmp_path / "probe.mp4"
        out.write_bytes(payload)
        duration, has_audio = _ffprobe_streams(out)
        assert duration == pytest.approx(12.0, abs=0.3)
        assert has_audio is False  # 无 TTS=无声预览（with_tts=false 的字节面）

    def test_render_with_tts_produces_audio_stream(self, media: dict, tmp_path: Path) -> None:
        tts = tmp_path / "tts.mp3"
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=8",
             "-c:a", "libmp3lame", "-q:a", "5", str(tts)],
            check=True, capture_output=True,
        )
        payload = vc.render_preview(
            self._items(media), tts.read_bytes(), total=12.0, workdir=tmp_path,
            font_file=vc._find_cjk_font(),
        )
        out = tmp_path / "probe.mp4"
        out.write_bytes(payload)
        duration, has_audio = _ffprobe_streams(out)
        assert duration == pytest.approx(12.0, abs=0.3)
        assert has_audio is True  # 画面时长权威：口播 8s→apad 补到 12s

    def test_watermark_pixels_present_top_right(self, media: dict, tmp_path: Path) -> None:
        """AIGC 角标抽帧验证：右上角水印区非纯背景（红底图上白字+黑框=高亮度差）。"""
        payload = vc.render_preview(
            self._items(media), None, total=12.0, workdir=tmp_path,
            font_file=vc._find_cjk_font(),
        )
        out = tmp_path / "probe.mp4"
        out.write_bytes(payload)
        frame = tmp_path / "frame.png"
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-ss", "1.0", "-i", str(out), "-frames:v", "1", str(frame)],
            check=True, capture_output=True,
        )
        pixels = _corner_pixels(frame)
        assert pixels, "帧抽取失败"
        # 纯红背景 (r>180, g<80, b<80) 上有水印（白字/黑框）→ 会出现非背景像素
        background = [p for p in pixels if p[0] > 150 and p[1] < 100 and p[2] < 100]
        assert len(pixels) - len(background) > 20, "右上角未见 AIGC 水印像素"


def _ffprobe_streams(path: Path) -> tuple[float, bool]:
    completed = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration", "-show_entries", "stream=codec_type", "-of", "json", str(path)],
        check=True, capture_output=True,
    )
    data = json.loads(completed.stdout.decode())
    duration = float(data["format"]["duration"])
    has_audio = any(s.get("codec_type") == "audio" for s in data["streams"])
    return duration, has_audio


def _corner_pixels(path: Path) -> list[tuple[int, int, int]]:
    """右上角水印区（x>0.86w, y<0.08h）的像素列表（ffmpeg 提 rawvideo RGB24）。"""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "frame.rgb"
        info = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "json", str(path)],
            check=True, capture_output=True,
        )
        meta = json.loads(info.stdout.decode())["streams"][0]
        width, height = int(meta["width"]), int(meta["height"])
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-i", str(path), "-vf", "format=rgb24", "-frames:v", "1",
             "-f", "rawvideo", str(raw)],
            check=True, capture_output=True,
        )
        data = raw.read_bytes()
    pixels: list[tuple[int, int, int]] = []
    for y in range(0, max(1, height // 12)):
        for x in range(int(width * 0.85), width):
            offset = (y * width + x) * 3
            pixels.append((data[offset], data[offset + 1], data[offset + 2]))
    return pixels
