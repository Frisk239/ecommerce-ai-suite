"""云 ASR 转写：录像 → 提音轨 → 句级时间戳 → 停顿聚合 → 切片候选（第 93 刀，ADR 0050）。

链路（**全程同步**：路由是同步 def，FastAPI 丢线程池——ffmpeg 子进程与厂商 HTTP
都是阻塞调用，与切片真切同形，不占事件循环）：

1. **提音轨**（``extract_audio_wav``）：录像字节 → ``ffmpeg -vn -ac 1 -ar 16000``
   的 16kHz 单声道 PCM wav。ASR 只吃音频；整段 mp4 直送既超单文件上限又白花带宽。
   无音轨/提取失败 -> ``AudioExtractionError``（路由转 422，不静默降级）。
2. **切段**（``split_wav_chunks``）：超过 24MB（Groq turbo 单文件上限 25MB）按
   10 分钟切块。wav 是裸 PCM——按样本对齐切、零重编码；块起始时间随块返回，
   转写结果按偏移合并（``merge_segments``），时间戳仍是**源录像时间轴**。
3. **云转写**（``transcribe_audio``）：OpenAI 兼容 ``POST {base}/audio/transcriptions``，
   ``response_format=verbose_json`` 取 ``segments[].{start,end,text}``（句级时间戳）。
   **没给 segments 就当失败**——没有时间戳就聚合不出候选，宁可 502 也不编时间码。
   无语音（segments 与 text 皆空）= 0 段，合法结果（回执如实说「未识别到语音」）。
   逐块串行共享**总预算 300s**（审计 19）：每块开转前判「累计已耗时+下一块预
   估」，超限即停——已成功块的部分候选先落库再抛 ``TranscribeBudgetExceeded``
   （路由 502 带已转块数，重跑被既有候选 409 挡住：先拣选再整段重转或切段上传）。
4. **停顿聚合**（``aggregate_segments``，纯函数）：句间 gap ≥1.2s 断段；段累计
   时长 ≥20s 后下一句强切另起；每候选 = {start, end, transcript(句文本连接)}；
   候选段数 ≤60 保护（超出把相邻段按序合并到 60 条，回执如实说明合并过）。

密钥纪律（同 0033/LLM）：``ASR_API_KEY`` 只经 env 进程内存——不写日志、不进异常
文案（超时/连接错误一律转通用文案）、不进任何响应。**空 key = 不建客户端、
不发请求**：自动转写端点据此 409 诚实拒绝（fail-closed），无 key 环境人工填
transcript 的现状不变（本地兜底走 ``scripts/transcribe_local.py``，不进 api 镜像）。
"""

import io
import logging
import math
import subprocess
import tempfile
import threading
import time
import wave
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openai import OpenAI
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from suite_api.models import ClipCandidate
from suite_api.settings import Settings, get_settings
from suite_platform.storage import ObjectStorage

logger = logging.getLogger(__name__)

# 转写来源三值（CONTEXT 词条「转写来源」；迁移 0030 只放开列不加 CHECK——取值
# 由这里收口，与 status/source_kind 同风格）
CLOUD = "cloud"
LOCAL = "local"
MANUAL = "manual"

# 候选两态（0039，与 services/clips 同词表）：转写只产 pending——人工拣选闸门保留
PENDING = "pending"

# 停顿聚合参数（spec 口径，改这里就是改产品语义，测试钉死）
PAUSE_GAP_SECONDS = 1.2  # 句间静默 ≥ 此值断段（一段=一口气说完的话）
MAX_SEGMENT_SECONDS = 20.0  # 段累计时长 ≥ 此值后下一句强切（长独白不糊成一大段）
MAX_CANDIDATES = 60  # 单份录像候选段数上限（超出合并相邻段，回执说明）

# 切段参数：Groq turbo 单文件 25MB —— 留 1MB 余量按 24MB 切；10 分钟 16kHz
# 单声道 PCM ≈ 19.2MB（32000 B/s），两块都在上限内
MAX_CHUNK_BYTES = 24 * 1024 * 1024
CHUNK_SECONDS = 600.0

