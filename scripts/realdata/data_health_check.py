"""数据中心健康审计（第 108 刀）：演示库 515 份资产的只读分类体检 + 报告落档。

## 为什么

演示库经 90–107 刀十几次真跑积累了 515 行资产：既有支撑 12 幕的演示素材，
也有冒烟/验收/探针留下的残留，还有「数据本身不健康」的形态（图片描述与画面
不符、字节重复、空标题、口径冗余……）。第 108 刀先把**问题全貌**盘出来，
第 109 刀（施工单）才动数据——所以本脚本**纯审计**：只读、不写任何行、
不动产品代码（治理动作不自动，0005/0009 同纪律）。

## 查什么（七类，任务书口径）

1. **图片资产描述-画面一致性**：7 份 kind=image 未废弃——取对象字节调真 VLM
   （services/vlm.describe_image，真 key）生成独立描述，与 confirmed_fields
   的「图片描述」做关键词交集（≥2 词=一致；0–1=疑似不符；VLM 失败=无法核验）。
   串行限流（默认每份间隔 1s），失败不中断整跑。
2. **字节重复**：全部未废弃资产当前版本的对象字节 → md5 分组（>5MB 跳过：
   大文件 md5 太慢）；重复组报告，另附「对象缺失」清单。
3. **空标题 / 标题-正文错配**：空标题（title NULL/空白）；OFF 错配残余——
   直接复用 `correct_off_mismatch.judge_mismatch` 纯函数（第 100 刀判据，
   同源不重写）；附加标题乱码检测（GBK 当 Latin-1 读进库的形态）。
4. **验收残留**（5 子类）：mcp_registered 探针（mcp-smoke/evidence probe 开头，
   demo_reset 同判据）、素材任务残留（failed/pending 且 id≤11，附 running
   僵死任务）、成片任务残留（非 registered 且超 1h）、验收录像（label 含
   探针/probe 或 id∈{5,6}，附其挂的候选）、ABCD/WANDS 存量对话（审阅与数码店
   不匹配的：英文标题/非数码类目词）。
5. **缺口池健康**（37 open 分三类）：直调 retrieve（同问法）——有命中=auto-close
   漏网候选；问句以「我」开头且含「问题/上一个」=元问题误落；「有没有 X 类」
   问句且 X 不在商品表（含 82/86 刀 OOV 判据复核）=OOV 误落。
6. **口径冗余**：退货/保修/发票/配送四主题按标题关键词分组，同主题 >1 份
   已发布文档=冗余；附重复标题资产（如 5 份「保温杯的净含量是多少？」）。
7. **会话/工单残留**：空会话（无消息/工单/评分/缺口/回流锚，demo_reset 同判据）、
   pending 超 24h 的陈旧工单。

## 输出

终端报告 + `--report` 直出 Markdown（默认
`docs/research/data-health-report.md`，109 刀的施工单）。每类发现带资产 id、
判定与建议动作（retire/fix/keep 三分列汇总）。

## 用法（仓库根）

```bash
# 全量审计（含真 VLM 7 份，约 1 分钟）
uv run python scripts/realdata/data_health_check.py \
    --db postgresql://suite:suite@localhost:5433/suite --report

# 不跑 VLM（离线/省调用）：图片一致性标「跳过（--skip-vlm）」
uv run python scripts/realdata/data_health_check.py --db ... --skip-vlm

# 报告另存
uv run python scripts/realdata/data_health_check.py --db ... --report out/health.md
```

本脚本**没有 --apply**：它只报告（同 demo_prepare 的检查器定位）；清理动作
109 刀照施工单来。
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# 复用第 100 刀的 OFF 错配判据（同源不重写）：脚本目录进 sys.path 后可直接
# import（uv run 走脚本路径时天然在；被测试当模块导入时由测试插 sys.path）。
_REALDATA_DIR = Path(__file__).resolve().parent
if str(_REALDATA_DIR) not in sys.path:
    sys.path.insert(0, str(_REALDATA_DIR))

import correct_off_mismatch as off_mismatch  # noqa: E402

# 关键词交集口径（第 111 刀起唯一定义在 app 服务层：同一函数既守本审计，也守
# 人洗时的实时复核护栏——services/image_verify.py；禁止两处各写一套）
from suite_api.services.image_verify import (  # noqa: E402
    IMAGE_VERIFY_MIN_OVERLAP,
    keyword_overlap,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT_PATH = REPO_ROOT / "docs" / "research" / "data-health-report.md"

# ---------- 判据常量（每一条都写明出处；不要在别处复制） ----------

IMAGE_DESCRIPTION_FIELD = "图片描述"
# 探针资产判据（与 demo_reset.PROBE_TITLE_SQL / workQueue.isWorkProbe 同源）
PROBE_TITLE_PATTERN = re.compile(r"^(mcp-smoke|evidence probe)", re.IGNORECASE)
PROBE_TITLE_SQL = r"^(mcp-smoke|evidence probe)"
# 录像/图片标题的探针特征（第 4 类：验收录像 label 口径，任务书原文）
PROBE_LABEL_MARKERS = ("探针", "probe")

MAX_MD5_BYTES = 5 * 1024 * 1024  # 只对 ≤5MB 的文件做 md5（大视频跳过）
VLM_INTERVAL_DEFAULT = 1.0  # 真 VLM 串行限流间隔（秒）
# 关键词交集阈值（≥2 词=一致；0–1=疑似不符）——唯一定义在 services/image_verify
DESCRIPTION_MIN_OVERLAP = IMAGE_VERIFY_MIN_OVERLAP
# 施工单动作优先级（同一对象被多类检查发现时取最强者，见 AuditReport.all_actions）
ACTION_PRIORITY = {"retire": 0, "fix": 1, "keep": 2}

STALE_TICKET_HOURS = 24  # pending 工单陈旧阈值
STALE_COMPOSE_HOURS = 1  # 成片任务残留阈值（created_at < now-1h）
MATERIAL_RESIDUE_STATUSES = ("failed", "pending")  # 素材任务残留状态
MATERIAL_MAX_ID = 11  # 只圈 id≤11（排除最新真跑 M-11 之后的新行——任务书口径）

# 四主题冗余关键词（第 6 类；同主题 >1 份已发布文档=冗余）
POLICY_TOPICS: dict[str, tuple[str, ...]] = {
    "退货/退换": ("退货", "退换"),
    "保修/质保": ("保修", "质保"),
    "发票": ("发票",),
    "配送/物流/发货": ("配送", "物流", "发货", "快递"),
}
# 评论导入也是 kind=document（33 刀批量通道）——标题里出现「退货/发货」是评论
# 内容不是口径文档，主题分组必须先剔掉这个来源_kind
REVIEW_SOURCE_KIND = "review_import"

# 经营范围类问句的泛词（「有没有货」问的是库存不是商品——不判 OOV）
GENERIC_SCOPE_TERMS = frozenset(
    {"货", "现货", "库存", "赠品", "优惠", "折扣", "活动", "发票", "保修", "问题", "颜色", "款"}
)
# 泛词前的定语（「还有别的颜色吗」的实体是「别的颜色」——剥掉再比泛词）
SCOPE_ENTITY_MODIFIERS = ("别的", "其他", "其它", "这个", "那个", "新的", "一款", "另一个")
# 经营范围类问句形态（有 X 吗 / 有没有 X / 你们卖 X 吗 / 卖不卖 X）
SCOPE_PATTERNS = (
    re.compile(r"有没有(?P<entity>[^，。！？?！\s]{1,12})"),
    re.compile(r"你们(?:卖|有)(?P<entity>[^，。！？?]{1,12})吗"),
    re.compile(r"有(?P<entity>[^，。！？?]{1,12})吗"),
    re.compile(r"卖不卖(?P<entity>[^，。！？?]{1,12})"),
)
# 元问题误落（第 5 类）：以「我」开头且含「问题/上一个」
META_QUESTION_RE = re.compile(r"^我.*(?:问题|上一个)")
# 非数码类目词（ABCD/WANDS 存量审阅用；任务书「标题含英文/非数码类目词」）
OFF_SCOPE_TERMS = (
    "家具", "服装", "服饰", "图书", "食品", "鞋", "化妆品", "家电", "日用品", "玩具",
    "shirt", "order", "shipping", "cart", "subscription", "account", "refund", "kitchen",
    "sofa", "table", "matt", "bowl", "dresser", "desk", "rug",
)
# 已发布演示素材（字节同源是 94a 实录的既成事实，不因重复/被查而建议废弃）
# 第 110 刀重灌：A-501/502 换真图（501 v1→v2→v3、502 v2）；A-503（空标题
# 重复份）退役——条目随之移除（退役资产本就不进本检查候选集）。
DEMO_ASSET_KEEP: dict[int, str] = {
    501: "94a 图片治理幕素材（显示器官图；110 刀重灌真图）",
}

# 标题乱码检测：Latin-1 符号区（GBK 字节被当 Latin-1 存进来的典型形态）里
# 出现 ≥3 个符号才判——「Nestlé」这类单重音品牌名不误伤（实测仅 1 个）
_MOJIBAKE_RE = re.compile(r"[\u00a1-\u00ff]")
_MOJIBAKE_MIN = 3
# 汉字连续串（问句/库内名的中文片段切分用，offers_known_product 的保守判定）
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")


# ---------- 纯函数（离线可测；第 108 刀单测覆盖点） ----------


def md5_hex(data: bytes) -> str:
    """字节 → md5 十六进制（分组键）。"""
    return hashlib.md5(data).hexdigest()


def group_duplicates(entries: Iterable[tuple[str, str]]) -> dict[str, list[str]]:
    """(md5, 标签) 序列 → {md5: [标签,…]}，只留 ≥2 个成员的组（判定口径纯函数）。

    标签稳定排序（去重分组与文件系统遍历顺序无关），组按首个标签排序——报告
    与测试可复现。
    """
    groups: dict[str, list[str]] = defaultdict(list)
    for digest, label in entries:
        groups[digest].append(label)
    dup = {digest: sorted(labels) for digest, labels in groups.items() if len(labels) > 1}
    return dict(sorted(dup.items(), key=lambda item: item[1][0]))


# 图片一致性判定值（表形状的稳定枚举）
VERDICT_CONSISTENT = "一致"
VERDICT_SUSPECT = "疑似不符"
VERDICT_NO_CONFIRMED = "无确认描述"
VERDICT_UNVERIFIED = "无法核验"
VERDICT_SKIPPED = "跳过（--skip-vlm）"


def description_verdict(
    confirmed: str | None,
    vlm_text: str | None,
    *,
    vlm_failed: bool = False,
    skipped: bool = False,
) -> str:
    """确认描述 vs VLM 独立描述 → 判定（纯函数，阈值见常量）。

    优先判「没得比」的两种（跳过/VLM 失败/无确认值），再判交接口径：
    ≥DESCRIPTION_MIN_OVERLAP 词=一致，否则疑似不符。
    """
    if skipped:
        return VERDICT_SKIPPED
    if vlm_failed or not (vlm_text or "").strip():
        return VERDICT_UNVERIFIED
    if not (confirmed or "").strip():
        return VERDICT_NO_CONFIRMED
    return (
        VERDICT_CONSISTENT
        if keyword_overlap(confirmed, vlm_text) >= DESCRIPTION_MIN_OVERLAP
        else VERDICT_SUSPECT
    )


def is_probe_asset(title: str | None, source_kind: str | None) -> bool:
    """探针资产判据（与 demo_reset / workQueue.isWorkProbe 同源）。"""
    return source_kind == "mcp_registered" and bool(
        PROBE_TITLE_PATTERN.search((title or "").strip())
    )


def is_probe_label(label: str | None) -> bool:
    """验收录像/探针图判据：label 含「探针」或「probe」（大小写不敏感）。"""
    lowered = (label or "").lower()
    return any(marker.lower() in lowered for marker in PROBE_LABEL_MARKERS)


def is_probe_like(asset: dict[str, Any]) -> bool:
    """资产是否探针性质：mcp 探针（判据同 demo_reset）或标题含探针特征。"""
    return is_probe_asset(asset.get("title"), asset.get("source_kind")) or is_probe_label(
        asset.get("title")
    )


def looks_mojibake(text: str | None) -> bool:
    """标题疑似乱码：Latin-1 符号区字符 ≥3 个（GBK 字节被当 Latin-1 存库）。"""
    return len(_MOJIBAKE_RE.findall(text or "")) >= _MOJIBAKE_MIN


def is_meta_question(question: str | None) -> bool:
    """元问题误落（第 5 类）：以「我」开头且含「问题/上一个」。"""
    return bool(META_QUESTION_RE.search((question or "").strip()))


def offers_known_product(question: str, universe: Iterable[str]) -> str | None:
    """问句是否点到库内在售商品/类目（双向包含；86 刀 oov_product_match 同向）。

    命中返回库内名，未命中 None。只做「问句点名」的保守判定：库内名（≥2 字）
    出现在问句里（含大小写不敏感的拉丁名——「Xperia Ear Duo」也算在售），或
    问句里的 ≥2 字中文片段是某个库内名的子串（顾客用简称「保温杯」）。
    """
    text = question or ""
    lowered = text.lower()
    names = [name for name in universe if name and len(name) >= 2]
    for name in sorted(names, key=len, reverse=True):
        if name in text or name.lower() in lowered:
            return name
    for run in _CJK_RUN_RE.finditer(text):
        chars = run.group()
        for size in range(len(chars), 1, -1):
            for start in range(0, len(chars) - size + 1):
                token = chars[start : start + size]
                for name in names:
                    if token in name:
                        return name
    return None


def scope_question_kind(question: str, universe: Iterable[str]) -> str:
    """经营范围类问句分类（纯函数）：not_scope/generic/in_scope/out_of_scope。

    - not_scope：不是「有没有 X」形态；
    - generic：X 是泛词（货/现货/优惠…）——问的是库存/活动不是商品，跳过；
    - in_scope：问句点到库内在售商品/类目（正常问句）；
    - out_of_scope：其它——点是经营范围问题（86 刀语义：只建工单，不该落缺口）。
    """
    entity = ""
    for pattern in SCOPE_PATTERNS:
        match = pattern.search(question or "")
        if match:
            entity = match.group("entity")
            break
    if not entity:
        return "not_scope"
    core = entity
    for modifier in SCOPE_ENTITY_MODIFIERS:
        if core.startswith(modifier):
            core = core[len(modifier) :]
            break
    if core in GENERIC_SCOPE_TERMS or any(term in core for term in ("多久", "几天", "几次")):
        return "generic"
    if offers_known_product(question, universe):
        return "in_scope"
    return "out_of_scope"


def redundancy_groups(
    assets: Sequence[dict[str, Any]], topics: dict[str, tuple[str, ...]] = POLICY_TOPICS
) -> dict[str, list[dict[str, Any]]]:
    """已发布文档按主题关键词分组（纯函数）：同主题 >1 份=冗余组。

    `assets` 行形状：{id, title, status, kind, source_kind}；只吃
    status=published、kind=document 且非 review_import 的行（评论也是
    document，标题碰巧含「退货/发货」不算口径文档）。
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for topic, keywords in topics.items():
        members = [
            asset
            for asset in assets
            if asset.get("status") == "published"
            and asset.get("kind") == "document"
            and asset.get("source_kind") != REVIEW_SOURCE_KIND
            and any(keyword in (asset.get("title") or "") for keyword in keywords)
        ]
        if len(members) > 1:
            groups[topic] = sorted(members, key=lambda asset: asset["id"])
    return groups


