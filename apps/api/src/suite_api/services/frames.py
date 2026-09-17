"""直播洗帧服务（第 94c 刀，ADR 0053）：已发布切片视频 → 采样帧 → VLM 打分 →
操作者拣选 → 确认帧登记为独立图片资产（kind=image、source_kind=clip_frame）。

链路（**全程同步**：路由是同步 def，FastAPI 丢线程池——ffprobe/ffmpeg 子进程
与厂商 VLM HTTP 都是阻塞调用，与切片真切/ASR 提音轨同形，不占事件循环）：

1. **定位候选帧 = 均匀采样，不是转写时间戳**：资产的 transcript 是纯文本
   （无时间戳；时间戳只活在 clip_candidates，且候选行与资产无关联）——「按句
   定位帧」没有数据可用。故对视频每 5s 抽一帧（采样数上限 24：超上限拉大间隔
   保持均匀，不掐尾），每帧 VLM 打分（1-10 + 一句话），分数 ≥6 的帧成候选
   （上限 8，超出取分高者、按时间序返回）。
2. **候选是请求态，不落库**：帧候选是轻量预览不是资产（0014 同性质），零新表
   零新行；缩略图（宽 ≤480px 的 jpeg）以 base64 data URL 回传，不落对象存储。
   刷新即重算——确认登记是唯一写动作，但**它本身无幂等键**：并发双击/重放会登记
   两份同秒帧（at_second 是自由参数，非拣选 CAS 行；单客户端有 registering 守卫）——\   ADR 0053 Debt，并发场景触发时补。
3. **确认登记**：从**当前已发布指针版**字节抽该秒全尺寸 jpg，走
   ``register_asset(kind=image, source_kind="clip_frame")`` 挂同商品（若源资产
   挂商品）、标题 ``{商品/视频名} · 实拍帧 mm:ss``。登记后与其他 image 同路走
   94a 治理：VLM 出「图片描述」草稿 → 人洗确认 → 发布 → 94b 媒体引用可出图。
4. **血缘最小形态**：audit_log 无备注列且登记动作从无留痕先例（register/
   upload 均不写 audit——留痕是治理动作不是登记动作），溯源由 source_kind
   （=clip_frame）+ 标题（实拍帧 mm:ss）+ 回执 ``cut_from``（切自 A-xxx · vN）
   承载；0026 血缘是派生视图，origin 环自动显示新来源词。

密钥纪律（同 0033/93/94a）：``VLM_API_KEY`` 只经 env 进程内存；**空 key = 端点
409 诚实拒绝**（fail-closed，同 ASR——打分没有本地兜底，无 key 就没有候选）。
"""

import json
import logging
import math
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from suite_api.models import Asset
from suite_api.services.machine_wash import strip_code_fence
from suite_api.services.registration import register_asset
from suite_platform.storage import ObjectStorage

logger = logging.getLogger(__name__)

# 采样参数（spec 口径，改这里就是改产品语义，测试钉死）
SAMPLE_INTERVAL_SECONDS = 5.0  # 每 5s 抽一帧
MAX_SAMPLES = 24  # 采样帧数上限（超出拉大间隔保持均匀，不掐尾）
MIN_SCORE = 6  # VLM 分数 ≥ 此值才成候选
MAX_CANDIDATES = 8  # 候选帧数上限（超出取分高者）
THUMB_MAX_WIDTH = 480  # 缩略图宽上限（px；不放大只缩小）

# ffmpeg/ffprobe 超时（秒）：输入侧 seek 的单帧提取是秒级；留 120s 上限防畸形
# 输入挂死请求线程（与 clips/asr 同值）
_FFMPEG_TIMEOUT = 120

