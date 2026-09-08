"""销售考核服务（第 19 刀，ADR 0040）：题库动态推导 + LLM 三维打分，无降级。

- **题库不落库**：题目=当前已发布（指针非空）dialogue 资产的 confirmed
  ``qa_pairs`` 逐对展开（顾客问=题面，客服答=评分参照），每对一题、按当前
  已发布版本取（0007 引用口径）；qa_pairs 弃权/空/缺项时兜底取转写首个
  「顾客：」行（standard_answer=None，评分只看 rubric 另两维与口径常识）。
  每题锚 ``{asset_id, version_no, source: qa|transcript, pair_index}``。
- **打分=LLM rubric，无降级**（0040：打分是考核的本体，同素材生成「失败
  不进中台」纪律）：三维口径沿用原型 RUBRIC——口径准确 40 / 证据贴合 30 /
  服务语气 30，LLM 输出 JSON ``{accurate, evidence, tone, comment}``。
  LLMNotConfigured/LLMError/坏输出=该次 attempt 落「未评分」行
  （score=NULL、last_error 记原因），**不向调用方抛**——前端拿 200 +
  unscored 状态给「重新评分」按钮；rescore 从记录字段重组同题 prompt 重跑。
- **记录不是中台对象**（0027）：只写 coach_records，不碰检索/发布/审计；
  成功打分时快照 model_name=评分时刻 settings.llm_model（回放显示扮演底座）。
- asyncio.run 前提：消费方路由恒为同步 def（FastAPI 线程池，线程上无运行中
  事件循环），与 machine_wash QA 抽取、material 生成同一先例（第 16 刀 P1#1
  的按 loop 缓存客户端也依赖这一点）。
- 事务边界（debt-1「LLM 等待不 idle-in-transaction」）：作答行先以未评分态
  commit 落库、重评读行后先 commit 结束只读事务，之后才进 LLM ≤20s 等待，
  结果由第二段事务 UPDATE 收口——先例 register_asset 机洗前 commit、material
  running 先落库。
"""

import asyncio
import json
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from suite_api.models import Asset, AssetVersion, CoachRecord
from suite_api.services import llm
from suite_api.services.asset_view import VersionTextError, read_version_text
from suite_api.services.machine_wash import QA_FIELD, redact, strip_code_fence
from suite_api.settings import get_settings
from suite_platform.storage import ObjectStorage

# 题源两种（0040）：qa=confirmed 问答对逐对成题；transcript=转写首问兜底
SOURCE_QA = "qa"
SOURCE_TRANSCRIPT = "transcript"

# 三维 rubric 满分（原型 RUBRIC 冻结口径，0040）：键=LLM 输出 JSON 字段名
RUBRIC_MAX: dict[str, int] = {"accurate": 40, "evidence": 30, "tone": 30}

SCORING_SYSTEM_PROMPT = (
    "你是电商客服销售考核的评分官，对受训销售的单轮作答按三维 rubric 打分。\n"
    "维度与满分：口径准确 40 分（回答口径与标准答案/事实一致，不编造承诺）；"
    "证据贴合 30 分（扣住题面与标准答案中的证据，不跑题不空泛）；"
    "服务语气 30 分（礼貌专业、站在顾客视角，有成交推进但不压迫）。\n"
    "标准答案为空时这是转写兜底题：只看题面与服务语气、口径常识打分，"
    "不因缺少标准答案在证据维度扣分。\n"
    '只输出一个 JSON 对象，形如 {"accurate": 0-40, "evidence": 0-30, '
    '"tone": 0-30, "comment": "一句话评语"}；三个分数是区间内整数，'
    "不要输出 JSON 以外的任何解释文字。"
)


class ScoreParseError(Exception):
    """评分输出解析失败（坏 JSON/形状不对/超区间）：该次 attempt 落未评分行。"""


def build_score_prompt(
    question_text: str, standard_answer: str | None, trainee_answer: str
) -> str:
    """user prompt 三要素（0040）：题面 + 标准答案（可空，写明兜底口径）+ 受训者答案。

    0038 修订（第 21 刀，审计刀 4 P0 簇出口 3）：字节不动、出口必掩——打分
    prompt 三输入在进厂商前统一过 redact（本函数是 score/rescore 两条调用链
    的唯一 prompt 漏斗，收口于此即两条出口同掩）。受训者手输的答案、历史
    脏数据里未掩的题面/参照快照（出口 2 收口前落的 confirmed 原文）都在此
    兜住；redact 幂等，已掩值重复掩无副作用。"""
    lines = [f"题面（顾客问）：{redact(question_text)}"]
    if standard_answer:
        lines.append(f"标准答案（评分参照）：{redact(standard_answer)}")
    else:
        lines.append("标准答案（评分参照）：（无——转写兜底题，按 rubric 口径常识打分）")
    lines.append(f"受训者作答：{redact(trainee_answer)}")
    return "\n".join(lines)


