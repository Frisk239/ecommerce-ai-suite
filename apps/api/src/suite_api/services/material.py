"""素材中心任务服务（第 17 刀，ADR 0038；第 98 刀内容套件，ADR 0055）：
生成（三模板）→ 双闸质检（规则+LLM 事实性）→ 配图（文生图，可选）→
操作者抽检 → 登记（文案 material 资产 + 配图 image 资产）。

状态机（0038，任务留在素材中心、不是中台对象，0012）::

    queued → running → pending_qc（规则+LLM 双闸过线，等人抽检）
                     ↘ failed（生成不可用/坏输出/规则不过/LLM 质检不过/
                                人工打回——可重试）
    pending_qc → registered（抽检通过，成品登记为资产，终态）

- **同步就地执行**：建任务的 API 请求内完成 LLM 生成+双闸质检+配图
  （文案 ≤20s + 质检 ≤20s + 配图 ≤60s，同回流机洗的 asyncio.run 线程池
  前提——消费方路由恒为同步 def，FastAPI 丢线程池）。queued/running 是落库
  可见的瞬时态：running 在调 LLM 前 commit（P1#2 同款纪律：LLM 等待不持有
  写事务）。
- **无降级模板**（0038）：生成是任务的本体，LLM 未配置/失败=failed，不像
  问答可回退证据组装。失败不进中台（0029）：failed 不登记任何字节，
  registered 才经 register_asset 写对象存储。
- **三模板（第 98 刀，ADR 0055）**：站内投放文案（默认，第 17 刀形态）/
  小红书笔记体/短视频口播稿——**模板只是 prompt 参数，不是 Agent、不加
  planner**（运营不是 Agent 的裁决不动）。
- **质检双闸（ADR 0055）**：第一道 ``qc_check`` 四条纯函数规则（0038 不变）；
  第二道 **LLM 事实性质检**（同机洗调用模式：文案+商品规格事实给 LLM 问
  有没有矛盾/夸大/编造）——不过线=failed（last_error 记 LLM 闸原因，文案
  保留供人看，可重试）；两闸**独立记录**（规则项进 last_error、LLM 判定进
  ``qc_llm_passed`` 列）。第三道仍是人抽检（approve 才登记）。
- **配图（ADR 0055；第 115 刀 W15 语义重构）**：三模板三种配图语义——
  站内投放=**美化产品图**（真实商品图 → 指令式图像编辑，商品主体来自原图，
  imggen.edit_image；该商品没有图片资产=诚实跳过 skipped_no_image，
  不退回文生图冒充）；小红书=**封面文字卡**（确定性流水线渲染，cover_card，
  无 AI 无 key 无真图依赖）；短视频口播=**文生图背景图**（98 刀原语义不变：
  背景不含商品主张）。**无 IMGGEN key=诚实跳过**（skipped_no_key，任务不
  fail——配图是增值项不是任务本体）；生成失败=image_status=failed（同样
  不 fail 任务）；成功=字节暂存对象存储 material/ 前缀（不是资产，同
  clip_recordings 先例），抽检通过才登记为独立 image 资产（source_kind=
  material_generated）。**生成图=素材成品非知识证据**：不回写商品规格、
  描述走 94a 人洗治理。美化产品图所基于的真实图片记 image_reference_asset_id
  （迁移 0035，血缘锚）。
"""

import asyncio
import hashlib
import json
import logging
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from suite_api.models import Asset, AssetVersion, MaterialTask, Product
from suite_api.services import cover_card, imggen, llm
from suite_api.services.machine_wash import redact, strip_code_fence
from suite_api.services.registration import image_suffix, register_asset
from suite_platform.storage import ObjectStorage

logger = logging.getLogger(__name__)

# 任务五态（0038）：与 asset 三态无关，不共用词表
QUEUED = "queued"
RUNNING = "running"
PENDING_QC = "pending_qc"
REGISTERED = "registered"
FAILED = "failed"

# 打回理由长度上限（第 48 刀）：路由 422 挡超长，服务层兜底截断
REJECT_REASON_MAX = 200

TASK_STATUSES = (QUEUED, RUNNING, PENDING_QC, REGISTERED, FAILED)

# 规则质检阈值（0038 锁死：非空/总长≤2000/标题非空/正文含商品名）
MAX_TOTAL_CHARS = 2000