# 打分提示词：判定面只有「画面里看得见的东西」——不编造画面外信息（0009 的
# 看图版），输出钉 JSON 形状（解析在 parse_frame_score，坏形状=该帧无分跳过）
_SCORE_SYSTEM_PROMPT = (
    "你是电商直播的画面抽帧助手。给你直播视频里抽出的一帧，请判断它是否适合"
    "当商品素材图：商品主体是否完整可见、画面是否清晰（转场模糊/贴片遮挡/纯人"
    "脸特写/纯文字屏都算不适合）。只依据画面里确实看得见的内容判断。\n"
    '只输出一个 JSON 对象：{"score": 1到10的整数, "note": "一句话说明"}，'
    "10 分=清晰完整的商品展示画面，1 分=与商品展示无关或模糊不可用。"
    "不要输出 JSON 以外的任何文字。"
)


class FrameError(Exception):
    """洗帧失败基类：调用方按子类分派 HTTP 码（422/502）。"""


class FrameExtractionError(FrameError):
    """抽帧失败（时长探测失败 / ffmpeg 非 0 退出 / 无输出 / 源字节不存在）：
    路由转 422。"""


# ---------------------------------------------------------------- 纯函数（单测钉死）


def sample_points(
    duration_seconds: float,
    *,
    interval: float = SAMPLE_INTERVAL_SECONDS,
    cap: int = MAX_SAMPLES,
) -> list[float]:
    """时长 → 均匀采样点（纯函数）：从 0 起每 ``interval`` 秒一帧，总帧数
    ``ceil(duration/interval)`` 但不超过 ``cap``——超上限时把间隔拉大到
    ``duration/cap``（保持均匀覆盖全程，不掐尾：掐尾会漏掉直播后段的展示帧）。

    时长 ≤0 / interval ≤0 / cap ≤0 → ``[]``（调用方按无帧可采处理）；点值毫秒
    取整（ffmpeg ``-ss`` 收小数秒，回执展示不抖动）。
    """
    if duration_seconds <= 0 or interval <= 0 or cap <= 0:
        return []
    effective = max(interval, duration_seconds / cap)
    count = min(cap, math.ceil(duration_seconds / effective))
    return [round(index * effective, 3) for index in range(count)]


def parse_frame_score(raw: str) -> tuple[int, str] | None:
    """VLM 打分输出 → ``(score, note)``（纯函数）：剥围栏 → JSON → 形状校验。

    坏 JSON / 非对象 / score 不是 1-10 的数 / note 不是字符串 → None（该帧无
    分，跳过不编造）；note 取 strip 后文本（空串合法——分数是闸，话是给人看的）。
    """
    try:
        data = json.loads(strip_code_fence(raw))
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    score = data.get("score")
    note = data.get("note", "")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        return None
    if not 1 <= score <= 10:
        return None
    if not isinstance(note, str):
        return None
    return int(score), note.strip()


def filter_candidates(
    scored: Sequence["ScoredFrame"],
    *,
    min_score: int = MIN_SCORE,
    cap: int = MAX_CANDIDATES,
) -> list["ScoredFrame"]:
    """打分帧 → 候选（纯函数）：分数 ≥ ``min_score`` 者入选；超 ``cap`` 取分高者
    （同分早的优先），输出按时间升序（操作者按播放顺序拣）。"""
    passed = [frame for frame in scored if frame.score >= min_score]
    if len(passed) > cap:
        passed = sorted(passed, key=lambda f: (-f.score, f.at_second))[:cap]
    return sorted(passed, key=lambda f: f.at_second)


def frame_timecode(at_second: float) -> str:
    """秒 → ``mm:ss``（标题形态「实拍帧 mm:ss」；分钟累计不进位到小时，演示
    切片量级 <1h，标题列宽 200 不会撞）。"""
    at_second = max(at_second, 0.0)
    minutes, seconds = divmod(int(math.floor(at_second)), 60)
    return f"{minutes:02d}:{seconds:02d}"


def frame_title(base_name: str, at_second: float) -> str:
    """确认登记的资产标题：``{商品/视频名} · 实拍帧 mm:ss``（spec 钉死形态）。

    基名截 40 字给时间码留位（标题列 String(200)）；空基名兜底「直播切片」。
    """
    base = base_name.strip()[:40] or "直播切片"
    return f"{base} · 实拍帧 {frame_timecode(at_second)}"


# ---------------------------------------------------------------- ffmpeg IO


