"""图片描述一致性复核（第 111 刀护栏，W7 防复发）。

## 为什么

第 108 刀审计发现 7 份图片资产里 3 份「确认描述与画面不符」——人洗确认时
没有独立第二眼，描述可以直接与画面对不上地进检索面（描述是图片唯一的检索
文本面，ADR 0051）。本模块把 108 审计用的「关键词交集」判据前移成**人洗时
的实时复核**：人 PATCH「图片描述」时，后端读该版对象字节调 VLM 独立描述，
与人的值做交集，把结果作为响应附注回给前端（徽章）。

## 判据与权限边界（治理权在人）

- 交集 **≥2 词=一致**（通过，UI 不干预）；
- **0–1 词=疑似不符**——UI 亮「⚠ 请复核」，但**不阻止** PATCH：人仍是最终
  裁决者，护栏只提供第二眼，不做否决（护栏拦不住「人就是要把描述写成别的」
  这种正当场景，比如描述里带营销语/规格词）。
- VLM 未配置/调用失败/对象缺失 = **fail-open**：不复核、不拦、不写假结论
  （照常人洗兜底；111 刀前的人洗形态就是无复核，本模块不把它变成新闸门）。

## 开关

env ``IMAGE_VERIFY_ON_WASH``（默认 on）：显式关掉可省一次 VLM 调用（演示
时不想等/不想花钱）；**key 空时自动跳过**（同 VLM 草稿的诚实降级口径）。

## 关键词口径

与 108 审计同源（``scripts/realdata/data_health_check.py`` 从本模块导入，
禁止两处各写一套）：中文 bigram + ASCII 词，停用字与「画面/背景/清晰」类
套话剔除（A-505 实测：不剔套话会让两张无关图误判一致）。
"""

import re
from dataclasses import dataclass
from typing import Any

from suite_api.settings import Settings, get_settings

# 一致判定阈值：与 108 审计 DESCRIPTION_MIN_OVERLAP 一字不差（唯一定义在此，
# 审计脚本从本模块导入）。
IMAGE_VERIFY_MIN_OVERLAP = 2

# 关键词提取的停用字（bigram 里含这些字的不算关键词——中文启发式的降噪）
_STOP_CHARS = frozenset(
    "的了是在有和与及也就都还这那我你他她它请问一下一个什么怎么怎样多少哪吗呢吧啊呀哦嗯很挺太真个只把被给对能会要又没不"
)
# 描述句的套话（图/画面结构词，不是「画的是什么」——A-505 实测：「画面」「背景」
# 让测试图与瓶装水描述交集凑到 2 词误判「一致」；剥掉后交集归零=疑似不符）
_BOILERPLATE = frozenset(
    {
        "画面",
        "背景",
        "图片",
        "照片",
        "图像",
        "主体",
        "居中",
        "清晰",
        "整体",
        "拍摄",
        "实拍",
        "一张",
        "这张",
        "下方",
        "上方",
        "中央",
        "中间",
        "可见",
        "呈现",
        "显示",
        "周围",
        "环境",
        "部分",
        "位置",
        "颜色",
        "色彩",
    }
)
_ASCII_TOKEN_RE = re.compile(r"[0-9a-z]+")
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")


def keywords_of(text: str | None) -> set[str]:
    """文本 → 关键词集（中文 bigram + ASCII 词；停用字/套话剔除）。"""
    lowered = (text or "").lower()
    tokens: set[str] = {match.group() for match in _ASCII_TOKEN_RE.finditer(lowered)}
    for run in _CJK_RUN_RE.finditer(lowered):
        chars = run.group()
        for i in range(len(chars) - 1):
            bigram = chars[i : i + 2]
            if bigram[0] in _STOP_CHARS or bigram[1] in _STOP_CHARS:
                continue
            if bigram in _BOILERPLATE:
                continue
            tokens.add(bigram)
    return tokens


def keyword_overlap(left: str | None, right: str | None) -> int:
    """两段文本的关键词交集大小（判定用的计数，暴露给响应与测试）。"""
    return len(keywords_of(left) & keywords_of(right))


# 跳过原因（稳定枚举：前端/测试按值分派文案，不在两处各写字符串）
SKIP_DISABLED = "disabled"  # IMAGE_VERIFY_ON_WASH=off
SKIP_NO_KEY = "no_key"  # VLM_API_KEY 空（不建客户端、不发请求）
SKIP_VLM_FAILED = "vlm_failed"  # 看图失败/超时/坏输出（fail-open）
SKIP_OBJECT_MISSING = "object_missing"  # 版本对象字节缺失（fail-open）


@dataclass(frozen=True)
class WashVerify:
    """一次复核的结果（响应附注与纯函数测试共用同一形状）。

    ``skipped`` 非空 = 没复核（原因见枚举）：此时 passed/overlap 恒 None——
    不写「已通过」的假结论（fail-诚实）。复核完成时 skipped 为 None，
    passed=交集是否 ≥ 阈值。
    """

    passed: bool | None
    overlap: int | None
    vlm_summary: str | None
    skipped: str | None

    def as_payload(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "overlap": self.overlap,
            "vlm_summary": self.vlm_summary,
            "skipped": self.skipped,
        }


def skipped(reason: str) -> WashVerify:
    """构造「未复核」结果（原因取上面枚举；调用方在拿不到字节等边界用）。"""
    return WashVerify(passed=None, overlap=None, vlm_summary=None, skipped=reason)


def enabled(settings: Settings | None = None) -> bool:
    """复核开关（默认 on）：env ``IMAGE_VERIFY_ON_WASH`` 显式关才关。"""
    return (settings or get_settings()).image_verify_on_wash


def verify_description(image_bytes: bytes, description: str) -> WashVerify:
    """人的描述 vs VLM 独立描述 → 复核结果（**纯附注，绝不抛异常、绝不拦 PATCH**）。

    顺序：开关关 -> disabled；VLM 未配置 -> no_key；看图失败 -> vlm_failed；
    成功则算交集（先过 ``redact`` 落进附注——VLM 可能把图中号码带进来，
    出口必掩同 0038 口径）。VLM 调用只在开关开且配置了 key 时发生。
    """
    from suite_api.services import vlm  # 延迟导入：与 machine_wash 同口径
    from suite_api.services.machine_wash import redact

    if not enabled():
        return skipped(SKIP_DISABLED)
    if not vlm.is_configured():
        return skipped(SKIP_NO_KEY)
    try:
        summary = vlm.describe_image(image_bytes)
    except vlm.VLMNotConfigured:  # 竞态兜底（is_configured 与建客户端之间）
        return skipped(SKIP_NO_KEY)
    except vlm.VLMError:
        return skipped(SKIP_VLM_FAILED)
    overlap = keyword_overlap(description, summary)
    return WashVerify(
        passed=overlap >= IMAGE_VERIFY_MIN_OVERLAP,
        overlap=overlap,
        vlm_summary=redact(summary),
        skipped=None,
    )