def parse_score_output(raw: str) -> dict[str, Any]:
    """LLM 输出 -> {accurate, evidence, tone, comment}：剥围栏 -> JSON 对象 ->
    三维为区间内整数（bool 不算整数）、comment 非空串。坏输出抛 ScoreParseError
    （调用方落未评分行，不降级不抛给前端）。"""
    try:
        data: Any = json.loads(strip_code_fence(raw))
    except ValueError as exc:
        raise ScoreParseError("评分输出不是合法 JSON") from exc
    if not isinstance(data, dict):
        raise ScoreParseError("评分输出须为 JSON 对象")
    result: dict[str, Any] = {}
    for dim, cap in RUBRIC_MAX.items():
        value = data.get(dim)
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= cap:
            raise ScoreParseError(f"{dim} 须为 0-{cap} 的整数")
        result[dim] = value
    comment = data.get("comment")
    if not isinstance(comment, str) or not comment.strip():
        raise ScoreParseError("comment 须为非空字符串")
    result["comment"] = comment.strip()
    return result


def first_customer_question(transcript: str) -> str | None:
    """转写首个非空「顾客：」行的问句（兜底题面）；没有则 None（该资产不成题）。

    0038 修订（第 21 刀，审计刀 4 P0 簇出口 3）：字节不动、出口必掩——转写
    兜底题面直接读自版本字节（未掩区），在推导出口处过 redact 再外发：题库
    列表、落库 question_text、打分 prompt 三处消费者从此拿到的都是掩码值
    （qa 题面的 confirmed 值已由出口 2 在写回点收口，旧脏行由
    build_score_prompt/score_attempt 兜住）。"""
    for line in transcript.splitlines():
        line = line.strip()
        if line.startswith("顾客："):
            question = line[len("顾客：") :].strip()
            if question:
                return redact(question)
    return None


def _question(
    asset: Asset,
    version: AssetVersion,
    source: str,
    pair_index: int | None,
    question_text: str,
    standard_answer: str | None,
) -> dict[str, Any]:
    return {
        "key": {
            "asset_id": asset.id,
            "version_no": version.version_no,
            "source": source,
            "pair_index": pair_index,
        },
        "question": question_text,
        "standard_answer": standard_answer,
        "asset_title": asset.title,
    }


def asset_questions(
    db: Session, asset: Asset, version: AssetVersion, storage: ObjectStorage
) -> list[dict[str, Any]]:
    """单资产出题：confirmed qa_pairs 有 value 逐对成题；弃权/空/坏形状→转写首问兜底。

    只认 confirmed（0010/0035：机洗草稿未经人洗不算治理过的口径，不考）。
    """
    entry = dict(version.confirmed_fields).get(QA_FIELD)
    value = entry.get("value") if isinstance(entry, dict) else None
    if isinstance(value, list):
        questions = []
        for index, pair in enumerate(value):
            if (
                isinstance(pair, dict)
                and isinstance(pair.get("q"), str)
                and pair["q"].strip()
            ):
                answer = pair.get("a")
                standard = answer.strip() if isinstance(answer, str) and answer.strip() else None
                questions.append(
                    _question(asset, version, SOURCE_QA, index, pair["q"].strip(), standard)
                )
        if questions:
            return questions
    try:
        transcript = read_version_text(db, storage, version)
    except VersionTextError:
        return []  # 存储异常（对象缺失/非 UTF-8）：读路径不炸题库，该资产暂无题
    question = first_customer_question(transcript)
    if question is None:
        return []
    return [_question(asset, version, SOURCE_TRANSCRIPT, None, question, None)]


def derive_questions(db: Session, storage: ObjectStorage) -> list[dict[str, Any]]:
    """题库推导（只读，不落库）：已发布 dialogue 逐资产展开，按资产 id 升序。

    已发布口径=指针非空（含修订中：考的是线上正在服务的版本，非修订草稿）。
    """
    assets = list(
        db.scalars(
            select(Asset)
            .where(Asset.kind == "dialogue", Asset.current_published_version_id.is_not(None))
            .order_by(Asset.id)
        )
    )
    questions: list[dict[str, Any]] = []
    for asset in assets:
        assert asset.current_published_version_id is not None  # where 已滤
        version = db.get(AssetVersion, asset.current_published_version_id)
        if version is None:  # pragma: no cover - 指针漂移属数据异常，防御不 500
            continue
        questions.extend(asset_questions(db, asset, version, storage))
    return questions


def normalize_key(raw: Any) -> dict[str, Any] | None:
    """题源锚入参归一（非 dict/缺字段/枚举外/类型错 -> None -> 404）。

    transcript 题 pair_index 恒 None；qa 题必须给整数 pair_index。
    """
    if not isinstance(raw, dict):
        return None
    try:
        asset_id = int(raw["asset_id"])
        version_no = int(raw["version_no"])
    except (KeyError, TypeError, ValueError):
        return None
    source = raw.get("source")
    if source not in (SOURCE_QA, SOURCE_TRANSCRIPT):
        return None
    pair_index = raw.get("pair_index")
    if pair_index is not None:
        try:
            pair_index = int(pair_index)
        except (TypeError, ValueError):
            return None
    if source == SOURCE_QA and pair_index is None:
        return None
    return {
        "asset_id": asset_id,
        "version_no": version_no,
        "source": source,
        "pair_index": pair_index,
    }


