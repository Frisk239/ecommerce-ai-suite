"""第十二批：ops_runs 表（ADR 0041 运营 Agent 编排轨迹）

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-08

运营模块自有表，不是中台对象（0041）：不能被检索、不能发布、不进治理台。
steps JSONB = 三步轨迹 [{key,name,status,detail,via}]（pending|running|done|
failed，同步就地执行、逐步落库可观察）；output JSONB = 投放文案
{title, body, refs:[{asset_id,version_no}]}，refs 冻结 compose 时刻的当前已
发布指针版本（0007 演示诚实度口径，指针前移不漂移）。delivered_at 非空=已
投放（渠道动作，不改任何资产三态；词条「发布」_Avoid_「投放发布」）。
down 直接删表——run 不是中台对象，无数据迁移负担。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ops_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column(
            "steps",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("output", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("ops_runs")