# 配图步七态（第 98 刀 ADR 0055；第 115 刀 W15a 增 skipped_no_image）。
# 取值由本模块收口，无 DB CHECK 同 status 风格。``requested`` 兼作**请求标志**：
# 建任务带 with_image 时落此值（无独立 with_image 列）——重试按它重跑配图步
# （旧暂存字节被新字节覆盖清理）。
IMAGE_NONE = "none"  # 未请求配图
IMAGE_REQUESTED = "requested"  # 请求了配图，尚未生成（建任务时的初值）
IMAGE_PENDING = "pending"  # 已生成，字节暂存待抽检后登记
IMAGE_REGISTERED = "registered"  # 抽检通过，已登记为 image 资产
IMAGE_SKIPPED_NO_KEY = "skipped_no_key"  # 无 IMGGEN_API_KEY，诚实跳过（编辑/文生图路径）
# 第 115 刀：该商品没有任何图片资产——「美化产品图」没有真图可美化，诚实跳过
# （值宽 ≤20：列是 String(20)，skipped_no_image=16）
# （不退回文生图：探针实证过「参考图里没有商品时走编辑=凭空画商品」）
IMAGE_SKIPPED_NO_IMAGE = "skipped_no_image"
IMAGE_FAILED = "failed"  # 配图失败（编辑/文生图/渲染任一环节；不 fail 任务）

# 可补配图的 image_status 集合（第 115 刀 retry-image 端点闸）：待抽检任务的
# 配图没成或没出（跳过/失败），先补前置条件（传商品图/配 key）后**只重跑配图步**，
# 不动已过双闸的文案。
IMAGE_RETRYABLE = (IMAGE_REQUESTED, IMAGE_SKIPPED_NO_KEY, IMAGE_SKIPPED_NO_IMAGE, IMAGE_FAILED)

# ---------------------------------------------------------------- 内容模板（第 98 刀）

# 三模板（ADR 0055：prompt 模板参数，不是 Agent）：gen_style 进生成 system
# prompt；image_prompt/image_size 只服务文生图路径（第 115 刀起仅口播背景图
# 在用——站内配图走图像编辑、小红书走文字卡流水线，见 _run_image_step 分派）。
# 第 115 刀 W16：gen_style 按爆款结构调研显式化（标题公式/段落结构/标签配比），
# 事实纪律不变（只写规格事实面，双闸照拦）。
TEMPLATES: dict[str, dict[str, str]] = {
    "station": {
        "name": "站内投放文案",
        "gen_style": (
            "标题不超过 16 字且必须包含商品名。正文分行写卖点，每行一条、每条"
            "只讲一个卖点，突出商品名称；商品有规格事实（如净含量、材质）时，用"
            "「字段：值」的行文在正文中带出，便于后续结构化。不用感叹号轰炸，"
            "不写事实里没有的承诺。"
        ),
        "image_prompt": (
            "电商商品横图：商品居中完整呈现，干净浅色背景，柔和棚拍光，"
            "突出材质质感与工艺细节，商业摄影风格"
        ),
        "image_size": "1024x1024",
    },
    "xhs": {
        "name": "小红书笔记体",
        "gen_style": (
            "用小红书笔记的口吻写，按爆款结构组织：\n"
            "1. 标题 ≤20 字、前 10 字内出现商品词，采用钩子式写法（痛点+方案 / "
            "数字+结果 / 对比+反转 三选一），不用「上新/满减促销」式直卖标题，"
            "也不用极限词（最好/第一/国家级）。\n"
            "2. 正文按「场景共鸣（1-2 句，描述真实使用情境）→ 痛点/冲突 → "
            "解决方案（3-5 个短段，每段只讲一个卖点，带具体规格数字）→ 行动"
            "引导（1-2 句）」推进；每段开头用 1-2 个 emoji 作段落标记，短句"
            "分段，像真实用户分享体验。\n"
            "3. 结尾单独一行给 3-5 个 # 开头的话题标签：1 个泛流量话题 + 2 个"
            "垂直细分话题 + 1 个长尾话题，不与正文重复堆词。\n"
            "商品名与真实规格仍须自然带入，不得编造事实里没有的参数或功效。"
        ),
        "image_prompt": (
            "小红书风格封面图：明亮通透的生活场景氛围，暖色调，清新干净的构图，"
            "有适度的留白呼吸感，生活方式博主审美"
        ),
        "image_size": "768x1024",
    },
    "short_video": {
        "name": "短视频口播稿",
        "gen_style": (
            "写成 15-30 秒口播量的短视频口播稿：按「开场钩子（前 3 秒抓人，"
            "疑问或反差开场）→ 卖点分镜 → 行动号召」三段推进，每段一行、以"
            "「【分镜 N】」开头；口播词口语化、每句 8-15 字、有节奏感、可直接"
            "照读，不堆长句；商品名与真实规格仍须自然带入，不编造。"
        ),
        "image_prompt": (
            "竖版短视频背景图：色彩饱和、动感氛围、视觉重心居中且四周留白，"
            "便于后期叠加字幕与贴纸，构图简洁不抢主体"
        ),
        "image_size": "768x1024",
    },
}