# ASR 请求超时（秒）：spec 的「同步执行超时 120s」——超时转 ASRUnavailable，
# 不重试（重试只会把操作者的等待翻倍；失败可再点一次）
ASR_TIMEOUT_SECONDS = 120.0
# 转写总预算（秒，审计 19）：提音轨 + 逐块云请求的**服务端硬上限**。块数 ×
# 单块 120s 超时在长录像上无界（多块串行可达十分钟级），而前端只在超时处先断
# ——操作者只看到「网络失败」。每块开转前判「累计已耗时 + 下一块预估」，超限
# 即停：已成功块聚合出的部分候选**先落库再抛错**（TranscribeBudgetExceeded，
# 路由转 502 带已转块数）。前端转写超时 320s 略宽于此值。
TRANSCRIBE_TOTAL_BUDGET_SECONDS = 300.0
# 时钟缝（默认真单调钟）：预算判定只读这一个符号——测试注入假钟即可模拟
# 「慢转写穿预算」，不必真等 300s
_now_seconds = time.monotonic
# ffmpeg 提音轨超时（秒）：本地流拷贝级操作是秒级；畸形输入不许挂死请求线程
_FFMPEG_TIMEOUT = 120
# 16kHz 单声道 16bit：ffmpeg 输出参数与 split_wav_chunks 的偏移换算同源
_TARGET_SAMPLE_RATE = 16000
_WAV_HEADER_BYTES = 44


class ASRError(Exception):
    """转写失败基类：调用方按子类分派 HTTP 码（422/502）。"""


class ASRNotConfigured(ASRError):
    """ASR_API_KEY 未配置（空凭证不建客户端、不发请求）。"""


class ASRUnavailable(ASRError):
    """云转写请求失败（超时/连接/响应异常/无句级时间戳）。消息为通用文案，
    不含密钥/端点 URL；异常原文只保留在服务端日志与 cause 链。"""


class AudioExtractionError(ASRError):
    """提音轨失败（录像无音轨/字节损坏/ffmpeg 不可用）：路由转 422。"""


class TranscribeBudgetExceeded(ASRError):
    """转写超总预算（审计 19）：停在已成功块处，**已聚合的部分候选已先行落库**
    （部分成果保留）。异常携带 ``{已转块数}/{总块数}`` 与保留候选条数，路由转
    502。落库后的重跑会被「已有未拣选 cloud 候选」409 挡住（带现有条数）——
    操作者先拣选这批部分候选，再整段重转或把录像切段上传；「重跑不是追加」
    的幂等口径不因部分失败破例。"""

    def __init__(self, transcribed: int, total: int, candidates_saved: int) -> None:
        self.transcribed_chunks = transcribed
        self.total_chunks = total
        self.candidates_saved = candidates_saved
        super().__init__(
            f"转写超总预算（{TRANSCRIBE_TOTAL_BUDGET_SECONDS:.0f}s），已转 {transcribed}/{total} 块，"
            f"已保留 {candidates_saved} 条部分候选；可先拣选候选再整段重跑，或把录像切段上传"
        )


# 进程级单例（同步客户端线程安全：路由跑在线程池，多个请求共享一份连接池）。
# 懒建（而非 import 期）保证空凭证进程不持有客户端；测试注入替身走
# `transcribe_audio` 这个缝（不碰客户端）。密钥只在构造时从 settings 读入内存，
# 此后不再外流（0033）；settings 进程内缓存不变，热轮换密钥需重启（与 LLM 同口径）。
_client: OpenAI | None = None
_client_lock = threading.Lock()


def _new_client(settings: Settings) -> OpenAI:
    return OpenAI(
        api_key=settings.asr_api_key,
        base_url=settings.asr_base_url,
        timeout=ASR_TIMEOUT_SECONDS,
        max_retries=0,
        default_headers={"User-Agent": "ecommerce-ai-suite/0.1"},
    )


def _get_client() -> OpenAI:
    global _client
    settings = get_settings()
    if not settings.asr_api_key:
        raise ASRNotConfigured("未配置 ASR_API_KEY，云转写不可用")
    with _client_lock:
        if _client is None:
            _client = _new_client(settings)
        return _client


def is_configured() -> bool:
    """ASR 是否已配置（端点 409 判定与前端按钮禁用共用同一判据）。"""
    return bool(get_settings().asr_api_key)


# ---------------------------------------------------------------- 提音轨 / 切段


