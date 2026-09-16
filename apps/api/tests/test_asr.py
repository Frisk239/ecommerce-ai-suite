"""云 ASR 转写单元测试（第 93 刀，ADR 0050）：离线——不打外网、不连库、不吃 ffmpeg。

覆盖三块：
- **停顿聚合纯函数**（本刀主体语义）：gap 断段 / 20s 强切 / 60 段上限合并 /
  空 segments / 文本连接（中文不插空、英文补空格）/ 秒→时间码（终点恒 ≥ 起点+1s）；
- **切段与合并**：wav 按样本切块（偏移正确、字节上限与时长上限都生效）、
  多块结果按偏移合并回源时间轴（乱序/坏区间不丢句）；
- **客户端契约**：无 key 不建客户端（显式钉 `_new_client` 不被调用）、
  `_get_client` 抛 ASRNotConfigured、响应归一化（无句级时间戳=诚实失败，
  无语音=0 段）。
"""

import io
import wave
from types import SimpleNamespace

import pytest

import suite_api.services.asr as asr
from suite_api.services.asr import (
    MAX_CANDIDATES,
    AggregatedSegment,
    ASRNotConfigured,
    ASRUnavailable,
    AudioExtractionError,
    TranscribeBudgetExceeded,
    TranscribeOutcome,
    _join_texts,
    _segments_of,
    aggregate_segments,
    merge_segments,
    segment_timecodes,
    split_wav_chunks,
)
from suite_api.settings import Settings


def _seg(start: float, end: float, text: str) -> dict:
    return {"start": start, "end": end, "text": text}


def _texts(outcome) -> list[str]:
    return [segment.transcript for segment in outcome.segments]


# ---------------------------------------------------------------- 停顿聚合


def test_gap_splits_at_1_2_seconds_boundary() -> None:
    """句间静默 ≥1.2s 断段；<1.2s 不断（边界两侧都钉）。"""
    outcome = aggregate_segments(
        [
            _seg(0.0, 2.0, "第一句。"),
            _seg(3.2, 4.0, "整 1.2 秒停顿。"),  # gap == 1.2 -> 断
            _seg(5.19, 6.0, "差一点不到 1.2。"),  # gap == 1.19 -> 不断
        ]
    )
    assert _texts(outcome) == ["第一句。", "整 1.2 秒停顿。差一点不到 1.2。"]
    assert [(s.start, s.end) for s in outcome.segments] == [(0.0, 2.0), (3.2, 6.0)]
    assert outcome.before_cap == 2 and outcome.merged == 0 and not outcome.capped


def test_force_cut_after_20_seconds_even_without_pause() -> None:
    """无停顿长独白：段累计时长 ≥20s 后下一句另起候选（不糊成一大段）。"""
    segments = [_seg(i * 6.0, i * 6.0 + 5.9, f"第{i}句。") for i in range(8)]
    outcome = aggregate_segments(segments)  # 每句 5.9s + 0.1s 缝：gap 0.1 不断
    assert [(s.start, s.end) for s in outcome.segments] == [
        (0.0, 23.9),  # 四句累计 23.9s（≥20 在加上第 5 句**之前**成立 -> 第 5 句新段）
        (24.0, 47.9),
    ]
    assert _texts(outcome)[0] == "第0句。第1句。第2句。第3句。"
    assert _texts(outcome)[1] == "第4句。第5句。第6句。第7句。"


def test_cap_merges_adjacent_segments_to_60() -> None:
    """150 段（每段自带大停顿）→ 上限 60：相邻合并、时间轴仍连续升序、如实报合并数。"""
    segments = [_seg(i * 10.0, i * 10.0 + 2.0, f"句{i}。") for i in range(150)]
    outcome = aggregate_segments(segments)
    assert outcome.before_cap == 150
    assert len(outcome.segments) == MAX_CANDIDATES
    assert outcome.merged == 150 - MAX_CANDIDATES
    assert outcome.capped is True
    # 首段并进前三句（150/60=2.5，首桶 3 组）；末段止于最后一句的 end
    assert outcome.segments[0].transcript == "句0。句1。句2。"
    assert outcome.segments[-1].end == segments[-1]["end"]
    starts = [segment.start for segment in outcome.segments]
    assert starts == sorted(starts) and len(set(starts)) == len(starts)
    # 合并后的候选恒覆盖原句（不丢句）：每段文本仍由「句N。」组成
    assert all(segment.transcript.count("。") == segment.transcript.count("句") for segment in outcome.segments)