DEFAULT_TEMPLATE = "station"


def validate_template(template: str) -> str:
    """模板键校验（纯函数）：坏值 ValueError，路由层转 422（同 source_kind 先例）。"""
    if template not in TEMPLATES:
        raise ValueError(f"内容模板必须是 {'/'.join(TEMPLATES)} 之一，收到: {template!r}")
    return template


def template_name(template: str) -> str:
    return TEMPLATES[template]["name"]


_GENERATION_SYSTEM_PROMPT_BASE = (
    "你是商家侧电商内容创作助手，为指定商品撰写营销卖点文案。\n"
    "只依据给出的商品信息创作；信息里没有的规格参数与承诺不要编造。\n"
)


def generation_system_prompt(template: str = DEFAULT_TEMPLATE) -> str:
    """生成 system prompt（纯函数）：模板的 gen_style 拼在共同纪律之后。"""
    style = TEMPLATES[template]["gen_style"]
    return (
        f"{_GENERATION_SYSTEM_PROMPT_BASE}\n{style}\n"
        '只输出一个 JSON 对象，形如 {"title": "标题", "content": "正文"}；'
        "不要输出 JSON 以外的任何解释文字。"
    )


# 保留原常量名（站内模板=第 17 刀默认形态，既有引用/测试口径不漂）
GENERATION_SYSTEM_PROMPT = generation_system_prompt(DEFAULT_TEMPLATE)


class MaterialGenError(Exception):
    """生成侧失败（LLM 不可用/坏输出）：任务 failed，last_error 记原因。

    第 98 刀起兼承 LLM 质检侧的坏输出（「LLM 质检输出不可解析」）——两处的
    失败处理同形：任务 failed 可重试、文案保留预览。"""


def build_generation_prompt(product: Product) -> str:
    """user prompt：商品名+类目+规格字段模板+已写回的规格值（生成依据的事实面）。

    0038 修订（出口必掩，第 26 刀补漏——审计刀 5 P1①）：本函数是孪生于
    ops.build_generation_prompt 的第二条厂商 prompt 通道（21 刀掩了 ops 漏了
    这里）——商品文本进厂商前统一过 redact，规格写回值可能混有人工填的
    手机号/邮箱，厂商 prompt 是进程边界。redact 幂等，干净值原样通过。
    """
    lines = _product_facts(product)
    lines.append(f"请为「{redact(product.name)}」生成一条卖点文案（标题 + 正文）。")
    return "\n".join(lines)


def _product_facts(product: Product) -> list[str]:
    """商品事实行（生成与 LLM 质检共用同一事实面；出口必掩，0038）。"""
    spec_values = [
        f"{field}：{redact(str(entry.get('value')))}"
        for field, entry in dict(product.spec_values).items()
        if isinstance(entry, dict) and entry.get("value")
    ]
    lines = [
        f"商品名称：{redact(product.name)}",
        f"类目：{redact(product.category)}",
        f"规格字段：{redact('、'.join(dict(product.spec_schema).keys()) or '无')}",
    ]
    if spec_values:
        lines.append("已知规格值：\n" + "\n".join(spec_values))
    return lines


# ---------------------------------------------------------------- 配图（第 98 刀）


def build_image_prompt(product: Product, template: str = DEFAULT_TEMPLATE) -> str:
    """文生图 prompt（纯函数，模板派生）：风格行 + 商品事实 + 禁文字水印。

    第 115 刀起只服务**口播背景图**（背景不含商品主张，文生图语义成立）；
    站内配图走图像编辑（build_edit_instruction）、小红书走文字卡流水线。
    商品名/类目过 redact（0038 出口必掩——prompt 是进程边界）；文生图模型
    渲染文字易糊，显式要求画面不出现文字。
    """
    style = TEMPLATES[template]["image_prompt"]
    return (
        f"{style}。商品：{redact(product.name)}（类目：{redact(product.category)}）。"
        "画面中不要出现任何文字、水印或 logo。"
    )


