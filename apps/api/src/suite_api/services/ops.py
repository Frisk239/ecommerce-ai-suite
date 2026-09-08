"""运营 Agent 编排服务（第 22 刀，ADR 0041）：三步轨迹同步执行、失败续跑、投放确认。

**run 不是中台对象**（0041，同 0012 对 material_tasks 的口径）：只写 ops_runs
一张表，不进检索、不能发布、不进治理台、无 MCP 触点；「投放发布」是渠道动作
（v1 mock：只记 delivered_at），不改任何资产三态。

三步同步就地执行（0012 无队列先例，同素材生成），轨迹逐步落库可观察：

1. ``read_product``（via 中台接口·商品）——读商品名与规格字段；
2. ``gen_material``（via 厂商模型）——``complete_chat`` 直接生成投放文案草稿
   （不落素材任务、不落资产——运营中间产物只住 run 行内；要入库走素材中心
   人工路径）。**无降级**（0038 纪律）：LLM 未配置/失败=failed「生成不可用」，
   坏输出=failed「生成结果解析失败」；prompt 输入过 redact（0038 修订：出口
   必掩，厂商 prompt 是进程边界）。
3. ``compose``（via 中台接口·检索）——检索该商品**当前已发布**（指针非空）的
   material/video 资产做引用，refs 冻结当时版本号（0007 演示诚实度先例，
   指针前移不漂移）；无可用引用时正文由商品规格卖点兜底，step.detail 诚实
   披露「无已发布素材，正文由商品规格组装」（原型口径）。

事务纪律（同 material/coaching 先例）：每步 running 态**先 commit 落库**再
执行——LLM ≤20s 等待不持有写事务（P1#2）；步终态（done/failed + detail）再
commit 收口。失败步之后：后续步停 pending、轨迹中断，retry 从失败步续跑
（前序 done 不重跑；重置轨迹=前端新建 run）。

JSONB 列在-place 修改不触发脏标记（models 里没用 MutableDict）：轨迹/产出经
本地 plan dict 变更后**整体重赋** run.steps / run.output 再 commit。
"""

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from suite_api.models import Asset, AssetVersion, OpsRun, Product
from suite_api.services import llm
from suite_api.services.machine_wash import redact, strip_code_fence

# 步状态四态（0041，冻结原型 OpsStepStatus）；与资产三态、素材任务五态无关
PENDING = "pending"
RUNNING = "running"
DONE = "done"
FAILED = "failed"

# 三步键与展示元数据（key/name/via 冻结原型语义；gen_material 的 name 不写
# 「调用素材中心」——草稿不落任务不落成资产，via 如实标「厂商模型」）
STEP_READ = "read_product"
STEP_GEN = "gen_material"
STEP_COMPOSE = "compose"

STEP_DEFS: tuple[dict[str, str], ...] = (
    {"key": STEP_READ, "name": "读取商品卖点", "via": "中台接口 · 商品读取"},
    {"key": STEP_GEN, "name": "生成投放文案草稿", "via": "厂商模型"},
    {"key": STEP_COMPOSE, "name": "组装投放文案", "via": "中台接口 · 检索已发布素材"},
)

# compose 引用范围（ADR 0041：素材/切片两类，均 kind 维度）
REF_KINDS = ("material", "video")

# 无引用时 compose 的诚实披露（原型引用区口径 + spec 冻结文案）
NO_REF_DETAIL = "无已发布素材，正文由商品规格组装"

GEN_SYSTEM_PROMPT = (
    "你是商家侧电商运营助手，为指定商品撰写小红书风格的投放文案。\n"
    "只依据给出的商品信息创作；信息里没有的规格参数与承诺不要编造。\n"
    "正文分行写卖点，突出商品名称；口吻贴近种草笔记，不堆砌空洞形容词。\n"
    '只输出一个 JSON 对象，形如 {"title": "标题", "body": "正文"}；'
    "不要输出 JSON 以外的任何解释文字。"
)


class OpsGenError(Exception):
    """生成步失败（坏输出）：该步 failed「生成结果解析失败」，可重试。"""