def duplicate_title_groups(assets: Sequence[dict[str, Any]]) -> list[tuple[str, list[int]]]:
    """重复标题资产（纯函数）：标题去除首尾空白后同文的组（≥2 个 id）。"""
    by_title: dict[str, list[int]] = defaultdict(list)
    for asset in assets:
        title = (asset.get("title") or "").strip()
        if title:
            by_title[title].append(asset["id"])
    groups = [(title, sorted(ids)) for title, ids in by_title.items() if len(ids) > 1]
    return sorted(groups, key=lambda item: (-len(item[1]), item[0]))


def age_hours(created_at: datetime | None, now: datetime) -> float | None:
    """行创建时刻 → 年龄小时数（None 原样返回；naive 按 UTC 处理）。"""
    if created_at is None:
        return None
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    return (now - created_at).total_seconds() / 3600.0


def is_stale(created_at: datetime | None, now: datetime, hours: float) -> bool:
    """创建时刻距 now 是否超过 hours 小时（None 不判陈旧）。"""
    age = age_hours(created_at, now)
    return age is not None and age >= hours


def stale_bucket(age: float) -> str:
    """陈旧工单分桶（报告聚合用）：≥5 天 / 1–5 天 / 24h–1 天。"""
    if age >= 5 * 24:
        return "≥5 天"
    if age >= 24:
        return "1–5 天"
    return "24h 内"