def pick_reference_image(db: Session, product_id: int) -> tuple[Asset, AssetVersion] | None:
    """「美化产品图」的参考图解析：该商品挂载的图片资产，**已发布优先**。

    取值口径：kind=image、未废弃；先取有已发布指针的（线上口径、人洗核过），
    没有则最新登记的一张（待人洗草稿也是真图——用它做**生成输入**不构成
    「引用未发布口径」，产出仍走独立治理）。返回 (资产, 取字节用的版本)；
    商品没有任何图片资产 → None（调用方落 skipped_no_image）。
    """
    rows = db.scalars(
        select(Asset)
        .where(
            Asset.product_id == product_id,
            Asset.kind == "image",
            Asset.discarded_at.is_(None),
        )
        .order_by(Asset.current_published_version_id.desc().nullslast(), Asset.id.desc())
    ).all()
    for asset in rows:
        if asset.current_published_version_id is not None:
            version = db.get(AssetVersion, asset.current_published_version_id)
            if version is not None:
                return asset, version
    for asset in rows:  # 无已发布：最新一版的字节（草稿也是真图）
        version = db.scalar(
            select(AssetVersion)
            .where(AssetVersion.asset_id == asset.id)
            .order_by(AssetVersion.version_no.desc())
            .limit(1)
        )
        if version is not None:
            return asset, version
    return None


def build_edit_instruction(product: Product) -> str:
    """美化产品图的编辑指令（纯函数，站内模板）。

    指令模式按调研口径：动词前置（「将背景替换为…」）+ **保留项必申明**（商品
    外观/颜色/材质/细节完全不变——不申明模型会「自作主张优化」商品）+ 光照一致
    + 构图具象 + 禁止项。商品名过 redact（prompt 是进程边界）。
    """
    return (
        "将背景替换为干净明亮的浅色影棚场景：柔和棚拍光、浅色渐变底，"
        f"构图以{redact(product.name)}为绝对主体、商品居中完整可见、不裁切。"
        "保持商品的外观、颜色、材质、比例与所有细节完全不变，"
        "不要改变商品本身（不改形、不换色、不增减部件），"
        "新背景的光照方向与原图一致、投影自然。"
        "不要在画面中添加任何文字、水印或 logo。"
    )


def image_size_for(template: str) -> str:
    """配图尺寸（纯函数）：站内=方图 1024x1024，小红书/口播=竖版 768x1024。"""
    return TEMPLATES[template]["image_size"]


def _stage_image_key(content_bytes: bytes) -> str:
    """配图暂存键 ``material/{uuid}/{sha256前16}.{png|jpg|webp}``（魔数派生后缀）。

    不是资产键（documents/dialogue/clips 前缀都不占）——配图抽检通过前只是
    暂存字节（同 clip_recordings 先例），登记时由 register_asset 另写正键。
    """
    suffix = image_suffix(content_bytes) or "bin"
    digest = hashlib.sha256(content_bytes).hexdigest()[:16]
    return f"material/{uuid4().hex}/{digest}.{suffix}"


def image_title(product_name: str, template: str) -> str:
    """配图资产标题：``{商品名} · {模板名}配图``（基名截 40，同 frame_title 口径）。"""
    base = product_name.strip()[:40] or "商品"
    return f"{base} · {template_name(template)}配图"


def image_description_preset(content: str) -> str:
    """配图「图片描述」预填草稿（纯函数）：文案首行（截 80）。

    登记 image 资产时的 preset_fields——register_asset 内 VLM 若可用会看图
    出真草稿（优先生效，setdefault 不会覆盖）；VLM 无 key/失败时该预填兜底
    （extracted，待人洗可改，94a 治理）。值为文案首句摘要：描述的是「这是
    这条文案的配套图」，不是商品外观事实（ADR 0055 分层红线）。
    """
    first_line = next((line.strip() for line in content.splitlines() if line.strip()), "")
    return redact(first_line[:80])


# ---------------------------------------------------------------- LLM 事实性质检二道闸（第 98 刀）

QC_SYSTEM_PROMPT = (
    "你是电商营销文案的事实性审校。给你商品的真实事实（名称、类目、已知规格值）"
    "与一篇为该商品生成的营销文案，请逐项核对文案中的规格参数、材质、净含量、"
    "保质期等陈述是否与事实一致。\n"
    "只报三类问题：与给出的规格事实矛盾、夸大功效（事实里没有的承诺，如疗效/"
    "绝对化用语）、编造参数（事实里没有的数值或规格）。\n"
    "文案的写作风格、语气、emoji、话题标签不是问题，不要报告；事实里没有提及"
    "但文案也没有具体声称的不算问题。\n"
    '只输出一个 JSON 对象：{"passed": true, "issues": []}（没有问题）或 '
    '{"passed": false, "issues": ["一句话说明问题", ...]}。'
    "不要输出 JSON 以外的任何解释文字。"
)


