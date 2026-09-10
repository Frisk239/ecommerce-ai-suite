"""转人工工单共享服务（第 42 刀，ADR 0046）：建/取会话工单、提交联系方式、结单。

边界（0046 §3，勿漂移）：**工单 ≠ 缺口**。缺口是「知识待补」（发布文档即
关闭，机制在治理台）；工单是「顾客要人」（人回复后置 resolved）。同一会话可能
两者都有——那正是两个事实都成立。**没有坐席队列/分派/SLA 计时器/优先级/
附件**：工单是「有回执的待办」。回执话术里的「工作时间 4 小时内回复」是**承诺
话术，不是外发能力**（不真发短信/邮件）——不得让它看起来像已有的通知能力。

意图词表（0046 §2，单一来源）：``转人工|找人工|要人工|人工客服|真人|投诉|
举报``——**不含裸「人工」**（会误伤「人工智能」）。catalog_tools 的目录回落
真人闸同引本模式，避免两处各写一份漂移。

PII（0038/0046 §6）：联系方式落库存原文（字节不动），操作者面出口一律过
machine_wash.redact_contact（电话/邮箱/留言；姓名不掩）；顾客回显自己的输入
不掩。
"""

import re
from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from suite_api.models import HandoffTicket, ServiceMessage, ServiceSession

PENDING = "pending"
RESOLVED = "resolved"

# 显式要真人/投诉/举报的词表（0046 裁决 4）——不含裸「人工」
HUMAN_REQUEST_RE = re.compile("转人工|找人工|要人工|人工客服|真人|投诉|举报")


def wants_human(question: str) -> bool:
    """纯函数（无 IO，便于单测）：显式要真人/投诉/举报。

    快路径分派最前置（优先于订单/库存工具）：显式要人优先于一切工具。
    """
    return bool(HUMAN_REQUEST_RE.search(question))


def ticket_no(ticket: HandoffTicket) -> str:
    """工单号 ``H-{id:04d}``：由 PK 派生（不落列、不建序列表），纯函数便于单测。"""
    return f"H-{ticket.id:04d}"


def render_handoff_receipt(no: str) -> str:
    """显式要人时的回执话术（0046 §4）：号码 + 时间窗 + 留联系方式邀请。

    时间窗是承诺话术不是外发能力（本刀不真发短信/邮件，ADR 0046 后果段）。
    拒答路径**不用**本函数：拒答消息文本一个字符都不改。
    """
    return f"已记录工单 {no}，工作时间 4 小时内回复。\n可在下面留下联系方式，方便我们联系你。"


def ensure_session_ticket(
    db: Session, session: ServiceSession, *, message: ServiceMessage | None = None
) -> HandoffTicket:
    """建/取本会话工单（0046 裁决 1：一会话一单，幂等）。

    应用层 check-then-insert；并发窗口由 ``uq_handoff_tickets_session_id`` 唯一
    索引兜底——IntegrityError 时 SAVEPOINT 回滚插入，再查已有行返回（与
    knowledge_gaps.record_refusal_gap 同款）。**不可 db.rollback()**：会把同事务
    尚未提交的 agent 消息一并丢掉（拒答消息与工单必须同事务原子）。

    插后 ``flush`` 拿 PK（工单号由 PK 派生，显式路径要在建消息前拿到号码）。
    ``message`` 是触发它的第一条 agent 消息——仅新建时写（幂等复用不覆盖锚点：
    溯源是可核对的历史，不是「最近一次」）。
    """
    ticket = db.scalar(select(HandoffTicket).where(HandoffTicket.session_id == session.id))
    if ticket is not None:
        return ticket
    ticket = HandoffTicket(session_id=session.id, status=PENDING)
    try:
        with db.begin_nested():
            db.add(ticket)
            db.flush()  # 顺带 flush 同事务里已 add 的 agent 消息（PK 归位）
    except IntegrityError:
        # SAVEPOINT 已回滚插入；未 expunge 则后续 SELECT 的 autoflush 会再插一次
        if ticket in db:
            db.expunge(ticket)
        ticket = db.scalar(select(HandoffTicket).where(HandoffTicket.session_id == session.id))
        if ticket is None:
            raise
        return ticket
    if message is not None and message.id is not None:
        ticket.message_id = message.id
    return ticket


def submit_contact(
    db: Session,
    ticket: HandoffTicket,
    *,
    name: str,
    note: str,
    email: str | None,
    phone: str | None,
) -> HandoffTicket:
    """顾客提交联系方式：填 name/note + email/phone（可选）+ contact_at=now。

    取舍：已 resolved 409（工单已结，回访面收口——与 resolve 的非法转移同口径）；
    **已填过允许覆盖并刷新 contact_at**（顾客改主意/补电话是正常回访诉求，覆盖比
    「一次性」更贴近目的；name/note 同字段覆盖不留历史——工单不是审计对象）。
    name/note 非空与 email/phone 格式由路由 422 保证（本层只落库）。
    """
    if ticket.status == RESOLVED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"工单 {ticket_no(ticket)} 已结单，不能再提交联系方式",
        )
    ticket.name = name
    ticket.note = note
    ticket.email = email
    ticket.phone = phone
    ticket.contact_at = datetime.now(UTC)
    db.commit()
    db.refresh(ticket)
    return ticket


def resolve_ticket(
    db: Session, ticket: HandoffTicket, *, resolved_at: datetime
) -> HandoffTicket:
    """结单（pending -> resolved）：置 status + resolved_at。

    已是 resolved 抛 409（非法转移，照 routes/material 状态机口径）。没有
    「反结单」——结单是操作者回复后的终态，再次点即冲突（幂等表达靠 409）。
    """
    if ticket.status == RESOLVED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"工单 {ticket_no(ticket)} 已结单",
        )
    ticket.status = RESOLVED
    ticket.resolved_at = resolved_at
    db.commit()
    db.refresh(ticket)
    return ticket