def compact_id_ranges(ids: Sequence[int], prefix: str, width: int = 4) -> str:
    """连续 id 压成区间串（报告聚合用）：[2,3,4,7] -> H-0002–H-0004、H-0007。

    让几十张工单的清单在表格一格里放得下（Markdown 单元格会截断长文本）。
    """
    ordered = sorted({int(value) for value in ids})
    if not ordered:
        return ""
    def fmt(value: int) -> str:
        return f"{prefix}-{value:0{width}d}"
    parts: list[str] = []
    start = previous = ordered[0]
    for value in ordered[1:]:
        if value == previous + 1:
            previous = value
            continue
        parts.append(fmt(start) if start == previous else f"{fmt(start)}–{fmt(previous)}")
        start = previous = value
    parts.append(fmt(start) if start == previous else f"{fmt(start)}–{fmt(previous)}")
    return "、".join(parts)


# ---------- 报告数据结构 ----------


@dataclass
class Action:
    """一条建议动作（109 施工单的一行）：对象 / retire|fix|keep / 理由。"""

    ref: str
    action: str
    reason: str


@dataclass
class Section:
    """一类检查的报告块：headline 计数 + 表格 + 动作清单 + 附注。"""

    key: str
    title: str
    headline: str
    headers: list[str]
    rows: list[list[str]] = field(default_factory=list)
    actions: list[Action] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class AuditReport:
    """整份审计报告：meta 快照 + 七个检查块。"""

    meta: dict[str, Any]
    sections: list[Section]

    def all_actions(self) -> list[Action]:
        """施工单动作清单（按对象聚合）：动作取最强者 retire>fix>keep。

        同一资产会被多类检查交叉发现（如探针图在第 1、2、4 类都出现）——
        施工单按对象去重；同动作的多个理由合并，弱动作被强动作吸收（避免
        同一行既 keep 又 retire 的自相矛盾）。
        """
        merged: dict[str, Action] = {}
        for action in (item for section in self.sections for item in section.actions):
            current = merged.get(action.ref)
            if current is None:
                merged[action.ref] = Action(action.ref, action.action, action.reason)
                continue
            if ACTION_PRIORITY[action.action] < ACTION_PRIORITY[current.action]:
                merged[action.ref] = Action(action.ref, action.action, action.reason)
            elif (
                action.action == current.action
                and action.reason not in current.reason
                and current.reason not in action.reason
            ):
                merged[action.ref] = Action(
                    action.ref, current.action, f"{current.reason}；{action.reason}"
                )
        return list(merged.values())

    def actions_by_kind(self, kind: str) -> list[Action]:
        return [action for action in self.all_actions() if action.action == kind]


# ---------- 库读取（只读；text() 裸 SQL 复用 demo_prepare 形态） ----------


def _text(sql: str) -> Any:
    from sqlalchemy import text

    return text(sql)


def _rows(session: Any, sql: str, params: dict[str, Any] | None = None) -> list[tuple]:
    return list(session.execute(_text(sql), params or {}))


def _asset_ref(asset_id: int) -> str:
    return f"A-{asset_id}"


def _cell(text: str | None, limit: int = 120) -> str:
    """表格单元格：压平换行、截断（Markdown 表格不许裸换行）。"""
    flat = re.sub(r"\s+", " ", (text or "").strip())
    return flat[:limit] + ("…" if len(flat) > limit else "")


def _md_row(values: Sequence[str]) -> str:
    return "| " + " | ".join(value.replace("|", "\\|") for value in values) + " |"


def _load_asset_versions(session: Any, *, kind: str | None = None) -> list[dict[str, Any]]:
    """未废弃资产的版本行（kind 可选过滤）；当前版本=已发布指针 >最大 version_no。"""
    sql = """
        SELECT a.id, a.kind, a.status, a.source_kind, a.title,
               a.current_published_version_id,
               v.id, v.version_no, v.object_key, v.confirmed_fields, v.extracted_fields
        FROM assets a JOIN asset_versions v ON v.asset_id = a.id
        WHERE a.discarded_at IS NULL {kind_clause}
        ORDER BY a.id, v.version_no
    """
    kind_clause = "AND a.kind = :kind" if kind else ""
    rows = _rows(session, sql.format(kind_clause=kind_clause), {"kind": kind} if kind else None)
    by_asset: dict[int, dict[str, Any]] = {}
    for row in rows:
        (
            asset_id, asset_kind, status, source_kind, title, pointer,
            version_id, version_no, object_key, confirmed, extracted,
        ) = row
        entry = by_asset.setdefault(
            asset_id,
            {
                "id": asset_id,
                "kind": asset_kind,
                "status": status,
                "source_kind": source_kind,
                "title": title,
                "object_key": object_key,
                "version_no": version_no,
                "confirmed_fields": confirmed or {},
                "extracted_fields": extracted or {},
            },
        )
        if pointer is not None and version_id == pointer:
            entry.update(
                object_key=object_key,
                version_no=version_no,
                confirmed_fields=confirmed or {},
                extracted_fields=extracted or {},
            )
    return list(by_asset.values())


def _field_value(fields: dict[str, Any] | None, name: str) -> str:
    """JSONB 字段 {name: {value, source}} → 值（无值/弃权返回空串）。"""
    entry = (fields or {}).get(name)
    if isinstance(entry, dict):
        value = entry.get("value")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


# 标题乱码检测在 Python 侧做（Postgres 的 `[¡-ÿ]` 范围在 glibc 排序里会命中
# 大量 CJK——实测 491 份里误报 413；纯函数 looks_mojibake 口径见测试）
# Wikidata 品牌未解析形态（正文「品牌：Q5019402」——QID 没换成标签）
QID_BRAND_RE = re.compile(r"品牌：Q\d+")


# ---------- 检查 1：图片描述-画面一致性 ----------