def build_qc_prompt(title: str, content: str, product: Product) -> str:
    """LLM 质检 user prompt（纯函数）：商品事实面 + 待审文案（出口必掩，0038）。"""
    lines = _product_facts(product)
    lines.append("待审文案：")
    lines.append(f"标题：{redact(title)}")
    lines.append(f"正文：{redact(content)}")
    lines.append("请核对这篇文案与上述事实，按约定输出 JSON。")
    return "\n".join(lines)


def parse_qc_output(raw: str) -> tuple[bool, list[str]]:
    """LLM 质检输出 -> (passed, issues)（纯函数）：剥围栏 -> JSON -> 形状校验。

    坏 JSON / 非对象 / passed 非布尔 / issues 非字符串数组 = MaterialGenError
    （「LLM 质检输出不可解析」——fail-closed：质检闸跑不完就不放行进待抽检，
    任务 failed 可重试，与 roadmap「失败不进待抽检」口径一致）。
    """
    try:
        data: Any = json.loads(strip_code_fence(raw))
    except ValueError as exc:
        raise MaterialGenError("LLM 质检输出不可解析：输出不是合法 JSON") from exc
    if not isinstance(data, dict) or not isinstance(data.get("passed"), bool):
        raise MaterialGenError(
            'LLM 质检输出不可解析：须为 {"passed": bool, "issues": [str]} 形状 JSON 对象'
        )
    issues_raw = data.get("issues", [])
    if not isinstance(issues_raw, list) or not all(isinstance(i, str) for i in issues_raw):
        raise MaterialGenError(
            'LLM 质检输出不可解析：须为 {"passed": bool, "issues": [str]} 形状 JSON 对象'
        )
    return data["passed"], [issue.strip() for issue in issues_raw if issue.strip()]


def run_llm_qc(title: str, content: str, product: Product) -> tuple[bool, list[str]]:
    """LLM 事实性质检（第二道闸）：文案+事实给 LLM，返回 (passed, issues)。

    同机洗调用模式（asyncio.run + complete_chat，路由线程池前提）：
    LLMNotConfigured/LLMError 一律按「质检不可用」fail-closed 抛
    MaterialGenError——双闸过线才能进待抽检，闸跑不完不放行（生成侧已证明
    LLM 可用才走到这里，未配置属环境漂移，如实失败可重试）。
    """
    try:
        raw = asyncio.run(
            llm.complete_chat(QC_SYSTEM_PROMPT, build_qc_prompt(title, content, product))
        )
    except llm.LLMError as exc:
        raise MaterialGenError(f"LLM 事实性质检不可用：{exc}") from exc
    return parse_qc_output(raw)


# ---------------------------------------------------------------- 规则质检（第一道闸，0038 原样）


def parse_generated_output(raw: str) -> tuple[str, str]:
    """LLM 输出 -> (title, content)：剥围栏 -> JSON 对象 -> title/content 均非空串。

    坏 JSON / 不是对象 / 字段缺失或空 = MaterialGenError（「生成结果解析失败」
    ——与 LLM 故障分级不同，但同属生成侧失败，任务 failed 可重试）。
    """
    try:
        data: Any = json.loads(strip_code_fence(raw))
    except ValueError as exc:
        raise MaterialGenError("生成结果解析失败：输出不是合法 JSON") from exc
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("title"), str)
        or not isinstance(data.get("content"), str)
        or not data["title"].strip()
        or not data["content"].strip()
    ):
        raise MaterialGenError("生成结果解析失败：须为 {title, content} 非空字段 JSON 对象")
    return data["title"].strip(), data["content"].strip()


def qc_check(title: str | None, content: str | None, product_name: str) -> list[str]:
    """规则质检纯函数（0029 第一道闸门）：返回违规项列表，空列表=过线。

    四条（0038）：正文非空、总长 ≤2000、标题非空、正文必含商品名。
    代码不在 prompt 里——模型就算被叮嘱也不保证，落库前必须机器验一遍。
    """
    errors: list[str] = []
    if content is None or not content.strip():
        errors.append("正文不能为空")
    if title is None or not title.strip():
        errors.append("标题不能为空")
    if len(title or "") + len(content or "") > MAX_TOTAL_CHARS:
        errors.append(f"标题+正文总长不得超过 {MAX_TOTAL_CHARS} 字")
    if content is not None and product_name not in content:
        errors.append(f"正文必须包含商品名「{product_name}」")
    return errors


def _fail(task: MaterialTask, reason: str) -> MaterialTask:
    task.status = FAILED
    task.last_error = reason[:500]
    return task


# ---------------------------------------------------------------- 配图步（W15 三路分派）


