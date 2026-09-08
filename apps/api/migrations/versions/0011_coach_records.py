"""第十一批：coach_records 表（ADR 0040 销售考核记录）

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-08

考核模块自有表，不是中台对象（0027 考核不是资产）：无检索/发布/MCP 触点。
question_key 是题源锚 {asset_id, version_no, source: qa|transcript, pair_index}
（0007：抽的是当时的已发布版本），题目不落库、由 services/coaching 动态推导，
本表只存作答时刻的题面/标准答案快照与三维分。score NULL=未评分（LLM 未配置/
失败/坏输出，无降级），last_error 记原因、可重评；model_name 是打分时刻的
底座名快照。down 直接删表——记录不是中台对象，无数据迁移负担。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "coach_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("operator_name", sa.String(120), nullable=False),
        sa.Column("question_key", postgresql.JSONB(), nullable=False),
        sa.Column("question_text", sa.Text(), nullable=False),
        sa.Column("standard_answer", sa.Text(), nullable=True),
        sa.Column("trainee_answer", sa.Text(), nullable=False),
        sa.Column("score", postgresql.JSONB(), nullable=True),
        sa.Column("model_name", sa.String(120), nullable=True),
        sa.Column("last_error", sa.String(500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("coach_records")