def find_question(
    db: Session, storage: ObjectStorage, question_key: Any
) -> dict[str, Any]:
    """按锚在题库里找题；找不到=404（题随修订/取消发布消失是常态，如实报）。"""
    key = normalize_key(question_key)
    if key is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="题目不存在")
    for question in derive_questions(db, storage):
        if question["key"] == key:
            return question
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="题目不存在（可能已开修订或取消发布，题库按当前已发布版本推导）",
    )


def _apply_score(
    record: CoachRecord, question_text: str, standard_answer: str | None, trainee_answer: str
) -> None:
    """对记录跑一次 LLM 打分并就地写字段（成功：score+model_name、清原因；
    失败/未配置/坏输出：score 保持 NULL、last_error 写原因）。不抛。

    prompt 三输入走**入参**而不回读 record 属性：记录行先落库后属性即过期，
    读它会重新打开事务——LLM ≤20s 就挂成 idle-in-transaction（debt-1 纪律）。"""
    score, reason = try_score(question_text, standard_answer, trainee_answer)
    if score is not None:
        record.score = score
        record.model_name = get_settings().llm_model
        record.last_error = None
    else:
        record.score = None
        record.model_name = None
        record.last_error = reason


def try_score(
    question_text: str, standard_answer: str | None, trainee_answer: str
) -> tuple[dict[str, Any] | None, str | None]:
    """一次打分调用，返回 (score, 未评分原因)——恰一个非 None（三态收口）。"""
    prompt = build_score_prompt(question_text, standard_answer, trainee_answer)
    try:
        raw = asyncio.run(llm.complete_chat(SCORING_SYSTEM_PROMPT, prompt))
    except llm.LLMNotConfigured:
        return None, "未评分：未配置 LLM_API_KEY，打分没有降级模板（可重评）"
    except llm.LLMError as exc:
        return None, f"未评分：{exc}"
    try:
        return parse_score_output(raw), None
    except ScoreParseError as exc:
        return None, f"未评分：{exc}"


def score_attempt(
    db: Session,
    storage: ObjectStorage,
    operator_name: str,
    question_key: Any,
    answer: str,
) -> CoachRecord:
    """作答落记录 + 同步打分（0038 同款就地执行，LLM ≤20s）：返回的记录恒落库，
    打分失败只体现在 score=NULL + last_error（不向调用方抛，HTTP 200 带未评分态）。

    两段事务（debt-1「LLM 等待不 idle-in-transaction」，先例 register_asset 机洗
    前 commit、material running 先落库）：推导+作答行以「未评分」态先 commit
    （也终结 find_question 的只读事务）→ LLM 等待期间会话不持事务 → 打分结果
    UPDATE 收口。进程崩在 LLM 等待中留未评分行——本就语义合法、可重评。"""
    question = find_question(db, storage, question_key)
    # 0038 修订（第 21 刀，审计刀 4 P0 簇出口 3）：字节不动、出口必掩——
    # coach_records 是非中台表，三文本字段落库前过 redact（受训者手输答案
    # 是本字段裸 PII 的主要来源；题面/参照新链路已在推导与出口 2 收掩，
    # 这里统一兜历史脏值）。_apply_score 同用掩后值（prompt 侧
    # build_score_prompt 还有一道幂等掩，双保险）。
    question_text = redact(question["question"])
    standard_answer = (
        redact(question["standard_answer"]) if question["standard_answer"] is not None else None
    )
    trainee_answer = redact(answer)
    record = CoachRecord(
        operator_name=operator_name,
        question_key=question["key"],
        question_text=question_text,
        standard_answer=standard_answer,
        trainee_answer=trainee_answer,
    )
    db.add(record)
    db.commit()  # ① 未评分态先落库 + 释放推导事务，再等 LLM
    _apply_score(record, question_text, standard_answer, trainee_answer)  # 三输入走本地变量，不回读过期属性（不重开事务）
    db.commit()  # ② 打分结果 UPDATE 收口（新事务，毫秒级）
    db.refresh(record)
    return record


def rescore_record(db: Session, record_id: int) -> CoachRecord:
    """未评分记录重跑打分：同 prompt 从记录字段组装（题面/标准答案快照已在行上，
    不依赖题目仍在题库——已发布版本被修订也照评）。已评分 409（分数不可覆盖）。

    debt-1 事务边界：读行闸门后把 prompt 三输入取进本地变量并 commit 结束只读
    事务（行本就以未评分态落库，无需重写一次 INSERT）→ LLM 等待不持事务 →
    结果 UPDATE 收口。"""
    record = db.get(CoachRecord, record_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="考核记录不存在")
    if record.score is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="已评分的记录不能重新评分")
    question_text = record.question_text
    standard_answer = record.standard_answer
    trainee_answer = record.trainee_answer
    db.commit()  # ① 结束读取事务（属性已过期的行不再被 LLM 等待期触碰）
    _apply_score(record, question_text, standard_answer, trainee_answer)
    db.commit()  # ② 重评结果 UPDATE 收口
    db.refresh(record)
    return record
