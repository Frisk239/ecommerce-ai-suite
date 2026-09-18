"""第 121 刀 A（AI 客户对练）：coach_roleplays 表。

多轮对练会话（AI 顾客 persona + turns JSONB + 整段 transcript 评分）。down 删表。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0036"
down_revision: str | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "coach_roleplays",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("operator_name", sa.String(length=120), nullable=False),
        sa.Column("question_key", JSONB(), nullable=False),
        sa.Column("question_text", sa.Text(), nullable=False),
        sa.Column("standard_answer", sa.Text(), nullable=True),
        sa.Column("persona", JSONB(), nullable=False),
        sa.Column("turns", JSONB(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="active", nullable=False),
        sa.Column("score", JSONB(), nullable=True),
        sa.Column("model_name", sa.String(length=200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("coach_roleplays")