def initial_steps() -> list[dict[str, Any]]:
    """新建 run 的三步初始轨迹：全 pending、detail 空。"""
    return [
        {"key": d["key"], "name": d["name"], "via": d["via"], "status": PENDING, "detail": ""}
        for d in STEP_DEFS
    ]


def build_generation_prompt(product: Product) -> str:
    """gen_material 的 user prompt：商品名+类目+规格字段模板+已写回规格值。

    0038 修订（出口必掩）：商品文本进厂商前统一过 redact——规格写回值可能
    混有人工填的手机号/邮箱，厂商 prompt 是进程边界。redact 幂等，干净值
    原样通过。
    """
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
        lines.append("已知规格值（治理过的口径，优先采信）：\n" + "\n".join(spec_values))
    lines.append(f"请为「{redact(product.name)}」生成一条小红书投放文案（标题 + 正文）。")
    return "\n".join(lines)


def parse_generated_output(raw: str) -> tuple[str, str]:
    """LLM 输出 -> (title, body)：剥围栏 -> JSON 对象 -> title/body 均非空串。

    坏 JSON/不是对象/字段缺失或空 = OpsGenError（「生成结果解析失败」，与
    LLM 故障分级不同，同属生成步失败，该步 failed 可重试）。
    """
    try:
        data: Any = json.loads(strip_code_fence(raw))
    except ValueError as exc:
        raise OpsGenError("生成结果解析失败：输出不是合法 JSON") from exc
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("title"), str)
        or not isinstance(data.get("body"), str)
        or not data["title"].strip()
        or not data["body"].strip()
    ):
        raise OpsGenError("生成结果解析失败：须为 {title, body} 非空字段 JSON 对象")
    return data["title"].strip(), data["body"].strip()


def spec_selling_points(product: Product) -> list[str]:
    """商品规格卖点行（compose 兜底正文的原料）：已写回值的「字段：值」。

    0038 修订补全（第 26 刀，审计刀 5 P1③）：这些行会落进 ops_runs（非中台
    表）的 output.body 兜底正文——落库前过 redact（coaching 落库先例：掩后
    值进非中台表，字节不动的中台侧不受影响）。redact 幂等，净值原样通过。
    """
    return [
        redact(f"{field}：{entry.get('value')}")
        for field, entry in dict(product.spec_values).items()
        if isinstance(entry, dict) and entry.get("value")
    ]


def read_product_detail(product: Product) -> str:
    """read_product 步 done 的 detail：商品名+类目+规格字段与写回值（未写回如实标）。

    0038 修订补全（第 26 刀 P1③）：detail 落 ops_runs.steps——落库前整串
    过 redact（同上口径）。
    """
    fields = dict(product.spec_schema)
    parts = [
        f"{field}：{(product.spec_values.get(field) or {}).get('value') or '未写回'}"
        for field in fields
    ]
    return redact(f"{product.name}（{product.category}）｜" + "，".join(parts))


def fallback_body(product: Product) -> str:
    """compose 无引用时的兜底正文：商品规格卖点组装 + 诚实披露（原型口径）。

    0038 修订补全（第 26 刀 P1③）：兜底正文落 ops_runs.output.body——标题行
    的商品名/类目过 redact；points 行在 spec_selling_points 已掩。
    """
    points = spec_selling_points(product)
    lines = [redact(f"{product.name}（{product.category}）")]
    lines.extend(points if points else ["（规格事实尚未写回，暂无可组装的治理口径）"])
    lines.append(
        f"{NO_REF_DETAIL}——在素材中心登记并发布素材（或拣选切片发布）后重新编排，正文会跟随素材。"
    )
    return "\n".join(lines)


def fetch_published_refs(db: Session, product_id: int) -> list[dict[str, int]]:
    """compose 的检索：该商品当前已发布的 material/video 资产 -> 冻结版本引用。

    已发布口径=当前指针非空（含修订中：线上仍在服务当前已发布版，同
    routes/assets list status=published 口径）；version_no 取指针版本行的
    版本号（0007：refs 冻结当时版本，指针前移不漂移）。只读，不碰检索索引。
    """
    rows = db.execute(
        select(Asset.id, AssetVersion.version_no)
        .join(AssetVersion, Asset.current_published_version_id == AssetVersion.id)
        .where(Asset.product_id == product_id, Asset.kind.in_(REF_KINDS))
        .order_by(Asset.id)
    ).all()
    return [{"asset_id": asset_id, "version_no": version_no} for asset_id, version_no in rows]