def _stage_image(storage: ObjectStorage, task: MaterialTask, image_bytes: bytes) -> None:
    """配图字节落暂存键 + 清旧键（三路共用）：写只碰对象存储不碰 DB（P1#2：
    厂商/渲染等待不持有写事务，commit 收口在调用方）。"""
    old_key = task.image_object_key
    key = _stage_image_key(image_bytes)
    storage.put_bytes(key, image_bytes)
    if old_key and old_key != key:
        storage.delete(old_key)
    task.image_object_key = key
    task.image_status = IMAGE_PENDING


def _run_image_step(db: Session, storage: ObjectStorage | None, task: MaterialTask, product: Product) -> None:
    """配图步（with_image 请求才进来）：**三模板三路分派**（第 115 刀 W15），
    三态收口（pending / skipped_* / failed），永不 fail 任务。

    - **小红书 = 封面文字卡流水线**（cover_card，确定性渲染）：不调 AI、
      不需要 IMGGEN key、不需要商品真图——标题/商品名/类目都是任务已有事实。
      字体缺失=failed（不产豆腐块图）；同 spec 逐字节可复现。
    - **站内投放 = 美化产品图**（真实商品图 → imggen.edit_image 指令式编辑，
      商品主体来自原图）：无 key=skipped_no_key；**商品没有任何图片资产=
      skipped_no_image**（诚实跳过——探针实证过参考图里没有商品时走
      编辑等于凭空画商品，不退回文生图冒充）；编辑失败=failed。参考图
      （已发布优先）落 image_reference_asset_id（血缘锚）。
    - **口播 = 文生图背景图**（98 刀原语义不变：背景不含商品主张，文生图
      成立）：无 key=skipped_no_key；失败=failed。

    原因只进服务端日志；任务面只记状态事实（通用文案，0033 纪律）。
    """
    template = task.template or DEFAULT_TEMPLATE

    if template == "xhs":
        try:
            card = cover_card.render_cover_card(
                cover_card.CardSpec(
                    title=task.title or product.name,
                    product_name=product.name,
                    category=product.category,
                    palette_index=task.id,
                )
            )
        except cover_card.CoverCardError as exc:
            logger.warning("配图步失败: what=封面文字卡渲染 task=%s err=%s", task.id, exc)
            task.image_status = IMAGE_FAILED
            task.image_object_key = None
            task.image_reference_asset_id = None
            return
        if storage is None:  # pragma: no cover - 路由恒带 storage；防御替身调用面
            task.image_status = IMAGE_FAILED
            return
        _stage_image(storage, task, card)
        return

    if template == "station":
        if not imggen.is_configured():
            task.image_status = IMAGE_SKIPPED_NO_KEY
            task.image_object_key = None
            task.image_reference_asset_id = None
            return
        reference = pick_reference_image(db, product.id)
        if reference is None:
            task.image_status = IMAGE_SKIPPED_NO_IMAGE
            task.image_object_key = None
            task.image_reference_asset_id = None
            return
        asset, version = reference
        if storage is None:  # pragma: no cover - 同上
            task.image_status = IMAGE_FAILED
            return
        try:
            ref_bytes = storage.get_bytes(version.object_key)
        except FileNotFoundError as exc:
            logger.warning("配图步失败: what=参考图字节缺失 task=%s err=%s", task.id, exc)
            task.image_status = IMAGE_FAILED
            task.image_object_key = None
            task.image_reference_asset_id = None
            return
        try:
            image_bytes = imggen.edit_image(build_edit_instruction(product), ref_bytes)
        except imggen.ImggenError as exc:
            logger.warning("配图步失败: what=美化产品图编辑 task=%s err=%s", task.id, type(exc).__name__)
            task.image_status = IMAGE_FAILED
            task.image_object_key = None
            task.image_reference_asset_id = None
            return
        _stage_image(storage, task, image_bytes)
        task.image_reference_asset_id = asset.id
        return

    # short_video：文生图背景图（原路径）
    if not imggen.is_configured():
        task.image_status = IMAGE_SKIPPED_NO_KEY
        task.image_object_key = None
        return
    prompt = build_image_prompt(product, template)
    try:
        image_bytes = imggen.generate_image(prompt, size=image_size_for(template))
    except imggen.ImggenError:
        logger.warning("配图步失败: what=文生图背景 task=%s", task.id)
        task.image_status = IMAGE_FAILED
        task.image_object_key = None
        return
    if storage is None:  # pragma: no cover - 同上
        task.image_status = IMAGE_FAILED
        return
    _stage_image(storage, task, image_bytes)