@dataclass(frozen=True)
class ScoredFrame:
    """一个已打分的采样帧：秒位 + 分数 + VLM 一句话 + 缩略图 jpeg 字节。"""

    at_second: float
    score: int
    note: str
    thumbnail: bytes


@dataclass(frozen=True)
class WashOutcome:
    """洗帧回执（路由照此组装响应）：时长 + 采样数 + 候选。"""

    duration_seconds: float
    sampled: int
    candidates: list[ScoredFrame]


@dataclass(frozen=True)
class VideoRef:
    """洗帧所需的视频资产只读快照（已发布指针版的对象键 + 挂载）。

    路由在**收口事务之前**取出（P1#2 纪律，同 asr.RecordingRef）：ffprobe/
    ffmpeg/VLM 调用合计可达分钟级，不许占着池连接；rollback 会过期 ORM 实例。
    """

    asset_id: int
    version_no: int
    object_key: str
    product_id: int | None
    base_name: str

    @property
    def cut_from(self) -> str:
        """回执血缘锚：切自哪份资产的哪一版（A-xxxx · vN）。"""
        return f"A-{self.asset_id} · v{self.version_no}"


def probe_duration_seconds(video_bytes: bytes) -> float:
    """视频字节 → 时长秒（ffprobe）。失败/非正时长抛 FrameExtractionError。"""
    with tempfile.TemporaryDirectory(prefix="frame-probe-") as tmp:
        in_path = Path(tmp) / "in.mp4"
        in_path.write_bytes(video_bytes)
        command = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(in_path),
        ]
        try:
            completed = subprocess.run(  # noqa: S603 - 参数全为内部构造，无 shell
                command, capture_output=True, timeout=_FFMPEG_TIMEOUT, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise FrameExtractionError(f"ffprobe 无法执行: {exc}") from exc
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", "replace").strip()[:300]
            raise FrameExtractionError(
                f"时长探测失败（ffprobe 退出码 {completed.returncode}）: {detail}"
            )
        try:
            duration = float(completed.stdout.decode("utf-8", "replace").strip())
        except ValueError as exc:
            raise FrameExtractionError("ffprobe 未返回可用时长") from exc
        if duration <= 0:
            raise FrameExtractionError("视频时长为零，没有可采样的帧")
        return duration


def extract_frame_jpeg(
    video_bytes: bytes, at_second: float, *, max_width: int | None = None
) -> bytes:
    """视频字节 → ``at_second`` 处的单帧 jpeg（ffmpeg 输入侧 seek，秒级）。

    ``max_width`` 给定时缩到该宽（``scale=min(W,iw):-2``——只缩不放，高度取偶
    是 yuv420 对齐要求）；不给=全尺寸（确认登记路径用，字节即资产字节）。
    无输出（越界/损坏）或非 0 退出抛 FrameExtractionError——0047 已订正
    ``-ss`` 越过 EOF 会吸附末关键帧，真抽不出帧在这里如实失败。
    """
    with tempfile.TemporaryDirectory(prefix="frame-extract-") as tmp:
        in_path = Path(tmp) / "in.mp4"
        out_path = Path(tmp) / "out.jpg"
        in_path.write_bytes(video_bytes)
        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{max(at_second, 0.0):.3f}",
            "-i",
            str(in_path),
            "-frames:v",
            "1",
        ]
        if max_width is not None:
            # filtergraph 解析器把裸逗号当 filter 分隔符——min(480,iw) 里的逗号
            # 必须用 filtergraph 自己的单引号 quoting 包住（与 shell 无关，
            # subprocess 无 shell 时同样需要；实测 Windows/Linux 同形）
            command += ["-vf", f"scale='min({max_width},iw)':-2"]
        # -pix_fmt yuvj420p：mjpeg 编码器要求 full-range YUV（新版 ffmpeg 对
        # limited-range yuv 直编 mjpeg 直接拒），jpeg 标准本就是 full range
        command += ["-pix_fmt", "yuvj420p", "-q:v", "5", "-y", str(out_path)]
        try:
            completed = subprocess.run(  # noqa: S603 - 参数全为内部构造，无 shell
                command, capture_output=True, timeout=_FFMPEG_TIMEOUT, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise FrameExtractionError(f"ffmpeg 无法执行: {exc}") from exc
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", "replace").strip()[:300]
            raise FrameExtractionError(
                f"抽帧失败（ffmpeg 退出码 {completed.returncode}）: {detail}"
            )
        if not out_path.is_file() or out_path.stat().st_size == 0:
            raise FrameExtractionError(f"ffmpeg 未产出帧字节（{at_second}s 处）")
        return out_path.read_bytes()


# ---------------------------------------------------------------- VLM 打分


def score_frame(thumbnail_bytes: bytes) -> tuple[int, str] | None:
    """缩略图字节 → (分数, 一句话)（VLM 打分；解析失败 None=该帧无分跳过）。

    只抛 ``vlm.VLMError`` 子类（未配置/请求失败），由路由分派 409/502。打分用
    缩略图（≤480px）：判「清不清晰、是不是商品帧」不需要全尺寸，字节小请求快。
    """
    from suite_api.services import vlm  # 延迟导入：vlm 只依赖 settings，避免顶层耦合

    raw = vlm.chat_with_image(_SCORE_SYSTEM_PROMPT, "请给这一帧打分。", thumbnail_bytes)
    return parse_frame_score(raw)


def generate_candidates(video_bytes: bytes) -> WashOutcome:
    """已发布视频字节 → 打分候选（采样 → 缩略 → 逐帧 VLM 打分 → 过滤）。

    单帧打分输出解析失败=该帧跳过（无分不编造）；VLM 请求异常向上冒泡（路由
    502）。串行调用（≤24 帧 × 正常 1-3s/帧在同步请求预算内；VLM 单请求超时
    由 vlm 模块的 20s 纪律兜底）。
    """
    duration = probe_duration_seconds(video_bytes)
    points = sample_points(duration)
    scored: list[ScoredFrame] = []
    for at_second in points:
        thumbnail = extract_frame_jpeg(video_bytes, at_second, max_width=THUMB_MAX_WIDTH)
        verdict = score_frame(thumbnail)
        if verdict is None:
            logger.info("洗帧打分输出不可解析，跳过该帧: at=%s", at_second)
            continue
        score, note = verdict
        scored.append(ScoredFrame(at_second=at_second, score=score, note=note, thumbnail=thumbnail))
    return WashOutcome(
        duration_seconds=duration,
        sampled=len(points),
        candidates=filter_candidates(scored),
    )


# ---------------------------------------------------------------- 确认登记（写路径）


def register_frame_asset(
    db: Session,
    storage: ObjectStorage,
    video: VideoRef,
    *,
    at_second: float,
    vlm_note: str | None = None,
) -> Asset:
    """确认一帧 → 登记为独立图片资产（kind=image、source_kind=clip_frame）。

    - 字节=该秒**全尺寸** jpg（不是打分缩略图——资产字节是原图，94a 口径）；
    - 标题=``{base_name} · 实拍帧 mm:ss``；挂商品透传源视频资产的 product_id；
    - ``register_asset`` 内部照常跑 image 机洗（VLM 出「图片描述」草稿，94a 复用）
      ——vlm_note 只是候选阶段的打分附注，**不冒充描述草稿**（描述草稿由
      describe_image 的专用 prompt 产出，两个任务两种提示词），仅进日志；
    - 回执血缘（``cut_from``）由调用方随响应给出。
    """
    try:
        video_bytes = storage.get_bytes(video.object_key)
    except FileNotFoundError as exc:
        raise FrameExtractionError(f"源视频字节不存在: {video.object_key}") from exc
    frame_bytes = extract_frame_jpeg(video_bytes, at_second)
    if vlm_note:
        logger.info("洗帧登记: from=%s at=%s note=%s", video.cut_from, at_second, vlm_note[:120])
    return register_asset(
        db,
        storage,
        kind="image",
        title=frame_title(video.base_name, at_second),
        content_bytes=frame_bytes,
        filename=None,
        product_id=video.product_id,
        source_kind="clip_frame",
        key_suffix="jpg",
    )