def _fail(step: dict[str, Any], reason: str) -> None:
    step["status"] = FAILED
    step["detail"] = reason[:500]


# ---------- 执行环（纯逻辑核心 + DB 装配） ----------

# executor：变更传入 step dict（status/detail）与 plan["output"]；抛 OpsGenError
# 以外不应抛——失败由 executor 自行 _fail 收口，环只负责落库节拍。
Executor = Callable[[dict[str, Any], dict[str, Any]], None]


def drive_steps(plan: dict[str, Any], executors: dict[str, Executor], commit: Callable[[], None]) -> None:
    """纯执行环：pending 步 running 先落库→执行→终态落库；failed 后断链。

    - done 步直接跳过（续跑语义：前序不重跑）；
    - executor 抛 OpsGenError = 该步 failed（detail 记原因）并停止后续步——
      后续步保持 pending；
    - 每次状态变更都经 commit 落库（running 前置 commit 保证 LLM 等待不持
      事务的纪律在 DB 装配层兑现，本环只按节拍回调）。
    """
    for step in plan["steps"]:
        if step["status"] == DONE:
            continue
        step["status"] = RUNNING
        step["detail"] = ""
        commit()
        try:
            executors[step["key"]](step, plan)
        except OpsGenError as exc:
            _fail(step, str(exc))
            commit()
            return
        if step["status"] == FAILED:
            commit()
            return
        step["status"] = DONE
        commit()


def _run_executors(db: Session, product: Product) -> dict[str, Executor]:
    """三步 executor 的 DB 装配：闭包捕获 product/db，写 plan 里的步与产出。"""

    def read_product(step: dict[str, Any], plan: dict[str, Any]) -> None:
        del plan
        step["detail"] = read_product_detail(product)

    def gen_material(step: dict[str, Any], plan: dict[str, Any]) -> None:
        try:
            raw = asyncio.run(
                llm.complete_chat(GEN_SYSTEM_PROMPT, build_generation_prompt(product))
            )
        except llm.LLMNotConfigured as exc:
            _fail(step, f"生成不可用：未配置 LLM_API_KEY，投放文案没有降级模板（可重试，{exc}）")
            return
        except llm.LLMError as exc:
            _fail(step, f"生成不可用：{exc}（可重试）")
            return
        title, body = parse_generated_output(raw)  # 坏输出抛 OpsGenError，环内收口 failed
        # 0038 修订：出口必掩（第 26 刀评审收尾件）——厂商草稿同样落 ops_runs
        # （非中台表）：title/body 落 output 前统一过 redact，与 detail/spec/
        # fallback 同口径；prompt 虽已掩，模型复述掩码或自发吐裸号都不留底。
        title, body = redact(title), redact(body)
        plan["output"] = {"title": title, "body": body, "refs": []}
        step["detail"] = f"厂商模型已生成草稿《{title}》（正文 {len(body)} 字）"

    def compose(step: dict[str, Any], plan: dict[str, Any]) -> None:
        refs = fetch_published_refs(db, product.id)
        # 兜底标题的商品名同过 redact（0038 出口必掩；防御分支，gen done 时不走）
        output = dict(
            plan["output"]
            or {"title": redact(f"投放文案 · {product.name}"), "body": "", "refs": []}
        )
        if refs:
            output["refs"] = refs
            step["detail"] = (
                f"引用 {len(refs)} 件已发布素材/切片，版本号冻结为 compose 时刻的当前指针"
            )
        else:
            output["refs"] = []
            output["body"] = fallback_body(product)
            step["detail"] = NO_REF_DETAIL
        plan["output"] = output

    return {STEP_READ: read_product, STEP_GEN: gen_material, STEP_COMPOSE: compose}


