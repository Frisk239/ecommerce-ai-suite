"""第 94c 刀：直播洗帧纯函数单测（采样/打分解析/过滤/时间码/标题；无 PG 无 ffmpeg）。

第 112 刀补：``generate_candidates`` 的韧性三态（单帧 VLM 失败跳过 + failed_frames、
全帧失败抛 VLMUnavailable、NotConfigured 冒泡）——ffprobe/ffmpeg 走替身摘掉，
打分缝仍是 ``vlm_service.chat_with_image``。

采样、解析、过滤是产品语义的钉子（ADR 0053）：改任何默认值都该在这里先红。
score_frame 走替身（monkeypatch ``services.vlm.chat_with_image``）——不打真网。
"""

import pytest

import suite_api.services.frames as frames
import suite_api.services.vlm as vlm_service
from suite_api.services.frames import (
    ScoredFrame,
    filter_candidates,
    frame_timecode,
    frame_title,
    parse_frame_score,
    sample_points,
)

# ---------- 采样点 ----------


def test_sample_points_basic_interval() -> None:
    assert sample_points(20.0) == [0.0, 5.0, 10.0, 15.0]
    assert sample_points(5.0) == [0.0]
    assert sample_points(5.1) == [0.0, 5.0]


def test_sample_points_zero_and_negative_durations() -> None:
    assert sample_points(0.0) == []
    assert sample_points(-3.0) == []


def test_sample_points_cap_stretches_interval_not_tail() -> None:
    """超上限（24）拉大间隔保持均匀：200s 视频 40 个 5s 点 → 24 点、间隔 200/24，
    每点都 < duration（不掐尾：直播后段的展示帧不丢）。"""
    points = sample_points(200.0)
    assert len(points) == 24
    assert points[0] == 0.0
    assert all(0.0 <= p < 200.0 for p in points)
    # 均匀性：相邻间隔恒 ≈ 200/24（浮点毫秒取整容差）
    gaps = [round(b - a, 6) for a, b in zip(points, points[1:], strict=False)]
    assert max(gaps) - min(gaps) < 0.01


def test_sample_points_under_cap_uses_plain_interval() -> None:
    points = sample_points(100.0)
    assert len(points) == 20
    assert points == [round(i * 5.0, 3) for i in range(20)]


# ---------- 打分解析 ----------


def test_parse_frame_score_accepts_plain_and_fenced_json() -> None:
    assert parse_frame_score('{"score": 7, "note": "商品居中清晰"}') == (7, "商品居中清晰")
    fenced = '```json\n{"score": 8, "note": "特写完整"}\n```'
    assert parse_frame_score(fenced) == (8, "特写完整")


def test_parse_frame_score_rejects_bad_shapes() -> None:
    assert parse_frame_score("这不是 JSON") is None
    assert parse_frame_score("[]") is None  # 不是对象
    assert parse_frame_score('{"score": 0, "note": "x"}') is None  # 越下界
    assert parse_frame_score('{"score": 11, "note": "x"}') is None  # 越上界
    assert parse_frame_score('{"score": "7", "note": "x"}') is None  # 字符串分
    assert parse_frame_score('{"score": true, "note": "x"}') is None  # bool 不是分
    assert parse_frame_score('{"score": 7, "note": 7}') is None  # note 非字符串
    assert parse_frame_score('{"score": 7}') == (7, "")  # 缺 note=空说明（分数才是闸）


def test_parse_frame_score_strips_note_and_accepts_empty_note() -> None:
    assert parse_frame_score('{"score": 6, "note": "  可以用  "}') == (6, "可以用")
    assert parse_frame_score('{"score": 6, "note": ""}') == (6, "")


def test_parse_frame_score_accepts_whole_number_float() -> None:
    assert parse_frame_score('{"score": 7.0, "note": "ok"}') == (7, "ok")


# ---------- 候选过滤 ----------


def _frame(at: float, score: int) -> ScoredFrame:
    return ScoredFrame(at_second=at, score=score, note="n", thumbnail=b"j")