def retry_image_step(db: Session, storage: ObjectStorage | None, task: MaterialTask) -> MaterialTask:
    """补配图（第 115 刀）：待抽检任务的配图没成/没出时**只重跑配图步**。

    闸门：只有 pending_qc 且 image_status ∈ IMAGE_RETRYABLE 才放行（409 由
    HTTPException 语义给出——文案已过双闸，重跑生成是「重试」端点的事，这里
    不动文案）。前置条件补齐（传了商品图 / 配了 key）后用本端点闭环：
    「先传图 → 补配图 → 预览 → 抽检」。
    """
    if task.status != PENDING_QC:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"只有待抽检的任务可以补配图，当前状态: {task.status}",
        )
    if task.image_status not in IMAGE_RETRYABLE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"配图当前不可补（image_status={task.image_status}）：未请求配图或已有待抽检配图",
        )
    product = db.get(Product, task.product_id)
    if product is None:  # pragma: no cover - FK 保证行存在；防御替身调用面
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="商品不存在")
    _run_image_step(db, storage, task, product)
    db.commit()
    db.refresh(task)
    return task


# ---------------------------------------------------------------- 任务状态机


def run_generation_task(
    db: Session, storage: ObjectStorage | None, task: MaterialTask
) -> MaterialTask:
    """queued/failed → running → pending_qc | failed（同步就地，0038/0055）。

    步序：生成（模板 prompt）→ 解析 → 规则闸 → LLM 事实性闸 → 配图步 →
    pending_qc。LLM 未配置或调用失败=failed「生成不可用」（无降级模板）；
    坏输出=failed「解析失败」；规则不过=failed（记规则项）；LLM 质检不过=
    failed（记 LLM 闸原因，文案与 qc_llm_passed=False 留档）；两闸任一失败
    都不跑配图（不给没过线的文案配图）。running 先 commit 落库可见，同时保证
    LLM 等待（≤20s）不持有写事务（P1#2 同款纪律）；终态推进由本函数 commit
    收口。

    **字节触点（第 98 刀起）**：配图步把生成字节暂存对象存储 ``material/``
    前缀（不是资产）；正式登记触点仍在 approve 的 register_asset。
    """
    if task.status not in (QUEUED, FAILED):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"只有排队或失败的任务可以执行生成，当前状态: {task.status}",
        )
    product = db.get(Product, task.product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="商品不存在")

    template = task.template or DEFAULT_TEMPLATE
    task.status = RUNNING
    task.last_error = None
    task.qc_llm_passed = None
    db.commit()  # running 落库可见 + 释放事务再等 LLM

    try:
        raw = asyncio.run(
            llm.complete_chat(generation_system_prompt(template), build_generation_prompt(product))
        )
    except llm.LLMNotConfigured as exc:
        _fail(task, f"生成不可用：未配置 LLM_API_KEY，素材生成没有降级模板（{exc}）")
        db.commit()
        return task
    except llm.LLMError as exc:
        _fail(task, f"生成不可用：{exc}")
        db.commit()
        return task

    try:
        title, content = parse_generated_output(raw)
    except MaterialGenError as exc:
        _fail(task, str(exc))
        db.commit()
        return task

    title = title[:200]  # String(200) 列宽收口先于质检（debt-2：qc 与落库同一字符串）
    errors = qc_check(title, content, product.name)
    if errors:
        task.title, task.content = title, content  # 坏生成也留预览面，供人看原因
        task.qc_llm_passed = None  # 规则闸先挡下，LLM 闸未跑到（两闸独立记录）
        _fail(task, "规则质检不过线：" + "；".join(errors))
        db.commit()
        return task

    # 第二道闸（第 98 刀）：LLM 事实性质检——文案+事实给 LLM 问矛盾/夸大/编造。
    # 失败（判定不过/不可用/坏输出）都不进待抽检：任务 failed 可重试，文案保留。
    try:
        passed, issues = run_llm_qc(title, content, product)
    except MaterialGenError as exc:
        task.title, task.content = title, content
        task.qc_llm_passed = False
        _fail(task, str(exc))
        db.commit()
        return task
    if not passed:
        task.title, task.content = title, content  # 保留预览面：人看得见哪句被拦
        task.qc_llm_passed = False
        _fail(task, "LLM 事实性质检不过线：" + "；".join(issues)[:300])
        db.commit()
        return task

    # 双闸过线：文案先落任务行（第 115 刀——小红书封面文字卡要拿**文案标题**做
    # 大字；此前赋值在配图步之后，卡只能拿到商品名兜底），再跑配图步（建任务带
    # with_image → image_status 非 none 才跑；重试同条件重跑，旧暂存字节由
    # _run_image_step 覆盖清理；skipped/failed 永不 fail 任务，抽检后可走
    # retry-image 补配图）。commit 仍在最后收口。
    task.title = title
    task.content = content
    task.qc_llm_passed = True
    task.last_error = None
    task.status = PENDING_QC
    if task.image_status != IMAGE_NONE:
        _run_image_step(db, storage, task, product)

    db.commit()
    return task