def extract_audio_wav(video_bytes: bytes) -> bytes:
    """录像字节 → 16kHz 单声道 PCM wav 字节（ffmpeg）。

    无音轨（``Output file does not contain any stream``）/解码失败 / ffmpeg 缺失
    一律抛 AudioExtractionError（路由 422：让操作者知道这段录像转不了，而不是
    回一个空候选列表）。ffmpeg 本体在镜像里（第 46 刀起）；本机裸跑需自备。
    """
    with tempfile.TemporaryDirectory(prefix="asr-audio-") as tmp:
        in_path = Path(tmp) / "in.mp4"
        out_path = Path(tmp) / "out.wav"
        in_path.write_bytes(video_bytes)
        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(in_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(_TARGET_SAMPLE_RATE),
            "-c:a",
            "pcm_s16le",
            "-f",
            "wav",
            "-y",
            str(out_path),
        ]
        try:
            completed = _run_ffmpeg(command)
        except OSError as exc:  # ffmpeg 不在 PATH
            raise AudioExtractionError(f"ffmpeg 无法执行: {exc}") from exc
        detail = completed.stderr.decode("utf-8", "replace").strip()[:300]
        if completed.returncode != 0:
            if "does not contain any stream" in detail or "matches no streams" in detail:
                raise AudioExtractionError("录像没有可提取的音轨（无音频流）")
            raise AudioExtractionError(
                f"音频提取失败（ffmpeg 退出码 {completed.returncode}）: {detail}"
            )
        if not out_path.is_file() or out_path.stat().st_size <= _WAV_HEADER_BYTES:
            raise AudioExtractionError("录像没有可提取的音轨（提取结果为空）")
        return out_path.read_bytes()