def check_image_consistency(
    session: Any,
    storage: Any,
    *,
    vlm_call: Callable[[bytes], str] | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    interval: float = VLM_INTERVAL_DEFAULT,
    skip_vlm: bool = False,
    progress: Callable[[str], None] = lambda _line: None,
) -> Section:
    """7 份 image 资产：真 VLM 独立描述 vs 确认「图片描述」（关键词交集判定）。"""
    if vlm_call is None:  # 延迟导入：模块导入不拖 openai/设置
        from suite_api.services.vlm import describe_image

        vlm_call = describe_image
    images = _load_asset_versions(session, kind="image")
    section = Section(
        key="images",
        title="图片描述-画面一致性",
        headline="",
        headers=["资产", "标题", "确认描述", "VLM 独立描述", "判定", "建议"],
    )
    called = 0
    verdicts: list[str] = []
    for asset in images:
        ref = _asset_ref(asset["id"])
        title = _cell(asset["title"], 40) or "（空标题）"
        confirmed = _field_value(asset["confirmed_fields"], IMAGE_DESCRIPTION_FIELD)
        machine = _field_value(asset["extracted_fields"], IMAGE_DESCRIPTION_FIELD)
        vlm_text = ""
        failed_reason = ""
        if skip_vlm:
            verdict = VERDICT_SKIPPED
        else:
            try:
                data = storage.get_bytes(asset["object_key"])
            except Exception as exc:  # noqa: BLE001 - 读不到字节=无法核验，不中断
                failed_reason = f"对象读取失败（{type(exc).__name__}）"
                verdict = VERDICT_UNVERIFIED
            else:
                if called:
                    sleeper(interval)  # 串行限流：每份之间间隔（首份不睡）
                called += 1
                progress(f"  [{called}/{len(images)}] {ref} VLM 看图…")
                try:
                    vlm_text = (vlm_call(data) or "").strip()
                    verdict = description_verdict(confirmed, vlm_text)
                except Exception as exc:  # noqa: BLE001 - 失败不中断整跑
                    failed_reason = f"VLM 失败（{type(exc).__name__}）"
                    verdict = VERDICT_UNVERIFIED
        overlap = (
            keyword_overlap(confirmed, vlm_text)
            if confirmed and vlm_text
            else None
        )
        detail_bits = [f"交集 {overlap} 词"] if overlap is not None else []
        if machine:
            detail_bits.append(f"机洗描述：{_cell(machine, 40)}")
        if failed_reason:
            detail_bits.append(failed_reason)
        if is_probe_label(asset["title"]):
            detail_bits.append("标题含探针特征")
        judgement = verdict + (f"（{'；'.join(detail_bits)}）" if detail_bits else "")
        verdicts.append(verdict)
        if verdict == VERDICT_CONSISTENT:
            action = "keep"
            reason = "确认描述与画面互证"
        elif verdict == VERDICT_SUSPECT:
            action = "fix"
            reason = "确认描述与画面不符：需换图或改描述（人核后二选一）"
        elif verdict == VERDICT_NO_CONFIRMED:
            if asset["status"] == "published":
                action = "fix"
                reason = "已发布版本无确认「图片描述」——开修订补描述再发布"
            else:
                action = "keep"
                reason = "待人洗（补确认描述）"
        elif verdict == VERDICT_SKIPPED:
            action = "keep"
            reason = "本轮未跑 VLM（--skip-vlm），下次补核"
        else:
            action = "keep"
            reason = "本轮无法核验（失败原因见判定列），保持现状待下次核验"
        if is_probe_like(asset) and asset["status"] != "published":
            action, reason = "retire", "探针图（验收残留）——清出素材库"
        if asset["status"] == "published" and asset["id"] in DEMO_ASSET_KEEP:
            reason += f"；演示素材：{DEMO_ASSET_KEEP[asset['id']]}"
        section.rows.append(
            [
                ref,
                title,
                _cell(confirmed, 60) or "（无）",
                _cell(vlm_text, 60) or "（无）",
                judgement,
                f"{action}：{reason}",
            ]
        )
        section.actions.append(Action(ref, action, reason))
    counts: dict[str, int] = {}
    for verdict in verdicts:
        counts[verdict] = counts.get(verdict, 0) + 1
    section.headline = f"{len(section.rows)} 份：" + "、".join(
        f"{key} {value}" for key, value in counts.items()
    )
    section.notes.append(
        "关键词交集（≥2 词=一致）是启发式：判定列同时给出双方原文与交集词数，"
        "人核以原文为准；「画面/背景/清晰」类套话已从关键词剔除（A-505 误判实证）。"
    )
    section.notes.append(
        "A-504 的 VLM 失败源是 1×1 PNG 占位图（厂商拒绝过小图，BadRequest）——"
        "占位图形态本身记在第 3 类（建议清出）。"
    )
    return section


# ---------- 检查 2：字节重复 ----------


def check_byte_duplicates(session: Any, storage: Any) -> Section:
    """未废弃资产当前版本字节 md5 分组：重复组 + 对象缺失（>5MB 跳过）。"""
    assets = _load_asset_versions(session)
    entries: list[tuple[str, str]] = []
    contents: dict[str, bytes] = {}
    missing: list[str] = []
    skipped: list[str] = []
    for asset in assets:
        ref = _asset_ref(asset["id"])
        try:
            size = storage.size(asset["object_key"])
        except Exception as exc:  # noqa: BLE001 - 缺对象=健康问题本身
            missing.append(f"{ref}（{type(exc).__name__}）")
            continue
        if size > MAX_MD5_BYTES:
            skipped.append(f"{ref}（{size // (1024 * 1024)}MB）")
            continue
        try:
            data = storage.get_bytes(asset["object_key"])
        except Exception as exc:  # noqa: BLE001
            missing.append(f"{ref}（{type(exc).__name__}）")
            continue
        entries.append((md5_hex(data), ref))
        contents[ref] = data
    groups = group_duplicates(entries)
    by_ref = {_asset_ref(asset["id"]): asset for asset in assets}
    section = Section(
        key="duplicates",
        title="字节重复",
        headline=f"{len(groups)} 组重复；对象缺失 {len(missing)} 份；跳过大文件 {len(skipped)} 份",
        headers=["重复组", "成员", "md5", "判定与建议"],
    )
    for index, (digest, members) in enumerate(groups.items(), 1):
        ordered = sorted(
            members,
            key=lambda ref: (
                0 if by_ref[ref]["status"] == "published" else 1,
                int(ref.split("-")[1]),
            ),
        )
        primary = ordered[0]
        # 正文缺陷顺手识别（重复组首份字节）：Wikidata QID 当品牌名
        head_text = contents.get(primary, b"").decode("utf-8", errors="replace")
        qid_brand = bool(QID_BRAND_RE.search(head_text))
        member_actions: list[str] = []
        for ref in ordered:
            asset = by_ref[ref]
            if qid_brand:
                action, reason = "fix", "正文品牌是 Wikidata QID（未解析成标签）——重跑标签解析或人工订正"
            elif is_probe_like(asset):
                action, reason = "retire", "探针资产/探针图（验收残留）——清出素材库"
            elif ref == primary:
                action, reason = "keep", "重复组主份（引用/素材保留）"
            elif asset["status"] == "published":
                action, reason = "fix", "已发布重复副本：人核后合并引用或换图/废弃"
            else:
                action, reason = "retire", "未发布重复副本"
            if not qid_brand and asset["id"] in DEMO_ASSET_KEEP:
                action = "keep"
                reason = f"演示素材：{DEMO_ASSET_KEEP[asset['id']]}"
            section.actions.append(Action(ref, action, reason))
            member_actions.append(f"{ref} {action}")
        section.rows.append(
            [
                f"D-{index}",
                "、".join(members),
                f"md5 {digest[:12]}…" + ("（品牌为 QID）" if qid_brand else ""),
                "；".join(member_actions) + f"（主份 {primary}）",
            ]
        )
    if missing:
        section.notes.append("对象缺失（storage.get_bytes/size 失败，需查恢复）：" + "、".join(missing))
    if skipped:
        section.notes.append(f"跳过大文件（>{MAX_MD5_BYTES // (1024 * 1024)}MB）：" + "、".join(skipped))
    if groups:
        section.notes.append(
            "D-3 的 A-501/502/503 是同一份文件（「显示器商品图」实为咖啡胶囊照）——"
            "与第 1 类的「疑似不符」互证；D-2 的 A-483/484 正文同为「品牌：Q5019402」"
            "（TWS Earbuds 与 Fairbuds 共用一个 QID），是标签解析缺口。"
        )
    else:
        section.notes.append("无重复组。")
    return section


