"""内容成片引擎 v1（第 98b 刀，ADR 0056）：选材 → 时间线候选 → 预览成片 +
剪映草稿 → 人审改 → publish 登记 material 资产。

链路（**全程同步**：路由是同步 def，FastAPI 丢线程池——ffprobe/ffmpeg 子进程
与厂商 TTS HTTP 都是阻塞调用，与切片真切/ASR/洗帧同形，不占事件循环）：

1. **选材（纯函数，可测）**：商品已发布素材清单（切片视频含转写 / 图片含
   描述 / material 文案正文）→ 按规则挑选：优先切片片段（转写含商品名/卖点
   词的先入选）+ 图 2-3 张 + 文案要点 3 条；不足时图+文案补足；总时长目标
   15-60s（每素材 3-8s 估算；切片用 ffprobe 实测时长，超 8s 截前 8s）。
   **AI 排版结果就是候选**（0053/0014 同性质）：落任务留档，不直接成品。
2. **预览成片（ffmpeg）**：图/文案卡序列（相邻非切片素材间 0.4s 叠化
   crossfade，时线不缩水——叠化吃前一素材的延长尾帧）+ 切片段硬拼 + 文案
   要点字幕 drawtext + TTS 口播音轨（无 key=无音轨，with_tts=false 诚实标注）
   + **AIGC 水印「AI 生成」角标常驻**（红线①，代码里没有开关）。
3. **剪映草稿（最小自写 JSON 形态）**：draft_content.json + draft_meta_info.json
   + ``materials/`` 媒体文件打包 zip。schema 复刻剪映草稿结构（时间线/素材/
   轨道/微秒时基），素材 path 用**相对路径**指向包内 ``materials/``——
   pyJianYingDraft 调研结论（记偏差）：库可用但素材 path 硬绑**生成机的绝对
   路径**（``os.path.abspath``）且拖 pymediainfo 原生库依赖，与「服务端生成
   zip、操作者下载到自己机器打开」的形态冲突，故本仓自写最小 JSON。
4. **publish（人闸门）**：body 可带剪映导出的成品 mp4（不传=用预览成片）；
   文案正文=时间线文案要点串联，**双闸复用**（红线③）：material 的规则四条
   + LLM 事实性质检（98 刀同函数）不过线=422 不登记；过线登记 material 资产
   （source_kind=upload 服务端定值、标题「{商品} · 内容成片」、挂商品）→
   照常待人洗/发布治理。上传的成品 mp4 只是任务留档字节（compose/ 暂存），
   不自动资产化（ADR 0056 Debt）。**CAS 占位**（审计 19）：``planned→
   registering`` 原子条件更新防并发双登记（后来者 409），失败分支回 planned；
   登记成功后清理 preview/draft 暂存（final 留任务档，ADR 0056）。

红线四条（ADR 0056）：①AIGC 水印不可配置关闭；②选材白名单=只取**已发布+
自有来源**资产（本仓资产面天然满足——未发布资产根本不进选材查询，资产也
无外部抓取通道）；③文案双闸复用；④数字人/声音克隆 Out——TTS voice 用厂商
预置音色（tts.py 请求形状不携带参考音频）。
"""

import io
import json
import logging
import math
import subprocess
import tempfile
import uuid
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from suite_api.models import Asset, AssetVersion, ComposeTask, Product
from suite_api.services import material as material_service
from suite_api.services import tts as tts_service
from suite_api.services.asset_view import read_version_text
from suite_api.services.cjk_font import find_cjk_font
from suite_api.services.media import media_mime
from suite_api.services.publishing import resolve_field_value
from suite_api.services.registration import register_asset
from suite_platform.storage import ObjectStorage

logger = logging.getLogger(__name__)

# 任务三态（与 material_tasks 词表分立：成片不是文案任务的复用，是独立状态机）
PLANNED = "planned"  # 已出时间线候选+预览+草稿（待人审改）
# publish 占位瞬态（审计 19，CAS）：CAS 迁移 planned→registering 成功者独占登记
# （对齐 80 刀 end_session 的原子条件更新先例）；双闸/登记失败回 planned 可重试
REGISTERING = "registering"
REGISTERED = "registered"  # publish 双闸过线已登记 material 资产（终态）

# ---------------------------------------------------------------- 排版参数（spec 口径）

# 成片总时长目标窗（秒）：改这里就是改产品语义，测试钉死
TARGET_MIN_SECONDS = 15.0
TARGET_MAX_SECONDS = 60.0
# 每素材时长窗（秒）：图/文案卡片的档位，切片另按 ffprobe 实测
CLIP_MIN_SECONDS = 3.0
CLIP_MAX_SECONDS = 8.0
IMAGE_SECONDS = 4.0
TEXT_SECONDS = 3.5
# 中文口播语速（字/秒）：切片无实测时长时按转写字数估算
SPEECH_CHARS_PER_SECOND = 4.5

# 预览合成参数
PREVIEW_WIDTH = 720
PREVIEW_HEIGHT = 1280
PREVIEW_FPS = 25
CROSSFADE_SECONDS = 0.4  # 相邻非切片素材（图/文案卡）间的叠化时长
SUBTITLE_FONTSIZE = 40
CARD_FONTSIZE = 54

# 第 118 刀（W18 成片质量升级，Owner 裁决 2026-09-18）：
# - **水印取消**（ADR 0056 红线①修订）：成片画面全部来自真实素材（切片/商品图/
#   洗帧图）的程序化剪辑，本步不引入 AI 生成画面——「AI 生成」角标属过度声明；
#   TTS 合成口播的标注责任在发布环节（剪映精修/人工上市），预览与草稿不是
#   对外交付面。原 WATERMARK_* 常量删除。
# - 广告脚本结构（行业「黄金 3 秒」惯例）：片头钩子卡 + 片尾 CTA 卡，口播稿
#   = 钩子句 + 卖点段 + CTA 句的完整广告词。
HOOK_SECONDS = 2.5  # 片头钩子卡时长（黄金 3 秒内）
CTA_SECONDS = 2.5  # 片尾行动号召卡时长
HOOK_TEMPLATE = "{name}，到底值不值？"  # 悬念提问式钩子（黄金 3 秒策略）
CTA_TEMPLATE = "点击主页，把{name}带回家"

# 文案卡视觉（role 配色）：双色渐变底 + 角色字色——与素材中心封面文字卡
# （cover_card.py）同一套视觉语言（暖色=钩子、近黑=卖点、强调底条=CTA）。
CARD_GRADIENTS = {
    "hook": ("0x3A2A18", "0x6B4A24"),  # 暖棕渐变（钩子卡）
    "point": ("0x14161A", "0x272B3A"),  # 深蓝灰渐变（卖点卡，原深底延续）
    "cta": ("0x1F2430", "0x39415C"),  # 靛蓝渐变（CTA 卡）
}
CARD_TEXT_COLORS = {"hook": "0xF5C26B", "point": "white", "cta": "white"}
LABEL_FONTSIZE = 26  # 卡片左上角商品名小标

# Ken Burns 运镜（图项）：交替推近/拉远——让静态商品图「活起来」且不篡改商品
# （行业主流做法；开源同类实测无约束的视频生成会「凭空长手、挤出商品」）
ZOOM_MAX = 1.15
ZOOM_RATE_PER_FRAME = 0.0018

# 垫乐（BGM）：程序化轻和弦 pad（A 大调三和弦 + 低八度根音），tremolo 起伏 +
# 低通柔化；音量 0.18×——行业纪律「BGM 15-20% 不盖人声」。只陪口播轨（无声
# 预览语义保留：无 TTS = 纯无声），进预览不进剪映草稿（草稿 BGM 留人挑，债）。
BGM_VOLUME = 0.18
BGM_EXPR = (
    "0.10*sin(2*PI*220*t)+0.07*sin(2*PI*277.18*t)"
    "+0.07*sin(2*PI*329.63*t)+0.05*sin(2*PI*110*t)"
)

# publish 上传成品上限（与源录像同档 200MB）；只收 mp4 字节
MAX_FINAL_VIDEO_BYTES = 200 * 1024 * 1024