def _run_ffmpeg(command: list[str]) -> Any:
    """跑 ffmpeg（参数全为内部构造，无 shell）；超时/不可执行由调用方归类。"""
    try:
        return subprocess.run(  # noqa: S603 - 参数全为内部构造，无 shell
            command, capture_output=True, timeout=_FFMPEG_TIMEOUT, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise AudioExtractionError(f"ffmpeg 提音轨超时（>{_FFMPEG_TIMEOUT}s）") from exc


def split_wav_chunks(
    wav_bytes: bytes,
    *,
    max_bytes: int = MAX_CHUNK_BYTES,
    chunk_seconds: float = CHUNK_SECONDS,
) -> list[tuple[float, bytes]]:
    """wav 字节 → ``[(块起始秒, 块 wav 字节)]``（纯函数，无 ffmpeg）。

    切点按**样本对齐**（裸 PCM 直接切片，不带重编码开销）：块帧数取「字节上限」
    与「时长上限」的较小者。每块自带 wav 头（写回内存缓冲），可独立上送 ASR。
    空音频/零帧 -> ``[]``（调用方按无音轨处理）；wav 头非法 -> AudioExtractionError
    （诚实失败，不静默吞成 0 候选）。
    """
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as reader:
            channels = reader.getnchannels()
            sample_width = reader.getsampwidth()
            frame_rate = reader.getframerate()
            frames = reader.readframes(reader.getnframes())
    except wave.Error as exc:
        raise AudioExtractionError(f"音频块不是合法 wav: {exc}") from exc
    frame_bytes = channels * sample_width
    if frame_bytes <= 0 or frame_rate <= 0 or not frames:
        return []
    frame_count = len(frames) // frame_bytes
    frames_per_chunk = int(min(max_bytes // frame_bytes, chunk_seconds * frame_rate))
    frames_per_chunk = max(frames_per_chunk, 1)
    chunks: list[tuple[float, bytes]] = []
    for offset_frames in range(0, frame_count, frames_per_chunk):
        stop = min(offset_frames + frames_per_chunk, frame_count)
        payload = frames[offset_frames * frame_bytes : stop * frame_bytes]
        chunks.append(
            (
                offset_frames / frame_rate,
                _pack_wav(channels, sample_width, frame_rate, payload),
            )
        )
    return chunks


def _pack_wav(channels: int, sample_width: int, frame_rate: int, frames: bytes) -> bytes:
    """裸 PCM 帧 → 带 wav 头字节（与 split_wav_chunks 的读端同参）。"""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(sample_width)
        writer.setframerate(frame_rate)
        writer.writeframes(frames)
    return buffer.getvalue()


# ---------------------------------------------------------------- 云转写


def transcribe_audio(
    audio_bytes: bytes, *, filename: str = "audio.wav"
) -> list[dict[str, Any]]:
    """单个音频块 → 句级段 ``[{start, end, text}]``（时间戳相对该块起点）。

    只抛 ASRError 子类：未配置 / 请求失败 / **无句级时间戳**（网关不认
    verbose_json 或只回全文——没有时间戳就聚合不出候选，如实失败，不编时间码）。
    全程不重试（操作者等在同步请求里，重试只是把等待翻倍）。
    """
    client = _get_client()
    settings = get_settings()
    try:
        response = client.audio.transcriptions.create(
            model=settings.asr_model,
            file=(filename, audio_bytes),
            response_format="verbose_json",
        )
    except ASRError:
        raise
    except Exception as exc:  # noqa: BLE001 - 统一转通用文案，凭证/端点不外泄
        logger.warning("ASR 请求失败: %s", type(exc).__name__)
        raise ASRUnavailable("云转写服务暂时不可用") from exc
    return _segments_of(response)


def _segments_of(response: Any) -> list[dict[str, Any]]:
    """响应 → 段列表（非 dict/字段缺失的行跳过；文本空的行不产出）。"""
    raw_segments = getattr(response, "segments", None) or []
    segments: list[dict[str, Any]] = []
    for item in raw_segments:
        start = getattr(item, "start", None)
        end = getattr(item, "end", None)
        text = str(getattr(item, "text", "") or "").strip()
        if not text or start is None or end is None:
            continue
        try:
            start_value = float(start)
            end_value = float(end)
        except (TypeError, ValueError):
            continue
        segments.append({"start": start_value, "end": end_value, "text": text})
    if not segments and str(getattr(response, "text", "") or "").strip():
        raise ASRUnavailable("云转写未返回句级时间戳，无法聚合候选")
    return segments


def merge_segments(
    chunk_results: Sequence[tuple[float, Sequence[dict[str, Any]]]],
) -> list[dict[str, Any]]:
    """``[(块偏移秒, 块内段)]`` → 源录像时间轴上的段列表（纯函数，按 start 排序）。

    end < start 的坏行按交换处理（不丢句、不产负区间）；空文本行丢弃。
    """
    merged: list[dict[str, Any]] = []
    for offset, segments in chunk_results:
        for segment in segments:
            text = str(segment.get("text", "")).strip()
            if not text:
                continue
            start = float(segment["start"]) + offset
            end = float(segment["end"]) + offset
            if end < start:
                start, end = end, start
            merged.append({"start": start, "end": end, "text": text})
    merged.sort(key=lambda item: (item["start"], item["end"]))
    return merged


# ---------------------------------------------------------------- 停顿聚合（纯函数）


@dataclass(frozen=True)
class AggregatedSegment:
    """聚合出的一条候选段：秒制起止 + 句文本连接后的转写。"""

    start: float
    end: float
    transcript: str


@dataclass(frozen=True)
class AggregationOutcome:
    """聚合结果：候选段 + 上限保护的回执事实（before_cap>MAX_CANDIDATES 时合并）。"""

    segments: list[AggregatedSegment]
    before_cap: int  # 合并前的段数（回执用：如实说明「合并过」）
    merged: int  # 因 60 段上限被并入相邻段的条数（0=未触发）

    @property
    def capped(self) -> bool:
        return self.merged > 0


def aggregate_segments(
    segments: Sequence[dict[str, Any]],
    *,
    gap: float = PAUSE_GAP_SECONDS,
    max_seconds: float = MAX_SEGMENT_SECONDS,
    max_candidates: int = MAX_CANDIDATES,
) -> AggregationOutcome:
    """句级段 → 候选段（纯函数）：停顿断段 + 时长强切 + 段数上限保护。

    断段判据（**加当前句之前**判，与测试逐字对应）：
    - 与上一句的静默 gap ≥ ``gap``（默认 1.2s）——停顿是「一段话说完」的天然边界；
    - 当前段累计时长（上一句止 - 本段首句起）≥ ``max_seconds``（默认 20s）——
      长独白没有停顿也强切，不让一条候选吃掉整场直播。

    文本按句序连接（``_join_texts``：两侧皆非 CJK 才补空格，中文不插空）。
    段数 > ``max_candidates`` 时按序把相邻段均摊合并到正好 60 条（合并后仍连续、
    仍按时间升序），合并条数进回执（不静默截断——截断等于丢句子）。
    """
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for segment in segments:
        if current and (
            segment["start"] - current[-1]["end"] >= gap
            or current[-1]["end"] - current[0]["start"] >= max_seconds
        ):
            groups.append(current)
            current = []
        current.append(segment)
    if current:
        groups.append(current)

    before_cap = len(groups)
    merged = 0
    if before_cap > max_candidates:
        groups = _merge_to_cap(groups, max_candidates)
        merged = before_cap - max_candidates
    return AggregationOutcome(
        segments=[
            AggregatedSegment(
                start=group[0]["start"],
                end=max(item["end"] for item in group),
                transcript=_join_texts([str(item["text"]) for item in group]),
            )
            for group in groups
        ],
        before_cap=before_cap,
        merged=merged,
    )


def _merge_to_cap(groups: list[list[dict[str, Any]]], cap: int) -> list[list[dict[str, Any]]]:
    """把 N（>cap）组按序均摊合并成 cap 组：第 i 组并进第 ``i*cap//N`` 桶。

    均摊（而非「前 cap-1 组不动、尾巴全并最后一条」）：合并后的候选时长与
    信息量分布更均匀，拣选时不会出现一条吃掉几百句的怪物候选。
    """
    total = len(groups)
    buckets: list[list[dict[str, Any]]] = [[] for _ in range(cap)]
    for index, group in enumerate(groups):
        buckets[index * cap // total].extend(group)
    return buckets


def _join_texts(parts: Sequence[str]) -> str:
    """句文本连接：两侧皆非 CJK 才补空格（中文 ASR 不插空，英文不粘连）。"""
    text = ""
    for part in parts:
        if not text:
            text = part
        elif _is_cjk(text[-1]) or _is_cjk(part[0]):
            text += part
        else:
            text += " " + part
    return text


def _is_cjk(char: str) -> bool:
    return ord(char) >= 0x2E80  # CJK 部首起：汉字/假名/全角标点都算「不插空」侧


def segment_timecodes(start: float, end: float) -> tuple[str, str]:
    """秒区间 → (``HH:MM:SS``, ``HH:MM:SS``)（模型两列都是 String(8)）。

    起点截断（贴实）、终点向上取整——**终点恒 ≥ 起点+1s**：零点几秒的短句若
    start==end，拣选时 ffmpeg 会因「切段时长必须为正」422。99 小时以上的录像
    在 200MB 上传上限下不可能出现（列宽上限天然不撞）。
    """
    start_seconds = max(int(math.floor(start)), 0)
    end_seconds = max(int(math.ceil(end)), start_seconds + 1)
    return _hms(start_seconds), _hms(end_seconds)


def _hms(seconds: int) -> str:
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


# ---------------------------------------------------------------- 落候选（DB）


@dataclass(frozen=True)
class RecordingRef:
    """转写所需的录像只读快照（id/label/object_key）。

    路由在**收口事务之前**取出：``Session.rollback()`` 会让 ORM 实例过期，
    之后再读属性会重新开一个事务——合成调用（ffmpeg + 云请求，最长 ~120s）
    期间不许占着池连接（P1#2 纪律）。脚本侧同样用它（不依赖 api 会话）。
    """

    id: int
    label: str
    object_key: str

    @classmethod
    def of(cls, recording: Any) -> "RecordingRef":
        """从 ORM 行（或任何带这三个属性的对象）取快照。"""
        return cls(id=int(recording.id), label=str(recording.label), object_key=str(recording.object_key))


def pending_count(db: Session, recording_id: int, sources: Sequence[str] = (CLOUD,)) -> int:
    """该录像**未拣选**的指定来源候选条数（默认只看 cloud）。

    云端点与本地脚本共用这一条判据：已有同源未拣选候选就拒绝重跑（回执带现
    有条数，避免重复堆候选）；全部拣选/登记后可再生成一批。本地脚本把 local
    也算进「已有」——两种通道产出同形候选，重复跑一样会堆。
    """
    count = db.scalar(
        select(func.count())
        .select_from(ClipCandidate)
        .where(
            ClipCandidate.recording_id == recording_id,
            ClipCandidate.transcript_source.in_(tuple(sources)),
            ClipCandidate.status == PENDING,
        )
    )
    return int(count or 0)


def create_candidates(
    db: Session,
    *,
    recording: RecordingRef,
    segments: Sequence[AggregatedSegment],
    product_id: int | None,
    source: str = CLOUD,
) -> int:
    """聚合段 → pending 候选行（带 recording_id 直接绑定，不等上传顺手绑）。

    **带 recording_id**：转写候选的产生源就是这份录像（第 46 刀「上传即绑
    recording_id IS NULL 的待拣候选」的顺手默认动作只圈无源候选——这里显式
    绑定，不会被后续上传误绑；第 49 刀改绑端点仍可把未拣选的它们改走）。
    未拣选=status pending（人工拣选闸门保留）；``transcript_source`` 标通道。
    """
    for segment in segments:
        start_tc, end_tc = segment_timecodes(segment.start, segment.end)
        db.add(
            ClipCandidate(
                product_id=product_id,
                status=PENDING,
                timecode_start=start_tc,
                timecode_end=end_tc,
                transcript=segment.transcript,
                transcript_source=source,
                # source_video_label 是 String(120)，上传 label 可到 200——这里截宽
                source_video_label=recording.label[:120],
                recording_id=recording.id,
            )
        )
    db.commit()
    return len(segments)


@dataclass(frozen=True)
class TranscribeOutcome:
    """转写回执（路由照此组装响应）：候选数 + ASR 句段数 + 上限合并事实。"""

    candidates_created: int
    segments: int  # ASR 返回的句级段数（聚合前的原始句数，供操作者核量级）
    before_cap: int  # 聚合出的段数（未触发上限时 == candidates_created）
    merged: int

    @property
    def note(self) -> str | None:
        """回执附注（如实说明上限合并/无语音，未触发为 None）。"""
        if self.candidates_created == 0:
            return "云转写没有识别到语音，未生成候选"
        if self.merged:
            return (
                f"识别出 {self.before_cap} 段、超过单份录像 {MAX_CANDIDATES} 段上限，"
                f"已把相邻段合并为 {self.candidates_created} 条候选（{self.merged} 段并入相邻候选）"
            )
        return None


def _save_partial_candidates(
    db: Session,
    *,
    recording: RecordingRef,
    chunk_results: Sequence[tuple[float, Sequence[dict[str, Any]]]],
    product_id: int | None,
) -> int:
    """预算超限时把已成功块的聚合结果先落 pending 候选（部分成果保留）。

    语义自洽注记（审计 19）：部分落库后重跑 = 409 带现有条数（pending_count
    只看未拣选 cloud 候选）——操作者先拣选，全部拣选/登记后可再生成一批；
    与全量成功路径的幂等口径完全同形，不因部分失败追加堆候选。
    """
    if not chunk_results:
        return 0
    outcome = aggregate_segments(merge_segments(chunk_results))
    return create_candidates(
        db, recording=recording, segments=outcome.segments, product_id=product_id
    )


def transcribe_recording(
    db: Session,
    storage: ObjectStorage,
    recording: RecordingRef,
    *,
    product_id: int | None = None,
) -> TranscribeOutcome:
    """整段源录像 → 云 ASR → 停顿聚合 → 落 pending 候选（同步，总预算 300s）。

    调用方（路由）负责：key 未配置 409、已有未拣选 cloud 候选 409、商品 404，
    以及**在调本函数前收口事务**（P1#2 纪律：合成调用不许占着池连接等外网）。
    本函数只抛 ASRError 子类（路由分派 422/502）。

    总预算（审计 19）：提音轨 + 逐块云请求共享 ``TRANSCRIBE_TOTAL_BUDGET_SECONDS``
    ——每块开转前判「累计已耗时 + 下一块预估」（无实测节奏按单块超时上限估，
    有节奏按已完成块均速估），超限即停：已成功块的部分候选先落库，再抛
    ``TranscribeBudgetExceeded``（路由 502 带已转块数与保留条数）。
    """
    started = _now_seconds()
    try:
        video_bytes = storage.get_bytes(recording.object_key)
    except FileNotFoundError as exc:
        raise AudioExtractionError(
            f"源录像字节不存在: {recording.object_key}"
        ) from exc
    wav_bytes = extract_audio_wav(video_bytes)
    chunks = split_wav_chunks(wav_bytes)
    if not chunks:
        raise AudioExtractionError("录像没有可提取的音轨（提取结果为空）")
    chunk_results: list[tuple[float, list[dict[str, Any]]]] = []
    for index, (offset, payload) in enumerate(chunks):
        elapsed = _now_seconds() - started
        estimate = (
            elapsed / len(chunk_results) if chunk_results else ASR_TIMEOUT_SECONDS
        )
        if elapsed + estimate > TRANSCRIBE_TOTAL_BUDGET_SECONDS:
            saved = _save_partial_candidates(
                db, recording=recording, chunk_results=chunk_results, product_id=product_id
            )
            raise TranscribeBudgetExceeded(len(chunk_results), len(chunks), saved)
        chunk_results.append(
            (offset, transcribe_audio(payload, filename=f"chunk-{index + 1}.wav"))
        )
    segments = merge_segments(chunk_results)
    outcome = aggregate_segments(segments)
    created = create_candidates(
        db, recording=recording, segments=outcome.segments, product_id=product_id
    )
    return TranscribeOutcome(
        candidates_created=created,
        segments=len(segments),
        before_cap=outcome.before_cap,
        merged=outcome.merged,
    )