# ---------- 检查 3：空标题 / 标题-正文错配 ----------


def check_titles(session: Any) -> Section:
    """空标题 + OFF 错配残余（100 刀判据复用）+ 标题乱码。"""
    empty = _rows(
        session,
        """
        SELECT id, kind, status, source_kind
        FROM assets
        WHERE discarded_at IS NULL AND (title IS NULL OR btrim(title) = '')
        ORDER BY id
        """,
    )
    malformed = [
        row
        for row in _rows(
            session,
            """
            SELECT id, kind, status, source_kind, title
            FROM assets
            WHERE discarded_at IS NULL AND title IS NOT NULL
            ORDER BY id
            """,
        )
        if looks_mojibake(row[4])
    ]
    off_rows = off_mismatch.diagnose(off_mismatch.load_off_assets(session))
    mismatches = [row for row in off_rows if row["verdict"] == off_mismatch.VERDICT_MISMATCH]
    no_brand = [row for row in off_rows if row["verdict"] == off_mismatch.VERDICT_NO_BRAND]
    section = Section(
        key="titles",
        title="空标题 / 标题-正文错配",
        headline=(
            f"空标题 {len(empty)} 条；OFF 错配残余 {len(mismatches)} 份"
            f"（no_brand {len(no_brand)}）；标题乱码 {len(malformed)} 条"
        ),
        headers=["资产", "标题", "判定", "建议"],
    )
    for asset_id, kind, status, source_kind in empty:
        ref = _asset_ref(asset_id)
        action, reason = "fix", "空标题——补标题（已发布资产优先）"
        if asset_id in DEMO_ASSET_KEEP:
            reason += f"；演示素材：{DEMO_ASSET_KEEP[asset_id]}"
        section.rows.append([ref, "（空标题）", f"空标题 · {kind}/{status}/{source_kind}", f"{action}：{reason}"])
        section.actions.append(Action(ref, action, reason))
    for row in mismatches:
        ref = _asset_ref(row["asset_id"])
        action, reason = "fix", f"OFF 标题/正文品牌错配——改标题为 {row['new_title']}"
        section.rows.append([ref, _cell(row["title"], 40), "OFF 错配（100 刀判据）", f"{action}：{reason}"])
        section.actions.append(Action(ref, action, reason))
    for row in no_brand:
        ref = _asset_ref(row["asset_id"])
        section.rows.append(
            [
                ref,
                _cell(row["title"], 40),
                "OFF 无品牌行（不可判）",
                "keep：正文无「品牌：」锚，弃权不猜（0009 口径）",
            ]
        )
        section.actions.append(Action(ref, "keep", "OFF 正文无品牌行，不可判"))
    for asset_id, kind, status, source_kind, title in malformed:
        if not looks_mojibake(title):
            continue  # 单个重音字母（Nestlé）不是乱码
        ref = _asset_ref(asset_id)
        action, reason = "retire", "标题乱码 + 占位字节（1x1 PNG）：清出素材库；若要保留先改回原题"
        section.rows.append(
            [ref, _cell(title, 40), f"标题乱码 · {kind}/{status}/{source_kind}", f"{action}：{reason}"]
        )
        section.actions.append(Action(ref, action, reason))
    if not section.rows:
        section.notes.append("无空标题、无错配残余、无乱码标题。")
    return section


# ---------- 检查 4：验收残留 ----------


def _product_universe(session: Any) -> list[str]:
    """在售商品名 + 类目 + 类目别名（经营范围判定的单一参照集）。"""
    universe = [name for (name,) in _rows(session, "SELECT name FROM products") if name]
    universe += [
        name
        for (name,) in _rows(session, "SELECT DISTINCT category FROM products WHERE category IS NOT NULL")
        if name
    ]
    try:
        from suite_api.services.catalog_tools import CATEGORY_ALIASES

        universe += list(CATEGORY_ALIASES)
    except Exception:  # noqa: BLE001 - 别名表缺位只影响分类精度，不值得中断审计
        pass
    return sorted({name for name in universe if name}, key=len, reverse=True)


def check_acceptance_residue(session: Any, now: datetime) -> Section:
    """五子类验收残留：探针资产 / 素材任务 / 成片任务 / 验收录像 / ABCD·WANDS 存量。"""
    section = Section(
        key="residue",
        title="验收残留",
        headline="",
        headers=["对象", "子类", "现状", "建议"],
    )

    def add(ref: str, subtype: str, detail: str, action: str, reason: str) -> None:
        section.rows.append([ref, subtype, detail, f"{action}：{reason}"])
        section.actions.append(Action(ref, action, reason))

    # 4a mcp_registered 探针资产（demo_reset 同判据）
    probes = _rows(
        session,
        """
        SELECT id, title, status
        FROM assets
        WHERE discarded_at IS NULL AND source_kind = 'mcp_registered'
          AND title ~* :pattern
        ORDER BY id
        """,
        {"pattern": PROBE_TITLE_SQL},
    )
    for asset_id, title, status in probes:
        add(
            _asset_ref(asset_id),
            "mcp 探针资产",
            f"{status}：{_cell(title, 50)}",
            "retire",
            "探针资产（demo_reset 特征判据同源，可 --apply 批量置废弃）",
        )
    # 4a′ VLM 真跑探针图（同属验收残留；同时出现在第 1 类）
    vlm_probes = _rows(
        session,
        """
        SELECT id, title FROM assets
        WHERE discarded_at IS NULL AND kind = 'image' AND (title LIKE '%探针%' OR title LIKE '%probe%')
        ORDER BY id
        """,
    )
    for asset_id, title in vlm_probes:
        add(_asset_ref(asset_id), "VLM 探针图", _cell(title, 50), "retire", "验收探针图（与第 1 类交叉出现）")

    # 4b 素材任务残留（failed/pending 且 id≤11）+ running 僵死（补充证据）
    material = _rows(
        session,
        """
        SELECT id, status, title, last_error FROM material_tasks
        WHERE status = ANY(:statuses) AND id <= :max_id
        ORDER BY id
        """,
        {"statuses": list(MATERIAL_RESIDUE_STATUSES), "max_id": MATERIAL_MAX_ID},
    )
    for task_id, status, title, last_error in material:
        add(
            f"M-{task_id}",
            "素材任务残留",
            f"{status}：{_cell(title, 40)} {_cell(last_error, 40)}",
            "retire",
            "失败/挂起任务未推进——终止或重跑（任务行非资产，清行即成）",
        )
    running = _rows(
        session,
        """
        SELECT id, created_at, updated_at FROM material_tasks
        WHERE status = 'running' AND updated_at < :cutoff
        ORDER BY id
        """,
        {"cutoff": now - timedelta(hours=STALE_COMPOSE_HOURS)},
    )
    for task_id, created_at, updated_at in running:
        age = age_hours(updated_at or created_at, now) or 0.0
        add(
            f"M-{task_id}",
            "素材任务僵死",
            f"running 已 {age:.1f}h（updated_at 停滞）",
            "retire",
            "进程中断遗留的 running——终止或重跑（补充发现：任务书口径外）",
        )

    # 4c 成片任务残留（非 registered 且超 1h）
    compose = _rows(
        session,
        """
        SELECT id, status, created_at, product_id FROM compose_tasks
        WHERE status <> 'registered' AND created_at < :cutoff
        ORDER BY id
        """,
        {"cutoff": now - timedelta(hours=STALE_COMPOSE_HOURS)},
    )
    for task_id, status, created_at, product_id in compose:
        age = age_hours(created_at, now) or 0.0
        add(
            f"C-{task_id}",
            "成片任务残留",
            f"{status} 已 {age:.1f}h（商品 {product_id}）",
            "retire",
            "超 1h 未转 registered 的候选——人审后发布或清理暂存字节",
        )

    # 4d 验收录像（label 含探针/probe 或 id∈{5,6}）
    recordings = _rows(
        session,
        """
        SELECT r.id, r.label, r.size_bytes,
               (SELECT count(*) FROM clip_candidates c WHERE c.recording_id = r.id) AS candidates,
               (SELECT count(*) FROM clip_candidates c WHERE c.recording_id = r.id AND c.status = 'pending') AS pending
        FROM clip_recordings r
        WHERE r.label LIKE '%探针%' OR r.label LIKE '%probe%' OR r.id IN (5, 6)
        ORDER BY r.id
        """,
    )
    for rec_id, label, size_bytes, candidates, pending in recordings:
        add(
            f"R-{rec_id}",
            "验收录像",
            f"{label}（{size_bytes} B，挂候选 {candidates}/待拣 {pending}）",
            "retire",
            "ASR 验收探针录像——删录像行与挂其上的待拣候选（既有候选 41 见下）",
        )
    probe_candidates = _rows(
        session,
        """
        SELECT c.id, c.status, c.recording_id
        FROM clip_candidates c
        WHERE c.recording_id IN (5, 6)
        ORDER BY c.id
        """,
    )
    for cand_id, status, recording_id in probe_candidates:
        add(
            f"CD-{cand_id}",
            "验收录像候选",
            f"{status}（录像 R-{recording_id}）",
            "retire",
            "探针录像转写出的候选（93 刀验收残留）——与录像一并清",
        )

    # 4e ABCD/WANDS 存量对话（审阅与数码店不匹配）
    dialogues = _rows(
        session,
        """
        SELECT id, title, status, source_kind FROM assets
        WHERE discarded_at IS NULL AND kind = 'dialogue'
          AND source_kind IN ('session_backflow', 'wands')
        ORDER BY source_kind, id
        """,
    )
    universe = _product_universe(session)
    flagged = 0
    for asset_id, title, status, source_kind in dialogues:
        text = (title or "").lower()
        ascii_letters = sum(1 for ch in title or "" if ch.isascii() and ch.isalpha())
        ascii_ratio = ascii_letters / max(1, len(title or ""))
        matched = [term for term in OFF_SCOPE_TERMS if term.lower() in text]
        in_store = offers_known_product(title or "", universe)
        mismatched = (ascii_ratio >= 0.3 and not in_store) or bool(matched)
        if not mismatched:
            continue
        flagged += 1
        cause = (
            f"英文标题（ASCII 字母占比 {ascii_ratio:.0%}）"
            if ascii_ratio >= 0.3
            else f"非数码类目词：{'、'.join(matched)}"
        )
        add(
            _asset_ref(asset_id),
            "ABCD/WANDS 存量",
            f"{source_kind}/{status}：{_cell(title, 50)}（{cause}）",
            "retire",
            "与数码店语境不匹配的回流对话——已发布者同时污染检索语料，建议废弃",
        )
    section.headline = (
        f"探针资产 {len(probes)}；VLM 探针图 {len(vlm_probes)}；"
        f"素材任务残留 {len(material)}+僵死 {len(running)}；成片残留 {len(compose)}；"
        f"验收录像 {len(recordings)}+候选 {len(probe_candidates)}；"
        f"不匹配对话 {flagged}/{len(dialogues)}"
    )
    if not dialogues:
        section.notes.append("source_kind=session_backflow/wands 的 dialogue 资产为空。")
    elif flagged < len(dialogues):
        section.notes.append(
            f"另外 {len(dialogues) - flagged} 份回流对话与数码店语境兼容（中文、点到在售商品），未列入。"
        )
    return section