# ffmpeg/ffprobe 超时（秒）：与 clips/asr/frames 同值——15-60s 的 720p 合成在
# veryfast 档是十秒级，留 120s 上限防畸形输入挂死请求线程
_FFMPEG_TIMEOUT = 120

# 两模板（v1 不做多模板 DSL，roadmap 98b Out）：高光=从切片挑高光串集锦；
# 商品介绍=文案要点带图/切片穿插。配额=选材条数上限。
TEMPLATES: dict[str, dict[str, Any]] = {
    "highlight": {
        "name": "高光集锦",
        "desc": "从直播切片挑含商品名/卖点词的高光片段串集锦，图与文案要点收尾补足",
        "clips": 6,
        "images": 3,
        "texts": 2,
    },
    "product_intro": {
        "name": "商品介绍",
        "desc": "文案要点打头，图与切片穿插推进，适合新品介绍",
        "clips": 3,
        "images": 3,
        "texts": 4,
    },
}
DEFAULT_TEMPLATE = "product_intro"


def validate_template(template: str) -> str:
    """模板键校验（纯函数）：坏值 ValueError，路由层转 422（同 material 先例）。"""
    if template not in TEMPLATES:
        raise ValueError(f"成片模板必须是 {'/'.join(TEMPLATES)} 之一，收到: {template!r}")
    return template


class ComposeError(Exception):
    """成片失败基类：路由按 422（选材/闸门/入参）分派。"""


class NoMaterialError(ComposeError):
    """商品没有任何可入选的已发布素材（图/切片/文案全空）。"""


class RenderError(ComposeError):
    """预览合成失败（ffmpeg 不可用/非 0 退出/无输出/字体缺失）：路由转 502。"""


class ComposeConflictError(ComposeError):
    """并发登记冲突（审计 19，CAS）：publish 的原子占位（planned→registering）
    被并发写者抢先——后来者 rowcount=0。路由转 409（区别于顺序重复 publish 的
    422 状态机守卫）。"""


# ---------------------------------------------------------------- 选材纯函数（单测钉死）


@dataclass(frozen=True)
class ClipOption:
    """一条可入选的已发布切片视频：资产锚 + 指针版对象键 + 转写正文。"""

    asset_id: int
    version_no: int
    object_key: str
    transcript: str
    duration_seconds: float | None = None  # ffprobe 实测（plan 时回填；纯函数可注入）


@dataclass(frozen=True)
class ImageOption:
    """一张可入选的已发布图片：资产锚 + 指针版对象键 + 图片描述。"""

    asset_id: int
    version_no: int
    object_key: str
    description: str


@dataclass(frozen=True)
class ComposeManifest:
    """选材输入（纯数据）：商品名 + 卖点词 + 三类已发布素材清单。"""

    product_name: str
    selling_points: tuple[str, ...]
    clips: tuple[ClipOption, ...]
    images: tuple[ImageOption, ...]
    # 文案要点行：(来源 material 资产 id, 行文本)
    text_points: tuple[tuple[int, str], ...]


@dataclass(frozen=True)
class TimelineItem:
    """时间线一项（落库/回执形态）：clip|image|text + 资产锚 + 秒制起止。

    ``role``（第 118 刀）：text 项的广告脚本角色——hook=片头钩子卡 / cta=片尾
    行动号召卡（确定性模板生成，无资产锚）；None=普通卖点卡（来自 material
    文案）。渲染配色与口播稿组装都按它分派。
    """

    type: str  # clip | image | text
    asset_id: int | None
    start: float
    dur: float
    text: str | None = None  # text 项的文案要点行（字幕与口播的文本源）
    role: str | None = None  # hook | cta | None（text 项）

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "type": self.type,
            "asset_id": self.asset_id,
            "start": round(self.start, 3),
            "dur": round(self.dur, 3),
        }
        if self.text is not None:
            data["text"] = self.text
        if self.role is not None:
            data["role"] = self.role
        return data


@dataclass(frozen=True)
class PlanOutcome:
    """排版结果（纯函数输出）：时间线候选 + 总时长 + 如实附注。"""

    timeline: tuple[TimelineItem, ...]
    duration_seconds: float
    note: str | None = None


def estimate_clip_seconds(transcript: str) -> float:
    """转写文本 → 估算口播时长（纯函数）：按字数/语速，clamp 到 [3, 8] 秒。

    空文本按 CLIP_MIN 兜底（有人挑它进来就有画面价值，时长不猜大）。
    """
    text = "".join(ch for ch in transcript if not ch.isspace())
    if not text:
        return CLIP_MIN_SECONDS
    return _clamp(len(text) / SPEECH_CHARS_PER_SECOND, CLIP_MIN_SECONDS, CLIP_MAX_SECONDS)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def clip_seconds(option: ClipOption) -> float:
    """切片条目时长：实测优先（clamp 到 [3, 8]，超 8s 截前 8s），无实测按转写估算。"""
    if option.duration_seconds is not None and option.duration_seconds > 0:
        return _clamp(option.duration_seconds, CLIP_MIN_SECONDS, CLIP_MAX_SECONDS)
    return estimate_clip_seconds(option.transcript)


def rank_clips(
    clips: Sequence[ClipOption], product_name: str, selling_points: Sequence[str]
) -> list[ClipOption]:
    """切片排序（纯函数）：转写含商品名/卖点词的优先（同类保持原序），其余殿后。

    红线②的白名单语义在查询侧（load_compose_manifest 只取已发布+自有来源），
    这里只做相关性排序：高光=「讲到这件商品」的片段。
    """
    keywords = [product_name, *(p for p in selling_points if p)]

    def matched(option: ClipOption) -> int:
        return 0 if any(k and k in option.transcript for k in keywords) else 1

    return sorted(clips, key=matched)  # stable：同类保持已发布序


