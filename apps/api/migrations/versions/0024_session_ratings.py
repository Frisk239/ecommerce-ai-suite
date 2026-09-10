"""第 48 刀：session_ratings 表（顾客 CSAT）。

一会话一评（`session_id` 唯一约束）：CSAT 是会话级口径，与消息级 thumbs
（`service_messages.feedback`）正交。score 1–5（合法值集与校验在服务层，
不建 CHECK——沿用「legal 值集单一来源」先例）；comment 是顾客手打自由文本，
**库内原文、出口必掩**（ADR 0038）。

down 只删表：评分是顾客行为事实，降级即丢弃（与 0020/0023 的「非中台对象
无数据迁移负担」同口径）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "session_ratings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "session_id",
            sa.Integer(),
            sa.ForeignKey("service_sessions.id"),
            nullable=False,
        ),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("session_id", name="uq_session_ratings_session_id"),
    )


def downgrade() -> None:
    op.drop_table("session_ratings")