# ---------- 检查 5：缺口池健康 ----------


def _gap_verdict_heuristics(
    question: str, universe: list[str]
) -> tuple[str, str, str | None]:
    """缺口分类启发（纯函数，单测覆盖）：→ (分类, 理由, 命中商品名)。

    分类：元问题误落 / OOV 误落 / 经营范围正常 / 普通待补。
    """
    if is_meta_question(question):
        return "元问题误落", "以「我」开头且含「问题/上一个」——多轮记忆类问句", None
    kind = scope_question_kind(question, universe)
    if kind == "out_of_scope":
        return "OOV 误落", "「有没有 X 类」问句且 X 不在商品表（86 刀语义：只建工单不落缺口）", None
    if kind == "in_scope":
        return "经营范围正常", "问句点到库内在售商品（补货/库存类，可答）", offers_known_product(question, universe)
    return "普通待补", "既有形态待补（知识/口径类）", None


def check_gap_pool(
    session: Any,
    *,
    retrieve_fn: Callable[[Any, str], list[dict[str, Any]]] | None = None,
    progress: Callable[[str], None] = lambda _line: None,
) -> Section:
    """37 open 缺口：retrieve 复答（漏网）/ 元问题 / OOV 三类 + 正常待补。"""
    if retrieve_fn is None:
        from suite_api.services.retrieval import retrieve as retrieve_fn  # type: ignore[assignment]

    universe = _product_universe(session)
    gaps = _rows(
        session,
        """
        SELECT id, question
        FROM knowledge_gaps WHERE status = 'open'
        ORDER BY id
        """,
    )
    section = Section(
        key="gaps",
        title="缺口池健康",
        headline="",
        headers=["缺口", "问句", "分类", "现在可答（retrieve 命中）", "建议"],
    )
    answerable = 0
    meta_gaps = 0
    oov_gaps = 0
    for gap_id, question in gaps:
        hits = retrieve_fn(session, question)
        top = ""
        if hits:
            answerable += 1
            top = f"{_asset_ref(hits[0]['asset_id'])}·v{hits[0]['version_no']}"
        verdict, reason, matched = _gap_verdict_heuristics(question, universe)
        if verdict == "元问题误落":
            meta_gaps += 1
        elif verdict == "OOV 误落":
            oov_gaps += 1
        if hits:
            action = "fix"
            action_reason = (
                f"auto-close 漏网候选：同问现在有命中（{top}）——现场重问一次触发收口"
            )
        elif verdict == "元问题误落":
            action = "fix"
            action_reason = "元问题误落——带多轮历史重问可答（记忆路径），或人工收口"
        elif verdict == "OOV 误落":
            action = "fix"
            action_reason = "OOV 误落——本店无此商品应只建工单；人工收口或重走一次 CS 路径"
        else:
            action = "keep"
            action_reason = "正常待补（补文档后发布即收口）"
        if matched:
            reason += f"；命中在售：{matched}"
        section.rows.append(
            [
                f"G-{gap_id}",
                _cell(question, 50),
                f"{verdict}（{reason}）",
                top or "无命中",
                f"{action}：{action_reason}",
            ]
        )
        section.actions.append(Action(f"G-{gap_id}", action, action_reason))
    section.headline = (
        f"{len(gaps)} open：auto-close 漏网候选 {answerable}、元问题误落 {meta_gaps}、"
        f"OOV 误落 {oov_gaps}、其余 {len(gaps) - answerable - meta_gaps - oov_gaps} 正常待补"
    )
    section.notes.append(
        "retrieve 命中≠一定答得好（弱命中也可能照旧拒答）；漏网口径=「同问现在有证据」，"
        "施工时以现场重问为准（一次重问即触发 resolve_gap_answered_by_catalog）。"
    )
    section.notes.append(
        "G-80 是 96 刀明示的演示素材（demo_prepare 检查点在库 open，幕 11 用）——"
        "若要收口它，先确认演示手册是否还依赖（其余 OOV 误落行无此约束）。"
    )
    return section


# ---------- 检查 6：口径冗余 ----------