def test_cap_not_triggered_keeps_segments_and_receipt_has_no_note() -> None:
    outcome = aggregate_segments([_seg(0.0, 1.0, "只有一句。")])
    assert outcome.before_cap == 1 and outcome.merged == 0 and not outcome.capped
    receipt = TranscribeOutcome(
        candidates_created=1, segments=1, before_cap=1, merged=0
    )
    assert receipt.note is None


def test_empty_segments_yields_no_candidates_and_honest_note() -> None:
    """云转写没识别到语音：0 候选 + 回执如实说明（不是错误，也不是编造候选）。"""
    outcome = aggregate_segments([])
    assert outcome.segments == [] and outcome.before_cap == 0 and outcome.merged == 0
    receipt = TranscribeOutcome(candidates_created=0, segments=0, before_cap=0, merged=0)
    assert receipt.note is not None and "没有识别到语音" in receipt.note


def test_capped_receipt_states_merge_in_plain_words() -> None:
    receipt = TranscribeOutcome(candidates_created=60, segments=150, before_cap=150, merged=90)
    assert receipt.note is not None
    assert "150 段" in receipt.note and "60 条候选" in receipt.note and "90 段" in receipt.note


def test_text_join_skips_space_between_cjk_and_adds_between_latin() -> None:
    assert _join_texts(["你好，", "世界。"]) == "你好，世界。"
    assert _join_texts(["hello", "world"]) == "hello world"
    assert _join_texts(["hello", "世界"]) == "hello世界"
    assert _join_texts(["世界", "hello"]) == "世界hello"


def test_segment_timecodes_floor_start_and_ceil_end_with_min_one_second() -> None:
    assert segment_timecodes(0.2, 0.4) == ("00:00:00", "00:00:01")  # 亚秒短句也 >0s
    assert segment_timecodes(59.5, 61.2) == ("00:00:59", "00:01:02")
    assert segment_timecodes(3661.0, 3662.4) == ("01:01:01", "01:01:03")
    assert segment_timecodes(-0.5, 1.0) == ("00:00:00", "00:00:01")  # 负起点贴 0
    assert all(len(part) == 8 for part in segment_timecodes(0.0, 3600.0))


# ---------------------------------------------------------------- 切段 / 合并


