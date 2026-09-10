"""第 42 刀：handoff_tickets 表（ADR 0046 转人工工单闭环）。

转人工工单=「这段对话要人接」的回执，不是中台对象（0012），也不是知识缺口
（0024/0046 §3：缺口是「知识待补」，工单是「顾客要人」，同一会话可并存、
不合并）。**没有分派/坐席/SLA/优先级/附件**——只有 pending/resolved 两态。

一个会话一张工单：session_id 唯一索引；该会话第一次 handoff 事件创建，
后续复用（并发由唯一索引 + 应用层 SAVEPOINT 兜底）。message_id 指向触发它
的第一条 agent 消息（顾客联系方式表单挂在它下面，nullable）。工单号
H-{id:04d} 由 PK 派生（不落列，serial 天然唯一）。联系方式落库存原文
（0038「字节不动」），操作者面出口掩电话/邮箱。

down 直接删表——工单不是中台对象，无数据迁移负担（同 0009 material_tasks）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "handoff_tickets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "session_id", sa.Integer(), sa.ForeignKey("service_sessions.id"), nullable=False
        ),
        sa.Column("message_id", sa.Integer(), sa.ForeignKey("service_messages.id"), nullable=True),
        sa.Column(
            "status", sa.String(16), server_default=sa.text("'pending'"), nullable=False
        ),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("email", sa.String(200), nullable=True),
        sa.Column("phone", sa.String(50), nullable=True),
        sa.Column("contact_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_handoff_tickets_status", "handoff_tickets", ["status"])
    # 一会话一单（ADR 0046）：唯一索引兜并发创建
    op.create_index(
        "uq_handoff_tickets_session_id", "handoff_tickets", ["session_id"], unique=True
    )


def downgrade() -> None:
    op.drop_index("uq_handoff_tickets_session_id", table_name="handoff_tickets")
    op.drop_index("ix_handoff_tickets_status", table_name="handoff_tickets")
    op.drop_table("handoff_tickets")