def approve_task(db: Session, storage: ObjectStorage, task: MaterialTask) -> MaterialTask:
    """抽检通过（pending_qc → registered）：文案登记 material 资产 + 配图登记
    image 资产（第 98 刀双资产，回执两资产 id）。

    文案：kind=material、source_kind=material_generated（0025 已锁枚举）；登记
    内部已含同步机洗推进（素材文案按所挂商品规格字段跑正则，抽不到=弃权照常
    推进待人洗，0009）。

    配图（image_status=pending 时）：暂存字节转正为独立 image 资产——
    kind=image、source_kind=material_generated、标题 ``{商品} · {模板名}配图``、
    「图片描述」预填文案首句摘要（VLM 可用时看图草稿优先，setdefault 不覆盖；
    人洗可改，94a 治理），挂同商品；暂存键随后删除。生成图=素材成品非知识
    证据（ADR 0055）：不回写商品规格（image 机洗只出图片描述，天然不碰规格）。
    """
    if task.status != PENDING_QC:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"只有待抽检的任务可以抽检通过，当前状态: {task.status}",
        )
    assert task.title is not None and task.content is not None  # pending_qc 恒有文案
    asset = register_asset(
        db,
        storage,
        kind="material",
        title=task.title,
        content_bytes=task.content.encode("utf-8"),
        filename=None,
        product_id=task.product_id,
        source_kind="material_generated",
    )
    task.asset_id = asset.id

    if task.image_status == IMAGE_PENDING and task.image_object_key:
        product = db.get(Product, task.product_id)
        staged_key = task.image_object_key
        try:
            image_bytes = storage.get_bytes(staged_key)
        except FileNotFoundError as exc:
            # 暂存字节丢了：配图登记失败如实落到任务面（任务停在 pending_qc 的
            # 转移上抛 409 前不落半途写——material 资产已登记但配图缺，让操作者
            # 重试/放弃，不静默吞成单资产）
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="配图暂存字节缺失，无法登记配图资产；可打回后重试重新生成",
            ) from exc
        image_asset = register_asset(
            db,
            storage,
            kind="image",
            title=image_title(
                product.name if product else "商品", task.template or DEFAULT_TEMPLATE
            ),
            content_bytes=image_bytes,
            filename=None,
            product_id=task.product_id,
            source_kind="material_generated",
            preset_fields={"图片描述": image_description_preset(task.content)},
        )
        storage.delete(staged_key)  # 暂存键转正后清理（孤儿只在打回/放弃路径，ADR Debt）
        task.image_asset_id = image_asset.id
        task.image_object_key = None
        task.image_status = IMAGE_REGISTERED

    task.status = REGISTERED
    db.commit()
    return task


def reject_task(db: Session, task: MaterialTask, *, reason: str | None = None) -> MaterialTask:
    """抽检打回（pending_qc → failed）：可重试再生成。

    第 48 刀：收可选打回理由（运营要看得见「为什么被打回」，否则生成方无从改）。
    理由复用既有 ``last_error`` 列（UI 已在展示失败原因，不新造列与展示口径）：
    有理由 -> ``人工打回：{reason}``，无 -> 保持 ``人工打回``。理由在服务层截断
    到 ``REJECT_REASON_MAX``，路由/服务都不接受超长（路由负责 422，服务负责
    兜底截断——服务也可能被别的调用方使用）。

    commit 收在服务层（debt-2 第 24 刀）：与 run/approve 同一转移纪律，路由层
    不再补 commit——非法转移 409 抛在 commit 前，天然不落半途写。
    """
    if task.status != PENDING_QC:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"只有待抽检的任务可以打回，当前状态: {task.status}",
        )
    cleaned = (reason or "").strip()
    detail = cleaned[:REJECT_REASON_MAX]
    _fail(task, f"人工打回：{detail}" if detail else "人工打回")
    db.commit()
    return task


def retry_task(db: Session, storage: ObjectStorage | None, task: MaterialTask) -> MaterialTask:
    """失败重试（failed → 直接 running 起新一次生成，0038 同任务行）：
    run_generation_task 的 failed 入口本就放行，queued→running 的内存死转移
    已删（debt-2 第 24 刀——queued 只在建任务落库瞬时存在，重试不再伪造它）。
    last_error 清空由 run_generation_task 在 running commit 前一并做。"""
    if task.status != FAILED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"只有失败的任务可以重试，当前状态: {task.status}",
        )
    return run_generation_task(db, storage, task)