def _wav_bytes(seconds: float, *, rate: int = 16000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(b"\x00\x00" * int(rate * seconds))
    return buffer.getvalue()


def test_split_wav_chunks_by_seconds_keeps_offsets_and_playable_headers() -> None:
    chunks = split_wav_chunks(_wav_bytes(25.0), chunk_seconds=10.0, max_bytes=10**9)
    assert [offset for offset, _ in chunks] == [0.0, 10.0, 20.0]
    assert [len(payload) for _, payload in chunks] == [320044, 320044, 160044]
    # 每块自带 wav 头、帧数与偏移自洽（可独立上送 ASR）
    for offset, payload in chunks:
        with wave.open(io.BytesIO(payload), "rb") as reader:
            assert reader.getframerate() == 16000 and reader.getnchannels() == 1
            assert reader.getnframes() * 2 == len(payload) - 44
        assert offset >= 0


def test_split_wav_chunks_respects_byte_cap() -> None:
    """字节上限优先于时长上限（Groq 25MB 的硬约束）：2 秒一块 -> 25 秒切 13 块。"""
    chunks = split_wav_chunks(_wav_bytes(25.0), max_bytes=32000 * 2, chunk_seconds=1000.0)
    assert len(chunks) == 13
    assert all(len(payload) <= 32000 * 2 + 44 for _, payload in chunks)
    assert [offset for offset, _ in chunks][:3] == [0.0, 2.0, 4.0]


def test_split_wav_chunks_empty_audio_and_broken_bytes_are_honest() -> None:
    assert split_wav_chunks(_wav_bytes(0.0)) == []  # 合法 wav 但零帧 = 无音轨
    with pytest.raises(AudioExtractionError, match="wav"):
        split_wav_chunks(b"not-a-wav")  # 坏字节不静默吞：路由按 422 处理


def test_merge_segments_applies_chunk_offsets_and_sorts() -> None:
    merged = merge_segments(
        [
            (
                600.0,
                [_seg(0.0, 1.5, "第二块第一句"), _seg(2.0, 3.0, "第二块第二句")],
            ),
            (0.0, [_seg(0.0, 2.0, "第一块")]),
        ]
    )
    assert [item["text"] for item in merged] == ["第一块", "第二块第一句", "第二块第二句"]
    assert merged[1]["start"] == 600.0 and merged[1]["end"] == 601.5


def test_merge_segments_drops_empty_text_and_fixes_reversed_range() -> None:
    merged = merge_segments([(0.0, [_seg(3.0, 1.0, "倒序行"), _seg(0.0, 1.0, "   ")])])
    assert merged == [{"start": 1.0, "end": 3.0, "text": "倒序行"}]


# ---------------------------------------------------------------- 客户端契约


def test_transcript_source_vocabulary_is_three_values() -> None:
    """转写来源词表（CONTEXT 词条「转写来源」）：cloud/local/manual——0030 无 CHECK，
    取值由这里收口，所以词表本身也要钉。"""
    assert (asr.CLOUD, asr.LOCAL, asr.MANUAL) == ("cloud", "local", "manual")
    assert asr.PENDING == "pending"


def test_settings_defaults_match_researched_selection() -> None:
    """89 刀定案（带时间戳免费档=Groq turbo）落在缺省值上；key 缺省为空。"""
    settings = Settings(_env_file=None)
    assert settings.asr_api_key == ""
    assert settings.asr_base_url == "https://api.groq.com/openai/v1"
    assert settings.asr_model == "whisper-large-v3-turbo"


def test_get_client_without_key_never_builds_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """空 key=不建客户端（显式钉：`_new_client` 被调用即失败）。"""
    monkeypatch.setattr(asr, "get_settings", lambda: Settings(asr_api_key=""))
    monkeypatch.setattr(asr, "_client", None)

    def _explode(settings: Settings) -> object:  # pragma: no cover - 不该被调用
        raise AssertionError("空 key 不许建客户端")

    monkeypatch.setattr(asr, "_new_client", _explode)
    with pytest.raises(ASRNotConfigured):
        asr._get_client()
    assert asr.is_configured() is False


def test_is_configured_true_with_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(asr, "get_settings", lambda: Settings(asr_api_key="test-key"))
    assert asr.is_configured() is True


def test_segments_of_normalizes_and_rejects_missing_timestamps() -> None:
    with_segments = SimpleNamespace(
        segments=[
            SimpleNamespace(start=0.0, end=1.5, text=" 你好 "),
            SimpleNamespace(start=1.5, end=None, text="缺 end 丢弃"),
            SimpleNamespace(start=2.0, end=3.0, text="   "),
        ],
        text="你好",
    )
    assert _segments_of(with_segments) == [{"start": 0.0, "end": 1.5, "text": "你好"}]
    # 只回全文没句级时间戳（含空 segments）-> 诚实失败，不编时间码
    with pytest.raises(ASRUnavailable, match="句级时间戳"):
        _segments_of(SimpleNamespace(segments=[], text="有文本没时间戳"))
    # 既无 segments 又无文本 = 无语音：合法空结果
    assert _segments_of(SimpleNamespace(segments=[], text="")) == []


def test_aggregated_segment_is_frozen_value_object() -> None:
    """聚合段是 frozen dataclass：下游（落库/脚本）拿到的是值，不是可改状态。"""
    import dataclasses

    segment = AggregatedSegment(start=0.0, end=1.0, transcript="文本")
    with pytest.raises(dataclasses.FrozenInstanceError):
        segment.start = 2.0  # type: ignore[misc]


# ---------------------------------------------------------------- 总预算闸（审计 19）


def test_budget_error_carries_chunk_progress_and_saved_count() -> None:
    """预算超限异常携带 {已转块数}/{总块数} 与保留候选条数（路由 502 detail 的
    全部事实源）；文案含「已转 X/Y 块」与拣选/切段提示。"""
    exc = TranscribeBudgetExceeded(transcribed=1, total=3, candidates_saved=2)
    assert (exc.transcribed_chunks, exc.total_chunks, exc.candidates_saved) == (1, 3, 2)
    message = str(exc)
    assert "已转 1/3 块" in message
    assert "2 条部分候选" in message
    assert "切段上传" in message


def test_budget_constants_frontend_alignment() -> None:
    """总预算 300s 是服务端硬上限；单块超时 120s 仍是首块预估的取值源。"""
    assert asr.TRANSCRIBE_TOTAL_BUDGET_SECONDS == 300.0
    assert asr.TRANSCRIBE_TOTAL_BUDGET_SECONDS > asr.ASR_TIMEOUT_SECONDS
