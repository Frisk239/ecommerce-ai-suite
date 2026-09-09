"""第 40 刀：service_messages.feedback（顾客 thumbs-down，ADR 0044 §四）。

一列落点（v1 只收「没有帮助」）：nullable JSONB——NULL=未反馈；非空即
``{"helpful": false, "at": iso}``（helpful 键留位，thumbs-up 是本刀 Out）。
幂等在应用层：feedback 非 NULL 的消息再收反馈即 409（一条消息至多一次）。

分诊规则在代码（routes/customer.leave_feedback）：kind=answer 且 citations
非空的消息收到负反馈 -> 逐 citation 资产 assets.last_verified_at=NULL（撤销
验证，「发布=验证快照」被负反馈推翻；复审队列=治理台未验证/stale 面自然承
接，Guru 式 unverify 的最小版）。本迁移只加列，不动任何行。

downgrade：删列即回滚（无索引、无附加对象要清）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "service_messages",
        sa.Column("feedback", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("service_messages", "feedback")
