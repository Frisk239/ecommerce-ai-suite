"""素材中心任务服务（第 17 刀，ADR 0038）：生成→规则质检→操作者抽检→登记。

状态机（0038，任务留在素材中心、不是中台对象，0012）::

    queued → running → pending_qc（规则质检过线，等人抽检）
                     ↘ failed（生成不可用/坏输出/规则不过/人工打回——可重试）
    pending_qc → registered（抽检通过，成品登记为资产，终态）

- **同步就地执行**：建任务的 API 请求内完成 LLM 生成+质检（≤20s，同回流机洗
  的 asyncio.run 线程池前提——消费方路由恒为同步 def，FastAPI 丢线程池）。
  queued/running 是落库可见的瞬时态：running 在调 LLM 前 commit（P1#2 同款
  纪律：LLM 等待不持有写事务）。
- **无降级模板**（0038）：生成是任务的本体，LLM 未配置/失败=failed，不像
  问答可回退证据组装。失败不进中台（0029）：failed 不登记任何字节，
  registered 才经 register_asset 写对象存储。
- **规则质检是纯函数**（0029「质检过线才登记」的代码侧第一道）：
  ``qc_check`` 四条——正文非空、总长 ≤2000、标题非空、正文必含商品名；
  不过 → failed（last_error 记规则项），过 → pending_qc 等**操作者抽检**
  （第二道是人，approve 才登记）。
"""

import asyncio
import json
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from suite_api.models import MaterialTask, Product
from suite_api.services import llm
from suite_api.services.machine_wash import strip_code_fence
from suite_api.services.registration import register_asset
from suite_platform.storage import ObjectStorage

# 任务五态（0038）：与 asset 三态无关，不共用词表
QUEUED = "queued"
RUNNING = "running"
PENDING_QC = "pending_qc"
REGISTERED = "registered"
FAILED = "failed"

TASK_STATUSES = (QUEUED, RUNNING, PENDING_QC, REGISTERED, FAILED)

# 规则质检阈值（0038 锁死：非空/总长≤2000/标题非空/正文含商品名）
MAX_TOTAL_CHARS = 2000

GENERATION_SYSTEM_PROMPT = (
    "你是商家侧电商内容创作助手，为指定商品撰写营销卖点文案。\n"
    "只依据给出的商品信息创作；信息里没有的规格参数与承诺不要编造。\n"
    "正文分行写卖点，每行一条，突出商品名称；商品有规格事实（如净含量、材质）"
    "时，用「字段：值」的行文在正文中带出，便于后续结构化。\n"
    '只输出一个 JSON 对象，形如 {"title": "标题", "content": "正文"}；'
    "不要输出 JSON 以外的任何解释文字。"
)


class MaterialGenError(Exception):
    """生成侧失败（LLM 不可用/坏输出）：任务 failed，last_error 记原因。"""


def build_generation_prompt(product: Product) -> str:
    """user prompt：商品名+类目+规格字段模板+已写回的规格值（生成依据的事实面）。"""
    spec_values = [
        f"{field}：{entry.get('value')}"
        for field, entry in dict(product.spec_values).items()
        if isinstance(entry, dict) and entry.get("value")
    ]
    lines = [
        f"商品名称：{product.name}",
        f"类目：{product.category}",
        f"规格字段：{'、'.join(dict(product.spec_schema).keys()) or '无'}",
    ]
    if spec_values:
        lines.append("已知规格值：\n" + "\n".join(spec_values))
    lines.append(f"请为「{product.name}」生成一条卖点文案（标题 + 正文）。")
    return "\n".join(lines)


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


def run_generation_task(db: Session, storage: ObjectStorage, task: MaterialTask) -> MaterialTask:
    """queued/failed → running → pending_qc | failed（同步就地，0038）。

    LLM 未配置或调用失败=failed「生成不可用」（无降级模板）；坏输出=failed
    「生成结果解析失败」；规则不过=failed（last_error 记规则项）。running 先
    commit 落库可见，同时保证 LLM 等待（≤20s）不持有写事务（P1#2 同款纪律）。
    终态推进由调用方（本函数内）commit 收口。
    """
    if task.status not in (QUEUED, FAILED):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"只有排队或失败的任务可以执行生成，当前状态: {task.status}",
        )
    product = db.get(Product, task.product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="商品不存在")

    task.status = RUNNING
    task.last_error = None
    db.commit()  # running 落库可见 + 释放事务再等 LLM

    try:
        raw = asyncio.run(
            llm.complete_chat(GENERATION_SYSTEM_PROMPT, build_generation_prompt(product))
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

    errors = qc_check(title, content, product.name)
    if errors:
        task.title, task.content = title[:200], content  # 坏生成也留预览面，供人看原因
        _fail(task, "规则质检不过线：" + "；".join(errors))
        db.commit()
        return task

    task.title = title[:200]  # String(200) 列宽收口，超长截断不炸库
    task.content = content
    task.last_error = None
    task.status = PENDING_QC
    db.commit()
    return task


def approve_task(db: Session, storage: ObjectStorage, task: MaterialTask) -> MaterialTask:
    """抽检通过（pending_qc → registered）：成品经 register_asset 登记为资产。

    kind=material、source_kind=material_generated（0025 已锁枚举）；登记内部
    已含同步机洗推进（素材文案按所挂商品规格字段跑正则，抽不到=弃权照常推进
    待人洗，0009）——登记即已接入→待人洗，之后走既有人洗发布，无特判。
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
    task.status = REGISTERED
    db.commit()
    return task


def reject_task(task: MaterialTask) -> MaterialTask:
    """抽检打回（pending_qc → failed，原因=人工打回）：可重试再生成。"""
    if task.status != PENDING_QC:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"只有待抽检的任务可以打回，当前状态: {task.status}",
        )
    return _fail(task, "人工打回")


def retry_task(db: Session, storage: ObjectStorage, task: MaterialTask) -> MaterialTask:
    """失败重试（failed → 重跑，0038：同任务行状态回 running）：清 error 后
    复用 run_generation_task 做新一次生成。"""
    if task.status != FAILED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"只有失败的任务可以重试，当前状态: {task.status}",
        )
    task.last_error = None
    task.status = QUEUED  # 复位排队态，重跑即新一次生成
    return run_generation_task(db, storage, task)