def _execute_run(db: Session, run: OpsRun, product: Product) -> OpsRun:
    """从当前轨迹续跑（done 跳过、pending 执行、failed 前置断链保持原样——
    retry 已把 failed 复位 pending），每步经 persist 把本地 plan 重赋回行。"""
    plan: dict[str, Any] = {
        "steps": [dict(step) for step in run.steps],
        "output": dict(run.output) if run.output is not None else None,
    }

    def persist() -> None:
        # JSONB 列整体重赋触发脏标记（同 _write_back_product 的 product.spec_values）
        run.steps = [dict(step) for step in plan["steps"]]
        run.output = dict(plan["output"]) if plan["output"] is not None else None
        db.commit()

    drive_steps(plan, _run_executors(db, product), persist)
    return run


def _run_or_raise(db: Session, run_id: int) -> OpsRun:
    """retry/deliver 的取行：`FOR UPDATE` 行锁（第 26 刀，审计刀 5 P1⑥）。

    裁决取行级排他锁（Owner 给的显式选项，未另造 re-fetch+条件 UPDATE）：
    Session.get 的 with_for_update=True 即渲染 SELECT ... FOR UPDATE——锁住
    「读轨迹→判状态→写终态前复位」的临界区，两个并发的 retry/deliver
    串行化——deliver 的双跑（重复渠道动作）就此关死；retry 的并发双复位
    关死（第二个进来时 failed 步已被复位，409「没有失败步骤」）。
    边界如实注记：复位 commit 即放锁（LLM 等待不持写事务的纪律优先，
    P1#2），执行中对「正在跑」轨迹的再 retry 仍是残存 running 恢复语义
    （0041 单操作者语境可接受，多操作者再收紧）。"""
    run = db.get(OpsRun, run_id, with_for_update=True)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="编排任务不存在")
    return run


def start_run(db: Session, product_id: int) -> OpsRun:
    """建 run（404 商品闸门）并**同步就地执行**三步：返回时已是稳定态。

    run 行 + steps[pending×3] 先 commit 落库（与执行分两笔，瞬时 running 态
    对并发读可见），随后 _execute_run 按步推进。pending 的 run 不出现在
    响应里（请求内完成），但落库节拍就是「轨迹可观察」的兑现。
    """
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="商品不存在")
    run = OpsRun(product_id=product.id, steps=initial_steps())
    db.add(run)
    db.commit()  # 轨迹骨架先落库（同 material「running 前置 commit」纪律的前半）
    return _execute_run(db, run, product)


def retry_run(db: Session, run_id: int) -> OpsRun:
    """从失败步续跑（0041）：failed/残存 running 复位 pending、清 detail，
    前序 done 不重跑；没有可续步=409。已投放 409（投放是终局确认，不再改轨迹）。

    复位先 commit 落库再进执行环——LLM 步的 running 前置 commit 纪律不变。
    """
    run = _run_or_raise(db, run_id)
    if run.delivered_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="已投放的编排任务不能重试"
        )
    steps = [dict(step) for step in run.steps]
    resumable = [step for step in steps if step["status"] in (FAILED, RUNNING)]
    if not resumable:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="没有失败的步骤，无需重试（重置轨迹请新建编排任务）",
        )
    for step in resumable:
        step["status"] = PENDING
        step["detail"] = ""
    run.steps = steps
    db.commit()  # 复位落库（含释放读取事务）后再执行
    product = db.get(Product, run.product_id)
    if product is None:  # pragma: no cover - product_id 有 FK，行存在由库保证
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="商品不存在")
    return _execute_run(db, run, product)


def deliver_run(db: Session, run_id: int) -> OpsRun:
    """投放确认（渠道动作，0041）：三步全 done 才可投放，记 delivered_at；
    已投放再投 409。不改任何资产三态——这里没有任何 Asset 写入。
    """
    run = _run_or_raise(db, run_id)
    if run.delivered_at is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该编排任务已投放")
    if any(step["status"] != DONE for step in run.steps):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="轨迹尚未全部完成（存在待执行/运行中/失败步骤），不能投放",
        )
    run.delivered_at = datetime.now(UTC)
    db.commit()
    db.refresh(run)
    return run