def test_filter_candidates_min_score_gate() -> None:
    scored = [_frame(0.0, 8), _frame(5.0, 5), _frame(10.0, 6), _frame(15.0, 3)]
    assert [f.at_second for f in filter_candidates(scored)] == [0.0, 10.0]


def test_filter_candidates_cap_keeps_highest_then_sorts_by_time() -> None:
    # 10 帧：0/5/10 → 9 分；15/20/25 → 8 分；30/35 → 7 分；40/45 → 6 分
    scored = [_frame(float(at), 9 - index // 3) for index, at in enumerate(range(0, 50, 5))]
    out = filter_candidates(scored)
    assert len(out) == 8
    # 超上限取分高者：6 分两帧（40/45）被挤出，其余按时间升序返回
    assert [f.at_second for f in out] == [0.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 35.0]


def test_filter_candidates_tie_prefers_earlier_frame() -> None:
    scored = [_frame(0.0, 7), _frame(5.0, 7), _frame(10.0, 7), _frame(15.0, 6)]
    out = filter_candidates(scored, cap=2)
    assert [f.at_second for f in out] == [0.0, 5.0]


def test_filter_candidates_empty() -> None:
    assert filter_candidates([]) == []


# ---------- 时间码与标题 ----------


def test_frame_timecode() -> None:
    assert frame_timecode(0) == "00:00"
    assert frame_timecode(5.4) == "00:05"
    assert frame_timecode(65.0) == "01:05"
    assert frame_timecode(3599.9) == "59:59"
    assert frame_timecode(-1.2) == "00:00"  # 负秒防御


def test_frame_title_shape() -> None:
    assert frame_title("钛钢保温杯", 65.0) == "钛钢保温杯 · 实拍帧 01:05"
    assert frame_title("  ", 0) == "直播切片 · 实拍帧 00:00"  # 空基名兜底
    long_name = "名" * 60
    title = frame_title(long_name, 10)
    assert title.startswith("名" * 40)
    assert title.endswith("实拍帧 00:10")
    assert len(title) <= 200  # 标题列宽


# ---------- score_frame（VLM 替身） ----------


def test_score_frame_parses_vlm_output(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[bytes] = []

    def fake_chat(system_prompt: str, user_prompt: str, image_bytes: bytes) -> str:
        seen.append(image_bytes)
        return '{"score": 9, "note": "清晰特写"}'

    monkeypatch.setattr(vlm_service, "chat_with_image", fake_chat)
    assert frames.score_frame(b"thumb-bytes") == (9, "清晰特写")
    assert seen == [b"thumb-bytes"]  # 送打分的就是缩略图字节


def test_score_frame_unparsable_output_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vlm_service, "chat_with_image", lambda *args: "模型嘴瓢了")
    assert frames.score_frame(b"thumb-bytes") is None


def test_score_frame_propagates_vlm_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*args: object) -> str:
        raise vlm_service.VLMUnavailable("看图服务暂时不可用")

    monkeypatch.setattr(vlm_service, "chat_with_image", _boom)
    with pytest.raises(vlm_service.VLMUnavailable):
        frames.score_frame(b"thumb-bytes")


# ---------- generate_candidates：单帧失败不杀整批（第 112 刀） ----------


def _stub_extraction(monkeypatch: pytest.MonkeyPatch, duration: float = 15.0) -> None:
    """摘掉真 ffprobe/ffmpeg（本文件不上外部依赖）：采样点走纯函数真值 [0, 5, 10]。

    打分的缝仍是 ``vlm_service.chat_with_image``（与 score_frame 两测同款替身）。
    """
    monkeypatch.setattr(frames, "probe_duration_seconds", lambda _b: duration)
    monkeypatch.setattr(frames, "extract_frame_jpeg", lambda *args, **kwargs: b"\xff\xd8\xffthumb")


def test_generate_candidates_skips_single_frame_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """一帧超时只跳过该帧：其余帧照常打分出候选，failed_frames 如实计数=1。"""
    _stub_extraction(monkeypatch)
    calls: list[float] = []
    replies = ['{"score": 8, "note": "清晰"}', '{"score": 7, "note": "特写"}']

    def fake_chat(system_prompt: str, user_prompt: str, image_bytes: bytes) -> str:
        calls.append(1.0)
        if len(calls) == 2:  # 第 2 帧（at=5s）超时
            raise vlm_service.VLMUnavailable("看图服务暂时不可用")
        return replies.pop(0)

    monkeypatch.setattr(vlm_service, "chat_with_image", fake_chat)
    outcome = frames.generate_candidates(b"video-bytes")
    assert len(calls) == 3  # 三帧都试过（失败帧不中断后续）
    assert outcome.sampled == 3
    assert outcome.failed_frames == 1
    assert [f.at_second for f in outcome.candidates] == [0.0, 10.0]
    assert [f.score for f in outcome.candidates] == [8, 7]


def test_generate_candidates_all_frames_failed_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """全部采样帧都栽在 VLM 上：没有候选可出，抛 VLMUnavailable（路由 502），
    文案带帧数（可行动），不是静默空候选。"""
    _stub_extraction(monkeypatch)

    def _boom(*args: object) -> str:
        raise vlm_service.VLMUnavailable("看图服务暂时不可用")

    monkeypatch.setattr(vlm_service, "chat_with_image", _boom)
    with pytest.raises(vlm_service.VLMUnavailable) as excinfo:
        frames.generate_candidates(b"video-bytes")
    assert "全部 3 帧处理失败" in str(excinfo.value)


def test_generate_candidates_unparsable_outputs_stay_200_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """边界：全帧「输出不可解析」不是请求失败——不抛、零候选、failed_frames=0
    （与全帧 VLM 失败分说：那是服务不可用，这是模型没按形状返回）。"""
    _stub_extraction(monkeypatch)
    monkeypatch.setattr(vlm_service, "chat_with_image", lambda *args: "模型嘴瓢了")
    outcome = frames.generate_candidates(b"video-bytes")
    assert outcome.candidates == []
    assert outcome.sampled == 3
    assert outcome.failed_frames == 0


def test_generate_candidates_not_configured_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    """VLMNotConfigured 不在韧性面：fail-fast 冒泡（路由 409），第一帧就停，
    不当「单帧失败」吞掉继续打分。"""
    _stub_extraction(monkeypatch)
    calls: list[int] = []

    def _boom(*args: object) -> str:
        calls.append(1)
        raise vlm_service.VLMNotConfigured("未配置 VLM_API_KEY，看图出草稿不可用")

    monkeypatch.setattr(vlm_service, "chat_with_image", _boom)
    with pytest.raises(vlm_service.VLMNotConfigured):
        frames.generate_candidates(b"video-bytes")
    assert len(calls) == 1  # 配置问题不逐帧重试（fail-fast）


def test_generate_candidates_single_extraction_failure_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    """审计 113 P0：单帧 extract_frame_jpeg 失败（越界/损坏）→跳过该帧不杀整批。"""
    from suite_api.services import frames as frames_mod

    calls = iter([0, 1, 2])
    def fake_extract(video_bytes, at_second, max_width=480):
        n = next(calls)
        if n == 1:
            raise frames_mod.FrameExtractionError("ffmpeg timeout at test frame")
        return bytes([0xff, 0xd8]) + b"fake"

    monkeypatch.setattr(frames_mod, "extract_frame_jpeg", fake_extract)
    monkeypatch.setattr(
        frames_mod, "score_frame",
        lambda thumb: (8, "商品帧"[:20]),
    )
    monkeypatch.setattr(frames_mod, "probe_duration_seconds", lambda _: 15.0)

    outcome = frames_mod.generate_candidates(b"fake-video")
    assert outcome.failed_frames == 1
    assert len(outcome.candidates) == 2  # 采样 3 帧、1 帧抽帧失败跳过、2 帧打 8 分
