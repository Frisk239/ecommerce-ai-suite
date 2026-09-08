"""销售考核路由（第 19 刀，ADR 0040）：题库推导、作答打分、记录回放与重评。

四端点全操作者 cookie 鉴权（考核是操作者动作，0016 控制台=登录后的人机界面）；
路由恒为**同步 def**——作答/重评在请求内同步跑 LLM 打分（`asyncio.run` ≤20s，
前提同素材生成，第 16 刀 P1#1）。打分三态收口在服务层：LLM 未配置/失败/坏
输出**不抛**，200 返回带 status=unscored 的记录视图（前端给「重新评分」）。

单操作者 v1 自兼受训者与考官（0040 裁决）：GET /questions 连 standard_answer
一并下发——公开评分参照是诚实口径。记录不是中台对象（0027）：这里没有资产
三态语义，题源锚 `A-xxxx · vN` 只是引用（链治理台详情），考核不写中台任何表。
"""

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from suite_api.deps import get_current_operator, get_db, get_storage
from suite_api.models import CoachRecord, Operator
from suite_api.services.coaching import derive_questions, rescore_record, score_attempt
from suite_platform.storage import ObjectStorage

router = APIRouter(prefix="/api/coach", tags=["coach"])


class QuestionKeyOut(BaseModel):
    """题源锚（0007 引用口径：题面=该资产该版本里的内容，回放不随修订漂移）。"""

    asset_id: int
    version_no: int
    source: str  # qa | transcript
    pair_index: int | None  # transcript 兜底题为 None


class CoachQuestionOut(BaseModel):
    key: QuestionKeyOut
    question: str
    standard_answer: str | None
    asset_title: str | None


class AttemptIn(BaseModel):
    question_key: dict[str, Any]
    answer: str


class CoachRecordOut(BaseModel):
    id: int
    operator_name: str
    question_key: QuestionKeyOut
    question_text: str
    standard_answer: str | None
    trainee_answer: str
    score: dict[str, Any] | None
    model_name: str | None
    last_error: str | None
    status: str  # scored | unscored（由 score 是否为 NULL 派生的响应态）
    created_at: datetime


def _to_out(record: CoachRecord) -> CoachRecordOut:
    return CoachRecordOut(
        id=record.id,
        operator_name=record.operator_name,
        question_key=record.question_key,
        question_text=record.question_text,
        standard_answer=record.standard_answer,
        trainee_answer=record.trainee_answer,
        score=record.score,
        model_name=record.model_name,
        last_error=record.last_error,
        status="scored" if record.score is not None else "unscored",
        created_at=record.created_at,
    )


@router.get("/questions", response_model=list[CoachQuestionOut])
def list_questions(
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
    storage: Annotated[ObjectStorage, Depends(get_storage)] = None,
) -> list[CoachQuestionOut]:
    """题库（动态推导，不落库）：已发布对话的 confirmed QA 对逐题 + 兜底首问。"""
    del operator  # 读接口同样要求登录
    return [CoachQuestionOut(**q) for q in derive_questions(db, storage)]


@router.post("/attempts", response_model=CoachRecordOut)
def create_attempt(
    body: AttemptIn,
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
    storage: Annotated[ObjectStorage, Depends(get_storage)] = None,
) -> CoachRecordOut:
    """单轮作答（0040）：题锚找题（404）-> 落记录 -> 同步 LLM 打分（失败=未评分
    行不抛）。空答案 422。"""
    # 写接口仅要求登录，401 口径同既有写端点；受训者=操作者名（单操作者 v1）
    answer = body.answer.strip()
    if not answer:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="答案不能为空")
    record = score_attempt(db, storage, operator.username, body.question_key, answer)
    return _to_out(record)


@router.get("/records", response_model=list[CoachRecordOut])
def list_records(
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> list[CoachRecordOut]:
    """考核记录回放列表（id 倒序，append-only）。"""
    del operator
    records = list(db.scalars(select(CoachRecord).order_by(CoachRecord.id.desc())))
    return [_to_out(r) for r in records]


@router.post("/records/{record_id}/rescore", response_model=CoachRecordOut)
def rescore(
    record_id: int,
    operator: Annotated[Operator, Depends(get_current_operator)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> CoachRecordOut:
    """未评分记录重跑打分（空 key 环境配好底座后就地点一下即可）；已评分 409。"""
    del operator
    return _to_out(rescore_record(db, record_id))