def check_redundancy(session: Any) -> Section:
    """四主题口径冗余（同主题 >1 份已发布文档）+ 重复标题资产。"""
    published = _rows(
        session,
        """
        SELECT id, title, status, kind, source_kind FROM assets
        WHERE discarded_at IS NULL AND status = 'published'
        ORDER BY id
        """,
    )
    assets = [
        {"id": row[0], "title": row[1], "status": row[2], "kind": row[3], "source_kind": row[4]}
        for row in published
    ]
    groups = redundancy_groups(assets)
    section = Section(
        key="redundancy",
        title="口径冗余",
        headline="",
        headers=["主题", "成员", "判定", "建议"],
    )
    for topic, members in groups.items():
        ids = "、".join(_asset_ref(member["id"]) for member in members)
        titles = "；".join(_cell(member["title"], 30) for member in members)
        keeper = members[-1]  # 最新一份为保留主份（口径以最近治理为准）
        section.rows.append(
            [
                topic,
                f"{len(members)} 份：{ids}",
                f"{titles}",
                f"fix：同主题多份已发布——合并口径，保留最新 {_asset_ref(keeper['id'])}，其余废弃或改写",
            ]
        )
        for member in members:
            action = "keep" if member["id"] == keeper["id"] else "fix"
            reason = (
                f"同主题保留主份（{topic}）"
                if action == "keep"
                else f"{topic}冗余副本——合并进 {_asset_ref(keeper['id'])} 后废弃"
            )
            section.actions.append(Action(_asset_ref(member["id"]), action, reason))
    # 重复标题资产（跨主题的第二类冗余形态）
    alive = _rows(
        session,
        """
        SELECT id, title, status, kind, source_kind FROM assets
        WHERE discarded_at IS NULL ORDER BY id
        """,
    )
    dup_titles = duplicate_title_groups(
        [
            {"id": row[0], "title": row[1], "status": row[2], "kind": row[3], "source_kind": row[4]}
            for row in alive
        ],
    )
    for title, ids in dup_titles:
        if PROBE_TITLE_PATTERN.search(title):
            continue  # 探针标题重复是第 4 类的账，不重复报
        refs = "、".join(_asset_ref(asset_id) for asset_id in ids)
        section.rows.append(
            [
                "重题资产",
                f"{len(ids)} 份：{refs}",
                _cell(title, 40),
                "fix/retire：同题重复（回流多次/重跑）——保留 1 份，其余废弃",
            ]
        )
        # 主份选已发布者（检索/引用实际用的那份），否则最低 id
        published_ids = {
            row["id"] for row in assets if row["status"] == "published"
        }
        keep_id = next((asset_id for asset_id in ids if asset_id in published_ids), ids[0])
        for asset_id in ids:
            action = "keep" if asset_id == keep_id else "retire"
            reason = "同题保留主份" if action == "keep" else "同题重复副本（回流/重跑遗留）"
            section.actions.append(Action(_asset_ref(asset_id), action, reason))
    published_topics = sum(len(members) for members in groups.values())
    section.headline = (
        f"主题冗余 {len(groups)} 组（{published_topics} 份已发布）；"
        f"重题资产 {len(dup_titles)} 组"
    )
    section.notes.append(
        "主题分组只吃已发布、kind=document 且非 review_import 的行——评论标题碰巧含"
        "「退货/发货」等词（如 A-301）不是口径文档，不计入口径冗余。"
    )
    return section


# ---------- 检查 7：会话/工单残留 ----------


def check_session_ticket_residue(session: Any, now: datetime) -> Section:
    """空会话（demo_reset 同判据）+ pending 超 24h 陈旧工单。"""
    empty = _rows(
        session,
        """
        SELECT s.id, s.status, s.created_at FROM service_sessions s
        WHERE NOT EXISTS (SELECT 1 FROM service_messages m WHERE m.session_id = s.id)
          AND NOT EXISTS (SELECT 1 FROM handoff_tickets t WHERE t.session_id = s.id)
          AND NOT EXISTS (SELECT 1 FROM session_ratings r WHERE r.session_id = s.id)
          AND NOT EXISTS (SELECT 1 FROM knowledge_gaps g WHERE g.session_id = s.id)
          AND s.registered_asset_id IS NULL
        ORDER BY s.id
        """,
    )
    stale = _rows(
        session,
        """
        SELECT t.id, t.created_at
        FROM handoff_tickets t
        WHERE t.status = 'pending' AND t.created_at < :cutoff
        ORDER BY t.created_at
        """,
        {"cutoff": now - timedelta(hours=STALE_TICKET_HOURS)},
    )
    pending_total = _rows(session, "SELECT count(*) FROM handoff_tickets WHERE status = 'pending'")[0][0]
    section = Section(
        key="sessions",
        title="会话 / 工单残留",
        headline=(
            f"空会话 {len(empty)} 条；pending 工单 {int(pending_total)} 张"
            f"（其中陈旧 >{STALE_TICKET_HOURS}h {len(stale)} 张）"
        ),
        headers=["对象", "现状", "判定", "建议"],
    )
    for session_id, status, created_at in empty:
        age = age_hours(created_at, now) or 0.0
        section.rows.append(
            [
                f"S-{session_id}",
                f"{status}，创建于 {created_at:%Y-%m-%d %H:%M}（{age:.0f}h 前）",
                "空会话（无消息/工单/评分/缺口/回流锚）",
                "retire：跑 `python scripts/demo_reset.py --db … --apply` 批量清（同判据）",
            ]
        )
        section.actions.append(Action(f"S-{session_id}", "retire", "空会话（demo_reset 判据）"))
    buckets: dict[str, list[int]] = defaultdict(list)
    for ticket_id, created_at in stale:
        age = age_hours(created_at, now) or 0.0
        buckets[stale_bucket(age)].append(int(ticket_id))
    for bucket in ("≥5 天", "1–5 天"):
        ids = buckets.get(bucket, [])
        if not ids:
            continue
        section.rows.append(
            [
                f"H-*（{bucket}）",
                f"{len(ids)} 张：{compact_id_ranges(ids, 'H')}",
                f"pending 超 {STALE_TICKET_HOURS}h（无分派/SLA 语义）",
                "retire：人工批量置 resolved（留了联系方式的先跟进）",
            ]
        )
        section.actions.append(Action(f"H-*({bucket})", "retire", f"陈旧 pending 工单 {len(ids)} 张（{bucket}）"))
    if not empty:
        section.notes.append("无空会话。")
    if not stale:
        section.notes.append(f"无超 {STALE_TICKET_HOURS}h 的 pending 工单。")
    section.notes.append(
        "工单两态无 SLA（ADR 0046）：陈旧 pending 是运营待办不是数据缺陷——清与否由 109 刀定。"
    )
    return section


# ---------- 快照 / 组装 / 渲染 ----------


def build_meta(session: Any, db_url: str, now: datetime) -> dict[str, Any]:
    """报告头快照：日期/库/资产总数与分状态计数。"""
    masked = re.sub(r"(://[^:/@]+:)[^@]+(@)", lambda m: m.group(1) + "***" + m.group(2), db_url)
    totals = _rows(
        session,
        """
        SELECT count(*) FILTER (WHERE discarded_at IS NULL) AS alive,
               count(*) FILTER (WHERE discarded_at IS NOT NULL) AS discarded,
               count(*) FILTER (WHERE discarded_at IS NULL AND status = 'published') AS published,
               count(*) FROM assets
        """,
    )[0]
    by_kind = _rows(
        session,
        """
        SELECT kind, status, count(*) FROM assets
        WHERE discarded_at IS NULL GROUP BY kind, status ORDER BY kind, status
        """,
    )
    return {
        "date": now.astimezone().strftime("%Y-%m-%d %H:%M %Z"),
        "db": masked,
        "alive": int(totals[0]),
        "discarded": int(totals[1]),
        "published": int(totals[2]),
        "total": int(totals[3]),
        "by_kind": [(kind, status, int(count)) for kind, status, count in by_kind],
    }