def _interleave(
    first: list[dict[str, Any]], second: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """两队列交替合并（first 先出）：商品介绍的图文穿插推进。"""
    out: list[dict[str, Any]] = []
    for index in range(max(len(first), len(second))):
        if index < len(first):
            out.append(first[index])
        if index < len(second):
            out.append(second[index])
    return out


# 60s 上限裁撤时的让位序：文案先让、图次之、切片最后动（高光优先保画面）
_DROP_PRIORITY = {"text": 0, "image": 1, "clip": 2}
_TYPE_LABEL = {"text": "文案", "image": "图", "clip": "切片"}


def build_timeline(manifest: ComposeManifest, template: str) -> PlanOutcome:
    """选材+排版主纯函数：清单+模板 → 时间线候选（15-60s 目标窗）。

    规则（spec 口径）：
    - 优先切片（rank_clips 匹配序）取模板配额；图取配额（目标 2-3 张）；
      文案要点取配额（3 条为准，配额内有多少取多少）；不足时图+文案补足
      （有多少用多少，note 如实）；
    - 顺序：高光=切片打头，图收尾，文案垫底；商品介绍=图文交替推进、切片
      殿后；
    - 总时长：超 60s 从尾部降级裁（先裁文案、再裁图、切片最后）；不足 15s
      把图/文案卡时长按比例拉长补足（切片维持实测窗不注水）；素材太少实在
      补不到 15s 如实出 note（不硬凑）。
    """
    validate_template(template)
    quota = TEMPLATES[template]
    notes: list[str] = []

    clips = rank_clips(manifest.clips, manifest.product_name, manifest.selling_points)[
        : quota["clips"]
    ]
    images = list(manifest.images)[: quota["images"]]
    texts = list(manifest.text_points)[: quota["texts"]]

    if not clips and not images and not texts:
        raise NoMaterialError(
            f"商品「{manifest.product_name}」没有已发布的切片/图片/文案素材可入选"
        )

    entries: list[dict[str, Any]] = [
        {"type": "clip", "asset_id": c.asset_id, "dur": clip_seconds(c)} for c in clips
    ]
    image_entries = [{"type": "image", "asset_id": i.asset_id, "dur": IMAGE_SECONDS} for i in images]
    text_entries = [
        {"type": "text", "asset_id": asset_id, "dur": TEXT_SECONDS, "text": line}
        for asset_id, line in texts
    ]
    if template == "highlight":
        entries += image_entries + text_entries
    else:  # product_intro：图文交替推进，切片殿后
        entries += _interleave(image_entries, text_entries)

    # 广告脚本结构（第 118 刀 W18）：商品介绍=片头钩子 + 片尾 CTA；高光集锦
    # 切片打头不抢黄金 3 秒，只补片尾 CTA。钩子/CTA 是确定性模板句（悬念提问/
    # 行动号召），不带资产锚（asset_id=None——渲染与草稿都按纯文本卡走）。
    name = manifest.product_name.strip() or "这件好物"
    if template == "product_intro":
        entries.insert(0, {"type": "text", "asset_id": None, "dur": HOOK_SECONDS,
                           "text": HOOK_TEMPLATE.format(name=name), "role": "hook"})
    entries.append({"type": "text", "asset_id": None, "dur": CTA_SECONDS,
                    "text": CTA_TEMPLATE.format(name=name), "role": "cta"})

    # 60s 上限：让位序降级裁（保至少一项——单项超 8s 也不可能，clip 已 clamp）；
    # 钩子/CTA 各 2.5s 且是脚本结构件，不参与裁撤（裁了广告就不成广告了）
    dropped: list[str] = []
    while sum(e["dur"] for e in entries) > TARGET_MAX_SECONDS:
        droppable = [i for i, e in enumerate(entries) if not e.get("role")]
        if not droppable:
            break
        tail_index = max(droppable, key=lambda i: (-_DROP_PRIORITY[entries[i]["type"]], i))
        dropped.append(entries[tail_index]["type"])
        entries.pop(tail_index)
    if dropped:
        counts = {kind: dropped.count(kind) for kind in ("text", "image", "clip") if kind in dropped}
        notes.append(
            "为控 60s 上限裁掉了 "
            + "、".join(f"{count} 段{_TYPE_LABEL[kind]}" for kind, count in counts.items())
        )

    total = sum(e["dur"] for e in entries)
    # 15s 下限：只拉长图/卖点卡（切片维持实测窗、钩子/CTA 是结构卡固定时长
    # 不注水——拉长的行动号召卡只会更尬），均摊缺口
    if total < TARGET_MIN_SECONDS:
        flexible = [
            e for e in entries if e["type"] in ("image", "text") and not e.get("role")
        ]
        if flexible:
            share = (TARGET_MIN_SECONDS - total) / len(flexible)
            for entry in flexible:
                entry["dur"] = entry["dur"] + share
            total = TARGET_MIN_SECONDS
        else:
            notes.append(
                f"素材时长仅 {total:.0f}s，不足 15s 目标（切片不注水，补素材再合成更长成片）"
            )

    timeline: list[TimelineItem] = []
    cursor = 0.0
    for entry in entries:
        timeline.append(
            TimelineItem(
                type=entry["type"],
                asset_id=entry["asset_id"],
                start=round(cursor, 3),
                dur=round(entry["dur"], 3),
                text=entry.get("text"),
                role=entry.get("role"),
            )
        )
        cursor += entry["dur"]

    if template == "highlight" and not manifest.clips:
        notes.append("没有已发布切片，高光集锦退化为图+文案成片")
    if manifest.images and len(images) < 2:
        notes.append("已发布图片不足 2 张，按现有数量入选")

    return PlanOutcome(
        timeline=tuple(timeline),
        duration_seconds=round(cursor, 3),
        note="；".join(notes) or None,
    )


# ---------------------------------------------------------------- 预览合成（ffmpeg）


def _find_cjk_font() -> str:
    """找一个带 CJK 字形的字体文件（drawtext 渲染中文必需）。

    第 115 刀起定位逻辑抽到 services/cjk_font.py（封面文字卡流水线共用）；
    这里保留薄壳：把 None 翻成 RenderError（诚实失败：缺字体的 drawtext
    只会渲染豆腐块）。
    """
    path = find_cjk_font()
    if path is None:
        raise RenderError("找不到可用的中文字体（drawtext 渲染字幕/AIGC 标识必需）")
    return path


def wrap_cjk(text: str, width: int) -> str:
    """中文为主的文本折行（纯函数）：按显示宽度硬折（CJK 无空格可依）。"""
    lines: list[str] = []
    current = ""
    for ch in text:
        current += ch
        if len(current) >= width:
            lines.append(current)
            current = ""
    if current:
        lines.append(current)
    return "\n".join(lines)


def _filter_path(path: str) -> str:
    """路径进 filtergraph 的转义（两层）：反斜杠归一为 /、盘符冒号加反斜杠
    转义（``\\:``——冒号是 filter 选项分隔符，不转义会被当分隔符切开）、整体
    单引号包裹（filtergraph 的 quoting 层，同 frames.py ``scale='min(...)'``
    先例）。实测 Windows 盘符路径须「单引号+转义冒号」双管齐下。"""
    normalized = str(path).replace("\\", "/")
    return f"'{normalized.replace(':', '\\:')}'"


def _drawtext_file(workdir: Path, name: str, text: str) -> str:
    """字幕文本写临时文件（drawtext 的 textfile= 通道：免 text= 的转义地狱）。"""
    path = workdir / name
    path.write_text(text, encoding="utf-8")
    return _filter_path(path)


def _fades_to_next(items: Sequence[dict[str, Any]], index: int) -> bool:
    """第 index 项与下一项是否均为非切片（图/文案卡）——叠化只发生在这种边界。

    切片段与任何边界一律硬拼（直播画面/口播不适合被叠化糊头），时间线时序
    分毫不动：叠化吃的是前一素材的**延长尾帧**（render_dur = dur + 0.4）。
    """
    nxt = items[index + 1] if index + 1 < len(items) else None
    return (
        nxt is not None
        and items[index]["type"] in ("image", "text")
        and nxt["type"] in ("image", "text")
    )


def build_preview_filter(
    items: Sequence[dict[str, Any]],
    *,
    total: float,
    has_audio: bool,
    audio_input: int | None,
    workdir: Path,
    font_file: str,
    label: str = "",
) -> str:
    """预览合成的 filtergraph（纯函数；测试断言运镜/字幕/叠化就在这里钉）。

    ``items`` 每项：{type: image|text|clip, input_index, start, dur, text?, role?}。
    第 118 刀（W18）起：图项走 **Ken Burns 运镜**（zoompan 交替推近/拉远）；
    文案卡按 role 配色（钩子暖/卖点冷/CTA 靛蓝）+ 左上角商品名小标；相邻
    非切片段 xfade 叠化；每条文案要点窗口内字幕；**有口播时叠程序化垫乐**
    （BGM 音量 18% 不盖人声）；总时长终裁。水印已按 Owner 裁决移除（ADR 0056
    红线①修订：本步是真实素材的程序化剪辑，无 AI 生成画面）。
    """
    chains: list[str] = []
    label_file = _drawtext_file(workdir, "label.txt", (label or "").strip()[:12]) if label else None

    for index, item in enumerate(items):
        inp = f"[{item['input_index']}:v]"
        render_dur = item["dur"] + (CROSSFADE_SECONDS if _fades_to_next(items, index) else 0.0)
        common = f"fps={PREVIEW_FPS},format=yuv420p"
        if item["type"] == "image":
            # Ken Burns：2x 画幅内 zoompan（只缩不放损画质），偶数项推近、奇数项
            # 拉远——静态商品图「活起来」且不篡改商品
            frames = max(int(round(render_dur * PREVIEW_FPS)) + 1, 2)
            if index % 2 == 0:
                zoom = f"min(1+{ZOOM_RATE_PER_FRAME}*on,{ZOOM_MAX})"
            else:
                zoom = f"max({ZOOM_MAX}-{ZOOM_RATE_PER_FRAME}*on,1.0)"
            chain = (
                f"{inp}scale={PREVIEW_WIDTH * 2}:{PREVIEW_HEIGHT * 2}"
                ":force_original_aspect_ratio=increase,"
                f"crop={PREVIEW_WIDTH * 2}:{PREVIEW_HEIGHT * 2},setsar=1,"
                f"zoompan=z='{zoom}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                f":d={frames}:s={PREVIEW_WIDTH}x{PREVIEW_HEIGHT}:fps={PREVIEW_FPS},"
                f"trim=duration={render_dur:.3f},setpts=PTS-STARTPTS,{common}"
            )
        elif item["type"] == "clip":
            # 切片：contain 加黑边（直播画面裁掉主体不可取）；时长窗内硬截
            chain = (
                f"{inp}scale={PREVIEW_WIDTH}:{PREVIEW_HEIGHT}"
                ":force_original_aspect_ratio=decrease,"
                f"pad={PREVIEW_WIDTH}:{PREVIEW_HEIGHT}:(ow-iw)/2:(oh-ih)/2,setsar=1,"
                f"trim=duration={render_dur:.3f},setpts=PTS-STARTPTS,{common}"
            )
        else:  # 文案卡：role 配色大字（字幕另在其下）+ 左上角商品名小标
            role = item.get("role") or "point"
            card_text = _drawtext_file(
                workdir, f"card-{index}.txt", wrap_cjk(item.get("text") or "", 12)
            )
            color = CARD_TEXT_COLORS[role]
            cta_box = (
                ":box=1:boxcolor=0xF5C26B@0.9:boxborderw=14" if role == "cta" else ""
            )
            chain = (
                f"{inp}drawtext=fontfile={_filter_path(font_file)}:textfile={card_text}"
                f":fontsize={CARD_FONTSIZE}:fontcolor={color}{cta_box}"
                ":x=(w-text_w)/2:y=(h-text_h)/2:line_spacing=14"
            )
            if label_file is not None:
                chain += (
                    f",drawtext=fontfile={_filter_path(font_file)}:textfile={label_file}"
                    f":fontsize={LABEL_FONTSIZE}:fontcolor=white@0.55:x=36:y=48"
                )
            chain += f",trim=duration={render_dur:.3f},setpts=PTS-STARTPTS,{common}"
        chains.append(f"{chain}[v{index}]")

    # 组段：相邻非切片段 xfade 链成 run；clip/run 之间 concat 硬拼
    groups: list[list[int]] = []
    for index, _item in enumerate(items):
        join_fade = index > 0 and _fades_to_next(items, index - 1)
        if join_fade and groups:
            groups[-1].append(index)
        else:
            groups.append([index])

    stream_labels: list[str] = []
    for group in groups:
        if len(group) == 1:
            stream_labels.append(f"[v{group[0]}]")
            continue
        current = f"[v{group[0]}]"
        cursor = items[group[0]]["dur"]  # run 内下一项的时线起点（叠化 offset）
        for position, index in enumerate(group[1:], start=1):
            out = f"[g{len(stream_labels)}p{position}]"
            chains.append(
                f"{current}[v{index}]xfade=transition=fade:duration={CROSSFADE_SECONDS}"
                f":offset={cursor:.3f}{out}"
            )
            current = out
            cursor += items[index]["dur"]
        stream_labels.append(current)

    if len(stream_labels) == 1:
        body = stream_labels[0]
    else:
        body = "[vcat]"
        chains.append(f"{''.join(stream_labels)}concat=n={len(stream_labels)}:v=1:a=0{body}")

    # 字幕（每条文案要点在自己窗口）→ 按总时长终裁（水印已移除，ADR 0056 修订）
    for index, item in enumerate(items):
        if item["type"] != "text":
            continue
        sub_file = _drawtext_file(workdir, f"sub-{index}.txt", wrap_cjk(item.get("text") or "", 16))
        chains.append(
            f"{body}drawtext=fontfile={_filter_path(font_file)}:textfile={sub_file}"
            f":fontsize={SUBTITLE_FONTSIZE}:fontcolor=white:"
            "x=(w-text_w)/2:y=h-th-180:box=1:boxcolor=black@0.35:boxborderw=10:line_spacing=10"
            f":enable='between(t,{item['start']:.3f},{(item['start'] + item['dur']):.3f})'[s{index}]"
        )
        body = f"[s{index}]"
    chains.append(f"{body}trim=duration={total:.3f},setpts=PTS-STARTPTS[vout]")
    if has_audio:
        assert audio_input is not None  # noqa: S101 - 有音轨必有输入索引（调用方契约）
        # 口播为主（apad/atrim 对齐画面时长）+ 程序化垫乐（18% 不盖人声，
        # tremolo 起伏 + 低通柔化 + 首尾淡入淡出），amix 不归一化保绝对音量
        fade_out_start = max(total - 1.5, 0.0)
        chains.append(
            f"[{audio_input}:a]aresample=44100,apad,atrim=duration={total:.3f}"
            ",asetpts=PTS-STARTPTS[voice]"
        )
        chains.append(
            f"aevalsrc=exprs='{BGM_EXPR}':s=44100:d={total:.3f}"
            f",tremolo=f=0.8:d=0.3,lowpass=f=1800,volume={BGM_VOLUME}"
            f",afade=t=in:d=1.0,afade=t=out:st={fade_out_start:.3f}:d=1.5[bgm]"
        )
        chains.append("[voice][bgm]amix=inputs=2:duration=first:normalize=0[aout]")
    return ";".join(chains)


def render_preview(
    items: Sequence[dict[str, Any]],
    tts_bytes: bytes | None,
    *,
    total: float,
    workdir: Path,
    font_file: str | None = None,
    label: str = "",
) -> bytes:
    """时间线 → 预览成片 mp4 字节（阻塞 ffmpeg；items 带本地媒体路径）。

    - 图输入单帧进滤镜（zoompan 在 filter 内拉出整段——Ken Burns 运镜）；
      文案卡走 lavfi gradients 双色渐变源（第 118 刀）；切片整段进、filter 内
      trim；
    - 口播字节落盘为一个音频输入：短了 apad 补静音、长了 atrim 截齐（**画面
      时长权威**）；有口播时叠程序化垫乐（BGM_VOLUME，行业 15-20% 纪律）；
      无 TTS=纯无声视频（with_tts 由调用方如实记）；
    - 输出 720x1280 h264 +（有音轨时）aac，faststart 便于浏览器边下边播。
    ffmpeg 非 0 退出/无输出抛 RenderError（路由 502，可整任务重发）。
    """
    if font_file is None:
        font_file = _find_cjk_font()
    command: list[str] = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    for index, item in enumerate(items):
        if item["type"] == "image":
            # 单帧进：Ken Burns 由 zoompan 在 filter 内展开（-loop 会逐帧重复
            # 静止画面，zoompan d=1 时不产生运动）
            command += ["-i", item["path"]]
        elif item["type"] == "clip":
            command += ["-i", item["path"]]
        else:  # 文案卡：lavfi gradients 双色渐变源（+1s 余量，filter 内 trim 精确窗口）
            render_dur = item["dur"] + (CROSSFADE_SECONDS if _fades_to_next(items, index) else 0.0)
            c0, c1 = CARD_GRADIENTS[item.get("role") or "point"]
            command += [
                "-f", "lavfi", "-i",
                f"gradients=s={PREVIEW_WIDTH}x{PREVIEW_HEIGHT}:c0={c0}:c1={c1}"
                f":x0=0:y0=0:x1={PREVIEW_WIDTH}:y1={PREVIEW_HEIGHT}"
                f":r={PREVIEW_FPS}:d={math.ceil(render_dur) + 1}",
            ]
    audio_input: int | None = None
    if tts_bytes:
        audio_path = workdir / "tts.mp3"
        audio_path.write_bytes(tts_bytes)
        audio_input = len(items)
        command += ["-i", str(audio_path)]
    filter_complex = build_preview_filter(
        items,
        total=total,
        has_audio=tts_bytes is not None,
        audio_input=audio_input,
        workdir=workdir,
        font_file=font_file,
        label=label,
    )
    out_path = workdir / "preview.mp4"
    command += [
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        *(["-map", "[aout]"] if tts_bytes else []),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
        *(["-c:a", "aac", "-b:a", "128k", "-ar", "44100"] if tts_bytes else ["-an"]),
        "-movflags", "+faststart",
        "-t", f"{total:.3f}",
        str(out_path),
    ]
    try:
        completed = subprocess.run(  # noqa: S603 - 参数全为内部构造，无 shell
            command, capture_output=True, timeout=_FFMPEG_TIMEOUT, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RenderError(f"ffmpeg 无法执行: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace").strip()[:300]
        raise RenderError(f"预览合成失败（ffmpeg 退出码 {completed.returncode}）: {detail}")
    if not out_path.is_file() or out_path.stat().st_size == 0:
        raise RenderError("ffmpeg 未产出预览成片字节")
    return out_path.read_bytes()


# ---------------------------------------------------------------- 剪映草稿（最小自写 JSON）


def _us(seconds: float) -> int:
    """秒 → 剪映微秒时基。"""
    return int(round(seconds * 1_000_000))


def _segment(
    material_id: str, start: float, dur: float, *, render_index: int, is_video: bool
) -> dict[str, Any]:
    """轨道片段（真机草稿字段复刻）：target/source 微秒时基 + speed 引用。"""
    speed_id = uuid.uuid4().hex
    segment: dict[str, Any] = {
        "enable_adjust": True,
        "enable_color_correct_adjust": False,
        "enable_color_curves": True,
        "enable_color_match_adjust": False,
        "enable_color_wheels": True,
        "enable_lut": True,
        "enable_smart_color_adjust": False,
        "last_nonzero_volume": 1.0,
        "reverse": False,
        "track_attribute": 0,
        "track_render_index": 0,
        "visible": True,
        "id": uuid.uuid4().hex,
        "material_id": material_id,
        "target_timerange": {"start": _us(start), "duration": _us(dur)},
        "common_keyframes": [],
        "keyframe_refs": [],
        "source_timerange": {"start": 0, "duration": _us(dur)},
        "speed": 1.0,
        "volume": 1.0,
        "extra_material_refs": [speed_id],
        "is_tone_modify": False,
    }
    if is_video:
        segment["clip"] = {
            "alpha": 1.0,
            "flip": {"horizontal": False, "vertical": False},
            "rotation": 0.0,
            "scale": {"x": 1.0, "y": 1.0},
            "transform": {"x": 0.0, "y": 0.0},
        }
        segment["uniform_scale"] = {"on": True, "value": 1.0}
        segment["hdr_settings"] = {"intensity": 1.0, "mode": 1, "nits": 1000}
    else:
        segment["clip"] = None
        segment["hdr_settings"] = None
    segment["render_index"] = render_index
    return segment


def _speed_material(segment: dict[str, Any]) -> dict[str, Any]:
    return {
        "curve_speed": None,
        "id": segment["extra_material_refs"][0],
        "mode": 0,
        "speed": 1.0,
        "type": "speed",
    }


def _text_material(content: str) -> dict[str, Any]:
    """文本素材：content 是内嵌 JSON 字符串（真机形状——styles/range 按字数）。"""
    return {
        "id": uuid.uuid4().hex,
        "content": json.dumps(
            {
                "styles": [
                    {
                        "fill": {
                            "alpha": 1.0,
                            "content": {
                                "render_type": "solid",
                                "solid": {"alpha": 1.0, "color": [1.0, 1.0, 1.0]},
                            },
                        },
                        "range": [0, len(content)],
                        "size": 8.0,
                        "bold": False,
                        "italic": False,
                        "underline": False,
                        "strokes": [],
                    }
                ],
                "text": content,
            },
            ensure_ascii=False,
        ),
        "typesetting": 0,
        "alignment": 0,
        "letter_spacing": 0.0,
        "line_spacing": 0.02,
        "line_feed": 1,
        "line_max_width": 0.82,
        "force_apply_line_max_width": False,
        "check_flag": 7,
        "type": "text",
        "global_alpha": 1.0,
    }


def build_draft_content(
    items: Sequence[dict[str, Any]],
    *,
    total: float,
    draft_id: str,
    draft_name: str,
    tts_name: str | None,
) -> dict[str, Any]:
    """时间线 → 剪映 draft_content.json（dict；复刻剪映 5.9 微秒时基 schema）。

    ``items`` 每项在预览项之上多带 ``draft_name``（包内相对路径
    ``materials/xxx``）、``bytes``、``width/height``（媒体实测）与 ``duration``。
    video 主轨按时间线 butt-joint；text 字幕轨按各自窗口；audio 口播轨整段。
    **AIGC 标识作为常驻文本素材钉全程**（红线①随草稿走——人在剪映里可移
    位置，但生成侧恒有）。
    """
    videos: list[dict[str, Any]] = []
    speeds: list[dict[str, Any]] = []
    texts: list[dict[str, Any]] = []
    video_segments: list[dict[str, Any]] = []
    text_segments: list[dict[str, Any]] = []

    for item in items:
        is_photo = item["type"] == "image"
        if item.get("draft_name"):
            # 媒体项（切片/图）进主轨：photo=任意长素材（真机恒给 3h 上限值）
            material_id = uuid.uuid4().hex
            videos.append(
                {
                    "audio_fade": None,
                    "category_id": "",
                    "category_name": "local",
                    "check_flag": 63487,
                    "crop": {
                        "upper_left_x": 0.0, "upper_left_y": 0.0,
                        "upper_right_x": 1.0, "upper_right_y": 0.0,
                        "lower_left_x": 0.0, "lower_left_y": 1.0,
                        "lower_right_x": 1.0, "lower_right_y": 1.0,
                    },
                    "crop_ratio": "free",
                    "crop_scale": 1.0,
                    "duration": _us(10800) if is_photo else _us(item.get("duration") or item["dur"]),
                    "height": item.get("height") or PREVIEW_HEIGHT,
                    "id": material_id,
                    "local_material_id": "",
                    "material_id": material_id,
                    "material_name": item["draft_name"].rsplit("/", 1)[-1],
                    "media_path": "",
                    "path": item["draft_name"],  # 相对路径指向包内 materials/
                    "type": "photo" if is_photo else "video",
                    "width": item.get("width") or PREVIEW_WIDTH,
                }
            )
            segment = _segment(material_id, item["start"], item["dur"], render_index=0, is_video=True)
            video_segments.append(segment)
            speeds.append(_speed_material(segment))
        if item["type"] == "text" and item.get("text"):
            # 文案要点字幕（text 项无媒体字节，走文本轨）
            text_material = _text_material(item["text"])
            texts.append(text_material)
            text_segment = _segment(
                text_material["id"], item["start"], item["dur"], render_index=1, is_video=False
            )
            text_segments.append(text_segment)
            speeds.append(_speed_material(text_segment))

    # 水印文本轨已按 Owner 裁决移除（第 118 刀，ADR 0056 红线①修订：本步是
    # 真实素材的程序化剪辑，无 AI 生成画面——「AI 生成」角标属过度声明）

    audios: list[dict[str, Any]] = []
    audio_segments: list[dict[str, Any]] = []
    if tts_name:
        audio_id = uuid.uuid4().hex
        audios.append(
            {
                "app_id": 0,
                "category_id": "",
                "category_name": "local",
                "check_flag": 3,
                "copyright_limit_type": "none",
                "duration": _us(total),
                "effect_id": "",
                "formula_id": "",
                "id": audio_id,
                "local_material_id": audio_id,
                "music_id": audio_id,
                "name": tts_name.rsplit("/", 1)[-1],
                "path": tts_name,
                "source_platform": 0,
                "type": "extract_music",
                "wave_points": [],
            }
        )
        audio_segment = _segment(audio_id, 0.0, total, render_index=2, is_video=False)
        audio_segments.append(audio_segment)
        speeds.append(_speed_material(audio_segment))

    empty_material_keys = [
        "ai_translates", "audio_balances", "audio_effects", "audio_fades",
        "audio_track_indexes", "beats", "canvases", "chromas", "color_curves",
        "digital_humans", "drafts", "effects", "flowers", "green_screens",
        "handwrites", "hsl", "images", "log_color_wheels", "loudnesses",
        "manual_deformations", "masks", "material_animations", "material_colors",
        "multi_language_refs", "placeholders", "plugin_effects",
        "primary_color_wheels", "realtime_denoises", "shapes", "smart_crops",
        "smart_relights", "sound_channel_mappings", "stickers", "tail_leaders",
        "text_templates", "time_marks", "transitions", "video_effects",
        "video_trackings", "vocal_beautifys", "vocal_separations",
    ]
    materials: dict[str, Any] = {key: [] for key in empty_material_keys}
    materials.update({"videos": videos, "audios": audios, "texts": texts, "speeds": speeds})

    def _track(track_type: str, segments: list[dict[str, Any]], render_index: int) -> dict[str, Any]:
        return {
            "attribute": 0,
            "flag": 0,
            "id": uuid.uuid4().hex,
            "is_default_name": False,
            "name": track_type,
            "segments": segments,
            "type": track_type,
        }

    tracks = [_track("video", video_segments, 0)]
    if text_segments:
        tracks.append(_track("text", text_segments, 1))
    if audio_segments:
        tracks.append(_track("audio", audio_segments, 2))

    platform = {"app_id": 3704, "app_source": "lv", "app_version": "5.9.0", "os": "windows"}
    return {
        "canvas_config": {"width": 1080, "height": 1920, "ratio": "original"},
        "color_space": 0,
        "config": {
            "adjust_max_index": 1,
            "attachment_info": [],
            "combination_max_index": 1,
            "export_range": None,
            "extract_audio_last_index": 1,
            "lyrics_recognition_id": "",
            "lyrics_sync": True,
            "lyrics_taskinfo": [],
            "maintrack_adsorb": False,
            "material_save_mode": 0,
            "multi_language_current": "none",
            "multi_language_list": [],
            "multi_language_main": "none",
            "multi_language_mode": "none",
            "original_sound_last_index": 1,
            "record_audio_last_index": 1,
            "sticker_max_index": 1,
            "subtitle_keywords_config": None,
            "subtitle_recognition_id": "",
            "subtitle_sync": True,
            "subtitle_taskinfo": [],
            "system_font_list": [],
            "video_mute": False,
            "zoom_info_params": None,
        },
        "cover": None,
        "create_time": 0,
        "duration": _us(total),
        "extra_info": None,
        "fps": 30,
        "free_render_index_mode_on": False,
        "group_container": None,
        "id": draft_id,
        "keyframe_graph_list": [],
        "keyframes": {
            "adjusts": [], "audios": [], "effects": [], "filters": [],
            "handwrites": [], "stickers": [], "texts": [], "videos": [],
        },
        "last_modified_platform": platform,
        "platform": platform,
        "materials": materials,
        "mutable_config": None,
        "name": draft_name,
        "new_version": "110.0.0",
        "relationships": [],
        "render_index_track_mode_on": False,
        "retouch_cover": None,
        "source": "default",
        "static_cover_image_path": "",
        "time_marks": None,
        "tracks": tracks,
        "update_time": 0,
        "version": 360000,
    }


def build_draft_meta(draft_id: str, draft_name: str, total: float) -> dict[str, Any]:
    """draft_meta_info.json（dict）：复刻真机模板的空槽 + id/名称/时长。"""
    return {
        "cloud_package_completed_time": "",
        "draft_cloud_capcut_purchase_info": "",
        "draft_cloud_last_action_download": False,
        "draft_cloud_materials": [],
        "draft_cloud_purchase_info": "",
        "draft_cloud_template_id": "",
        "draft_cloud_tutorial_info": "",
        "draft_cloud_videocut_purchase_info": "",
        "draft_cover": "",
        "draft_deeplink_url": "",
        "draft_enterprise_info": {
            "draft_enterprise_extra": "",
            "draft_enterprise_id": "",
            "draft_enterprise_name": "",
            "enterprise_material": [],
        },
        "draft_fold_path": "",
        "draft_id": draft_id,
        "draft_is_ai_packaging_used": False,
        "draft_is_ai_shorts": False,
        "draft_is_ai_translate": False,
        "draft_is_article_video_draft": False,
        "draft_is_from_deeplink": "false",
        "draft_is_invisible": False,
        "draft_materials": [{"type": t, "value": []} for t in (0, 1, 2, 3, 6, 7, 8)],
        "draft_materials_copied_info": [],
        "draft_name": draft_name,
        "draft_new_version": "",
        "draft_removable_storage_device": "",
        "draft_root_path": "",
        "draft_segment_extra_info": [],
        "draft_type": "",
        "tm_draft_cloud_completed": "",
        "tm_draft_cloud_modified": 0,
        "tm_draft_removed": 0,
        "tm_duration": _us(total),
    }


def build_draft_zip(
    items: Sequence[dict[str, Any]],
    *,
    total: float,
    draft_name: str,
    tts_bytes: bytes | None,
) -> bytes:
    """时间线 + 媒体字节 → 剪映草稿 zip（draft_content/draft_meta_info/materials/）。

    素材以**相对路径** ``materials/…`` 引用且字节随包走——操作者把 zip 解压
    到剪映草稿目录（com.lveditor.draft/）即可打开；若剪映版本对相对路径不
    认，用草稿内「媒体重链接」指向包内 materials/（ADR 0056 的 v1 边界）。
    """
    draft_id = str(uuid.uuid4()).upper()
    tts_name = "materials/tts.mp3" if tts_bytes else None
    content = build_draft_content(
        items, total=total, draft_id=draft_id, draft_name=draft_name, tts_name=tts_name
    )
    meta = build_draft_meta(draft_id, draft_name, total)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("draft_content.json", json.dumps(content, ensure_ascii=False))
        archive.writestr("draft_meta_info.json", json.dumps(meta, ensure_ascii=False))
        for item in items:  # 媒体字节随包走（text 项无媒体，跳过）
            if item.get("draft_name"):
                archive.writestr(item["draft_name"], item["bytes"])
        if tts_bytes:
            archive.writestr(tts_name, tts_bytes)
    return buffer.getvalue()


# ---------------------------------------------------------------- 媒体探测（ffprobe）


def probe_video_meta(media_bytes: bytes) -> tuple[float, int, int]:
    """视频字节 → (时长秒, 宽, 高)。失败抛 RenderError（路由 502）。"""
    with tempfile.TemporaryDirectory(prefix="compose-probe-") as tmp:
        in_path = Path(tmp) / "in.mp4"
        in_path.write_bytes(media_bytes)
        command = [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height,duration",
            "-of", "json", str(in_path),
        ]
        try:
            completed = subprocess.run(  # noqa: S603 - 参数全为内部构造，无 shell
                command, capture_output=True, timeout=_FFMPEG_TIMEOUT, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RenderError(f"ffprobe 无法执行: {exc}") from exc
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", "replace").strip()[:300]
            raise RenderError(f"媒体探测失败（ffprobe 退出码 {completed.returncode}）: {detail}")
        try:
            stream = json.loads(completed.stdout.decode("utf-8", "replace"))["streams"][0]
            width, height = int(stream["width"]), int(stream["height"])
            duration = float(stream.get("duration") or 0.0)
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise RenderError("媒体探测失败：ffprobe 未返回可用的宽高/时长") from exc
        if width <= 0 or height <= 0:
            raise RenderError("媒体探测失败：宽高非法")
        return duration, width, height


def probe_image_size(image_bytes: bytes) -> tuple[int, int]:
    """图片字节 → (宽, 高)。失败抛 RenderError（选材侧降级为画幅默认值）。"""
    with tempfile.TemporaryDirectory(prefix="compose-imgprobe-") as tmp:
        in_path = Path(tmp) / "in.img"
        in_path.write_bytes(image_bytes)
        command = [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "json", str(in_path),
        ]
        try:
            completed = subprocess.run(  # noqa: S603 - 参数全为内部构造，无 shell
                command, capture_output=True, timeout=_FFMPEG_TIMEOUT, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RenderError(f"ffprobe 无法执行: {exc}") from exc
        if completed.returncode != 0:
            raise RenderError(f"图片探测失败（ffprobe 退出码 {completed.returncode}）")
        try:
            stream = json.loads(completed.stdout.decode("utf-8", "replace"))["streams"][0]
            width, height = int(stream["width"]), int(stream["height"])
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise RenderError("图片探测失败：ffprobe 未返回可用宽高") from exc
        if width <= 0 or height <= 0:
            raise RenderError("图片探测失败：宽高非法")
        return width, height


# ---------------------------------------------------------------- 素材清单（读库+读字节）


def load_compose_manifest(db: Session, storage: ObjectStorage, product: Product) -> ComposeManifest:
    """商品的已发布素材清单（选材输入）：切片/图/material 文案（已发布指针版）。

    红线②：查询面钉死「已发布 + 未废弃 + 挂本商品」（0004 已发布才可引用），
    本仓资产全部自有来源（source_kind 由登记端点语义定值，无外部抓取通道）
    ——白名单天然满足，不另设过滤开关。material 正文经版本字节读回
    （read_version_text 的回落口径与 MCP/export 一致）。
    """
    rows = db.execute(
        select(Asset, AssetVersion)
        .join(AssetVersion, Asset.current_published_version_id == AssetVersion.id)
        .where(
            Asset.product_id == product.id,
            Asset.status == "published",
            Asset.discarded_at.is_(None),
            Asset.kind.in_(("video", "image", "material")),
        )
        .order_by(Asset.id)
    ).all()

    clips: list[ClipOption] = []
    images: list[ImageOption] = []
    text_points: list[tuple[int, str]] = []
    for asset, version in rows:
        if asset.kind == "video":
            # 第 116 刀：**可播字节**是进选材的前提——旧式时间码文本切片
            # （kind=video 但键后缀 .txt，ADR 0039 存量形态）不是可播素材；
            # 此前它混进清单会让 ffmpeg 把文本当视频流（[N:v] 无流）整单 502。
            # 判据与媒体端点同源（media_mime 非 None 才算可播 mp4）。
            if media_mime("video", version.object_key) is None:
                logger.warning(
                    "成片选材：切片 A-%s 字节不是可播 mp4（旧时间码文本），跳过",
                    asset.id,
                )
                continue
            transcript = resolve_field_value(
                version.extracted_fields or {}, version.confirmed_fields or {}, "transcript"
            )
            if transcript:
                clips.append(
                    ClipOption(
                        asset_id=asset.id,
                        version_no=version.version_no,
                        object_key=version.object_key,
                        transcript=transcript,
                    )
                )
        elif asset.kind == "image":
            from suite_api.services.machine_wash import IMAGE_DESCRIPTION_FIELD

            description = resolve_field_value(
                version.extracted_fields or {},
                version.confirmed_fields or {},
                IMAGE_DESCRIPTION_FIELD,
            )
            images.append(
                ImageOption(
                    asset_id=asset.id,
                    version_no=version.version_no,
                    object_key=version.object_key,
                    description=description or "",
                )
            )
        else:  # material 文案：正文行=文案要点候选（每资产最多取 4 行，防一篇长文吃满配额）
            try:
                body = read_version_text(db, storage, version)
            except Exception:  # noqa: BLE001 - 单资产正文缺失不拖垮整次选材，如实跳过
                logger.warning("成片选材：material 资产 A-%s 正文读取失败，跳过", asset.id)
                continue
            lines = [line.strip() for line in body.splitlines() if line.strip()]
            text_points.extend((asset.id, line) for line in lines[:4])

    selling_points = tuple(
        str(entry.get("value"))
        for entry in dict(product.spec_values).values()
        if isinstance(entry, dict) and entry.get("value")
    )
    return ComposeManifest(
        product_name=product.name,
        selling_points=selling_points,
        clips=tuple(clips),
        images=tuple(images),
        text_points=tuple(text_points[:8]),
    )


# ---------------------------------------------------------------- plan / publish 服务


def plan_compose(
    db: Session, storage: ObjectStorage, product: Product, template: str
) -> ComposeTask:
    """选材 → 时间线 → 预览成片 + 剪映草稿 → 落 planned 任务行。

    步序：清单（读库+读正文，秒级）→ **rollback 收口事务**（P1#2：ffmpeg+
    TTS 最长 ~3 分钟，不占池连接）→ 切片 ffprobe 实测 → 纯函数排版 → TTS
    （无 key=无音轨，note 诚实标注）→ ffmpeg 预览 → 草稿 zip → 暂存字节 →
    落库 commit。任何一步失败抛 ComposeError 子类（路由 422/502），不落半行
    任务（plan 是请求态动作，失败即无痕可重发，0053 候选同性质）。
    """
    manifest = load_compose_manifest(db, storage, product)
    db.rollback()  # P1#2：先收口——下面 ffprobe/TTS/ffmpeg 全程不持池连接

    # 切片实测时长（本地字节 ffprobe，秒级；单条失败退回转写估算不拖垮整次）
    probed_clips: list[ClipOption] = []
    clip_meta: dict[int, tuple[float, int, int]] = {}
    for option in manifest.clips:
        try:
            duration, width, height = probe_video_meta(storage.get_bytes(option.object_key))
        except (ComposeError, FileNotFoundError):
            logger.warning("成片选材：切片 A-%s 时长探测失败，按转写估算", option.asset_id)
            probed_clips.append(option)
            continue
        clip_meta[option.asset_id] = (duration, width, height)
        probed_clips.append(
            ClipOption(
                asset_id=option.asset_id,
                version_no=option.version_no,
                object_key=option.object_key,
                transcript=option.transcript,
                duration_seconds=duration,
            )
        )
    if probed_clips:
        manifest = ComposeManifest(
            product_name=manifest.product_name,
            selling_points=manifest.selling_points,
            clips=tuple(probed_clips),
            images=manifest.images,
            text_points=manifest.text_points,
        )

    plan = build_timeline(manifest, template)

    # 口播：文案要点串联（纯高光集锦无文案时用入选切片的转写串联）
    narration_lines = [item.text for item in plan.timeline if item.type == "text" and item.text]
    if not narration_lines:
        picked_ids = {item.asset_id for item in plan.timeline if item.type == "clip"}
        narration_lines = [o.transcript for o in manifest.clips if o.asset_id in picked_ids]
    narration = "。".join(narration_lines)
    tts_bytes: bytes | None = None
    tts_note: str | None = None
    if narration:
        try:
            tts_bytes = tts_service.synthesize_speech(narration)
        except tts_service.TTSNotConfigured:
            tts_note = "TTS 未配置，预览无声"
        except tts_service.TTSError as exc:
            tts_note = f"口播合成失败，预览无声：{exc}"
            tts_bytes = None

    # 渲染项装配（本地临时目录喂 ffmpeg；draft 项带包内相对路径与实测宽高）
    with tempfile.TemporaryDirectory(prefix="compose-") as tmp:
        workdir = Path(tmp)
        render_items: list[dict[str, Any]] = []
        draft_items: list[dict[str, Any]] = []
        image_meta: dict[int, tuple[int, int]] = {}
        for index, item in enumerate(plan.timeline):
            option = next(
                (o for o in (*manifest.clips, *manifest.images) if o.asset_id == item.asset_id),
                None,
            )
            if option is None:  # text 项：来源 material 资产，无媒体字节（lavfi 卡）
                render_items.append(
                    {"type": "text", "input_index": index, "start": item.start,
                     "dur": item.dur, "text": item.text, "role": item.role}
                )
                draft_items.append(  # 草稿文本轨需要字幕项（无媒体字节）
                    {"type": "text", "start": item.start, "dur": item.dur, "text": item.text,
                     "role": item.role,
                     "draft_name": None, "bytes": None, "width": 0, "height": 0, "duration": 0.0}
                )
                continue
            try:
                media_bytes = storage.get_bytes(option.object_key)
            except FileNotFoundError as exc:
                raise ComposeError(
                    f"{'切片' if item.type == 'clip' else '图片'}资产 A-{item.asset_id}"
                    " 的已发布字节缺失"
                ) from exc
            suffix = Path(option.object_key).suffix or (".mp4" if item.type == "clip" else ".png")
            local = workdir / f"media-{index}{suffix}"
            local.write_bytes(media_bytes)
            if item.type == "clip":
                duration, width, height = clip_meta.get(item.asset_id, (item.dur, 0, 0))
            else:
                if item.asset_id not in image_meta:
                    try:
                        image_meta[item.asset_id] = probe_image_size(media_bytes)
                    except ComposeError:
                        image_meta[item.asset_id] = (0, 0)
                width, height = image_meta[item.asset_id]
                duration = item.dur
            draft_name = f"materials/{item.type}-A{item.asset_id}-v{option.version_no}{suffix}"
            render_items.append(
                {"type": item.type, "input_index": index, "start": item.start,
                 "dur": item.dur, "path": str(local)}
            )
            draft_items.append(
                {"type": item.type, "start": item.start, "dur": item.dur, "text": item.text,
                 "draft_name": draft_name, "bytes": media_bytes,
                 "width": width, "height": height, "duration": duration}
            )

        preview_bytes = render_preview(
            render_items, tts_bytes, total=plan.duration_seconds, workdir=workdir,
            label=manifest.product_name,
        )
        draft_bytes = build_draft_zip(
            draft_items,
            total=plan.duration_seconds,
            # 评审修（事务纪律）：rollback 后 ORM 属性访问会重开事务并贯穿
            # put_bytes 窗口——名字在清单阶段已预取（manifest.product_name）。
            draft_name=f"{manifest.product_name.strip()[:30] or '商品'} · 内容成片",
            tts_bytes=tts_bytes,
        )

    notes = [n for n in (plan.note, tts_note) if n]
    run_id = uuid.uuid4().hex
    preview_key, draft_key = f"compose/{run_id}/preview.mp4", f"compose/{run_id}/draft.zip"
    storage.put_bytes(preview_key, preview_bytes)
    storage.put_bytes(draft_key, draft_bytes)
    task = ComposeTask(
        product_id=product.id,
        status=PLANNED,
        template=template,
        timeline=[item.to_json() for item in plan.timeline],
        duration_seconds=plan.duration_seconds,
        with_tts=tts_bytes is not None,
        note="；".join(notes)[:500] if notes else None,
        preview_object_key=preview_key,
        draft_object_key=draft_key,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def compose_title(product_name: str) -> str:
    """publish 登记的素材资产标题：``{商品} · 内容成片``（服务端定值）。"""
    base = product_name.strip()[:40] or "商品"
    return f"{base} · 内容成片"


def timeline_content(timeline: Sequence[dict[str, Any]]) -> str:
    """时间线 → publish 正文：文案要点串联（无文案要点时退入选切片转写串联）。

    正文是给人洗/给投放引用的文本面——「AI 排的版、人确认的稿」；转写来自
    已发布资产（治理过的文本），纯高光集锦也有正文可登记。
    """
    lines = [
        str(item["text"]).strip()
        for item in timeline
        if item.get("type") == "text" and item.get("text") and not item.get("role")
    ]
    if not lines:
        lines = [f"（切片高光）{item.get('text') or ''}".strip()
                 for item in timeline if item.get("type") == "clip"]
    return "\n".join(lines)


def _claim_for_publish(db: Session, task: ComposeTask) -> None:
    """publish 占位（审计 19，CAS）：``planned → registering`` 的**原子条件更新**。

    对齐 80 刀 ``end_session`` 先例：读 status 后无行锁即双闸+登记的旧形态下，
    并发双 publish 都过读检查 → 双 ``register_asset`` = 双 material。这里以
    ``WHERE status='planned'`` 保证只有首写者占位；后来者 rowcount=0 抛
    ``ComposeConflictError``（路由 409「任务已被确认」）。
    """
    rowcount = db.execute(
        update(ComposeTask)
        .where(ComposeTask.id == task.id, ComposeTask.status == PLANNED)
        .values(status=REGISTERING)
    ).rowcount
    db.commit()
    db.refresh(task)
    if rowcount == 0:
        raise ComposeConflictError(
            f"任务已被确认（当前状态: {task.status}），不能重复登记"
        )


def _release_claim(db: Session, task: ComposeTask) -> None:
    """双闸失败分支的占位回滚：``registering → planned``（条件更新防误覆盖，
    如终态已由异常路径写入则不动）。回滚后任务可重试 publish。"""
    db.execute(
        update(ComposeTask)
        .where(ComposeTask.id == task.id, ComposeTask.status == REGISTERING)
        .values(status=PLANNED)
    )
    db.commit()
    db.refresh(task)


def _cleanup_staging(storage: ObjectStorage, task: ComposeTask) -> None:
    """publish 转正后的暂存清理（审计 19）：删 preview/draft 暂存字节。

    - **final 留档**（ADR 0056：成品留任务档，``/{id}/final`` 端点继续可下）；
      preview/draft 已被登记结果取代（registered 是终态，暂存键不再有读方）；
    - 删除失败**不 fail**：登记已成功（DB 已 commit），字节清理是尽力而为——
      只 log warning，孤儿暂存键由存储侧生命周期兜底，不把成功登记翻成报错。
    """
    for key in (task.preview_object_key, task.draft_object_key):
        try:
            storage.delete(key)
        except Exception:  # noqa: BLE001 - 清理失败不拖垮已成功的登记
            logger.warning("成片暂存清理失败（登记不受影响，待存储侧兜底）: %s", key)


def publish_compose(
    db: Session,
    storage: ObjectStorage,
    task: ComposeTask,
    product: Product,
    final_video_bytes: bytes | None,
) -> Asset:
    """人闸门确认（planned → registered）：文案过双闸（红线③复用）→ 登记
    material 资产；上传的成品 mp4 只是任务留档字节（compose/ 暂存，不资产化）。

    - **CAS 占位**（审计 19）：规则闸后先原子迁移 ``planned → registering``
      （并发双 publish 只有首写者能登记，后来者 409「任务已被确认」）；双闸/
      登记失败回滚 ``registering → planned``，任务停在可重试态；
    - 双闸复用 98 刀同函数：规则四条（material.qc_check）不过=ComposeError；
      LLM 事实性质检（material.run_llm_qc）不过/不可用=ComposeError
      fail-closed——publish 是登记闸，闸跑不完不放行（与素材任务同语义）；
    - 登记：kind=material、source_kind=**upload**（服务端定值——publish 时的
      正文已是人确认的成品文本，通道形态等同人工上传）、标题「{商品} · 内容
      成片」、挂商品；register_asset 内照常机洗推进待人洗（素材中心治理台
      可见，受人洗/发布治理）。
    """
    if task.status != PLANNED:
        raise ComposeError(f"只有待确认的成片任务可以登记，当前状态: {task.status}")
    title = compose_title(product.name)
    content = timeline_content(task.timeline or [])
    errors = material_service.qc_check(title, content, product.name)
    if errors:
        raise ComposeError("规则质检不过线：" + "；".join(errors))
    _claim_for_publish(db, task)
    # 评审修（事务纪律，material 先例）：LLM 双闸（≤20s）与成品字节 put_bytes
    # 都是外部调用——_claim 已收口事务再跑；expire_on_commit=False 使 product
    # 属性驻留，LLM 期间不再重开事务。
    try:
        passed, issues = material_service.run_llm_qc(title, content, product)
    except material_service.MaterialGenError as exc:
        _release_claim(db, task)
        raise ComposeError(str(exc)) from exc
    if not passed:
        _release_claim(db, task)
        raise ComposeError("LLM 事实性质检不过线：" + "；".join(issues)[:300])

    if final_video_bytes:
        run_prefix = task.preview_object_key.rsplit("/", 1)[0]
        task.final_video_object_key = f"{run_prefix}/final.mp4"
        storage.put_bytes(task.final_video_object_key, final_video_bytes)
    asset = register_asset(
        db,
        storage,
        kind="material",
        title=title,
        content_bytes=content.encode("utf-8"),
        filename=None,
        product_id=task.product_id,
        source_kind="upload",
    )
    task.asset_id = asset.id
    task.status = REGISTERED
    db.commit()
    _cleanup_staging(storage, task)
    return asset