def run_audit(
    session: Any,
    storage: Any,
    db_url: str,
    *,
    now: datetime | None = None,
    vlm_call: Callable[[bytes], str] | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    vlm_interval: float = VLM_INTERVAL_DEFAULT,
    skip_vlm: bool = False,
    retrieve_fn: Callable[[Any, str], list[dict[str, Any]]] | None = None,
    progress: Callable[[str], None] = lambda _line: None,
) -> AuditReport:
    """跑七类检查 → AuditReport（只读；VLM/retrieve 可注入替身便于测试）。"""
    now = now or datetime.now(UTC)
    sections = [
        check_image_consistency(
            session,
            storage,
            vlm_call=vlm_call,
            sleeper=sleeper,
            interval=vlm_interval,
            skip_vlm=skip_vlm,
            progress=progress,
        ),
        check_byte_duplicates(session, storage),
        check_titles(session),
        check_acceptance_residue(session, now),
        check_gap_pool(session, retrieve_fn=retrieve_fn, progress=progress),
        check_redundancy(session),
        check_session_ticket_residue(session, now),
    ]
    return AuditReport(meta=build_meta(session, db_url, now), sections=sections)


def render_markdown(report: AuditReport) -> str:
    """AuditReport → Markdown（--report 直出的报告全文）。"""
    meta = report.meta
    lines = [
        "# 数据中心健康审计报告（第 108 刀）",
        "",
        f"- 日期：{meta['date']}",
        f"- 库：{meta['db']}",
        (
            f"- 资产快照：总 {meta['total']}（未废弃 {meta['alive']} / 已废弃 {meta['discarded']}；"
            f"已发布 {meta['published']}）"
        ),
        "- 脚本：`scripts/realdata/data_health_check.py`（只读审计，不改任何数据）",
        "",
        "## 0. 摘要",
        "",
        "| 检查 | 计数 |",
        "| --- | --- |",
    ]
    for section in report.sections:
        lines.append(f"| {section.title} | {section.headline} |")
    lines.append("")
    kind_counts = " / ".join(
        f"{kind}·{status} {count}" for kind, status, count in meta["by_kind"]
    )
    lines.append(f"未废弃资产构成：{kind_counts}")
    lines.append("")
    for index, section in enumerate(report.sections, 1):
        lines.append(f"## {index}. {section.title}（{section.headline}）")
        lines.append("")
        if section.headers:
            lines.append(_md_row(section.headers))
            lines.append(_md_row(["---"] * len(section.headers)))
            for row in section.rows:
                lines.append(_md_row([_cell(value, 200) for value in row]))
        for note in section.notes:
            lines.append(f"> {note}")
        if section.notes:
            lines.append("")
        lines.append("")
    actions = report.all_actions()
    lines.append("## 建议动作汇总表（109 刀施工单）")
    lines.append("")
    for kind, label in (("retire", "retire（清出/终止）"), ("fix", "fix（修复/收口）"), ("keep", "keep（保留不动）")):
        listed = [action for action in actions if action.action == kind]
        lines.append(f"### {label}（{len(listed)}）")
        lines.append("")
        lines.append("| 对象 | 理由 |")
        lines.append("| --- | --- |")
        for action in listed:
            lines.append(_md_row([action.ref, _cell(action.reason, 200)]))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def print_terminal(report: AuditReport, *, max_rows: int = 25) -> None:
    """终端报告（表格全量进 Markdown；终端只给计数 + 截断行）。"""
    meta = report.meta
    print("== 数据中心健康审计（第 108 刀，只读） ==")
    print(f"库：{meta['db']}")
    print(
        f"资产：总 {meta['total']}（未废弃 {meta['alive']} / 已发布 {meta['published']}）"
    )
    for index, section in enumerate(report.sections, 1):
        print(f"\n[{index}] {section.title}：{section.headline}")
        if section.rows and section.headers:
            print("    " + " | ".join(section.headers))
        for row in section.rows[:max_rows]:
            print("    " + " | ".join(_cell(value, 38) for value in row))
        if len(section.rows) > max_rows:
            print(f"    …（还有 {len(section.rows) - max_rows} 行，见报告）")
        for note in section.notes:
            print(f"    注：{note}")
    actions = report.all_actions()
    print(
        "\n建议动作：retire {retire} / fix {fix} / keep {keep}".format(
            retire=sum(1 for action in actions if action.action == "retire"),
            fix=sum(1 for action in actions if action.action == "fix"),
            keep=sum(1 for action in actions if action.action == "keep"),
        )
    )


# ---------- 入口 ----------


def load_env_file(path: Path) -> int:
    """极简 .env 解析（同 load_abcd_dialogues.load_env_file 口径，标准库）。

    已设环境变量优先（不覆盖）；载入哪几个键只影响默认值（DATABASE_URL/
    STORAGE_ROOT 与 VLM key），显式 --db/--storage-root 恒优先。
    """
    if not path.is_file():
        return 0
    loaded = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key, value = key.strip(), value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value
            loaded += 1
    return loaded


def _resolve_storage_root(raw: str) -> Path:
    """相对路径按仓库根解析（脚本从任意 cwd 跑都指向同一对象存储）。"""
    path = Path(raw)
    return path if path.is_absolute() else (REPO_ROOT / path).resolve()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="数据中心健康审计（只读，不修任何数据）")
    parser.add_argument("--db", default=os.environ.get("DATABASE_URL"))
    parser.add_argument(
        "--storage-root",
        default=os.environ.get("STORAGE_ROOT", "./data/objects"),
        help="对象存储根（默认 .env/STORAGE_ROOT 或 ./data/objects；相对路径按仓库根解析）",
    )
    parser.add_argument(
        "--report",
        nargs="?",
        const=str(DEFAULT_REPORT_PATH),
        default=None,
        metavar="PATH",
        help=f"直出 Markdown 报告（默认 {DEFAULT_REPORT_PATH}）",
    )
    parser.add_argument("--skip-vlm", action="store_true", help="跳过真 VLM（离线/省调用）")
    parser.add_argument(
        "--vlm-interval", type=float, default=VLM_INTERVAL_DEFAULT,
        help=f"VLM 串行限流间隔秒（默认 {VLM_INTERVAL_DEFAULT}）",
    )
    parser.add_argument("--env-file", default=str(REPO_ROOT / ".env"), help="启动前载入的 .env")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    default_env = REPO_ROOT / ".env"
    loaded = load_env_file(default_env)
    args = parse_args(argv)
    if args.env_file != str(default_env):
        loaded += load_env_file(Path(args.env_file))
    if not args.db:
        print("错误：需要 --db 或 DATABASE_URL", file=sys.stderr)
        return 2

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from suite_api.db import check_database, to_sqlalchemy_url
    from suite_platform.storage import LocalDirectoryStorage

    if not check_database(args.db):
        print(f"错误：连不上库 {_cell(args.db, 80)}", file=sys.stderr)
        print(
            "提示：compose 数据库的宿主端口是 5433——"
            "--db postgresql://suite:suite@localhost:5433/suite",
            file=sys.stderr,
        )
        return 2
    if loaded:
        print(f"已从 .env 载入 {loaded} 个环境变量（库址/存储根/VLM 凭证默认值）")
    storage_root = _resolve_storage_root(args.storage_root)
    print(f"对象存储根：{storage_root}")
    if not args.skip_vlm:
        from suite_api.services.vlm import is_configured

        if is_configured():
            print(f"VLM：已配置——真调用 7 份，串行限流 {args.vlm_interval:.1f}s/份")
        else:
            print("VLM：未配置 key——本轮按「无法核验」记录（fail-诚实）")

    engine = create_engine(to_sqlalchemy_url(args.db))
    storage = LocalDirectoryStorage(storage_root)
    with Session(engine) as session:
        report = run_audit(
            session,
            storage,
            args.db,
            vlm_interval=args.vlm_interval,
            skip_vlm=args.skip_vlm,
            progress=print,
        )
    engine.dispose()

    print_terminal(report, max_rows=15 if args.report else 25)
    if args.report:
        path = Path(args.report)
        if not path.is_absolute():
            path = (REPO_ROOT / path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_markdown(report), encoding="utf-8")
        print(f"\n报告已写出：{path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
