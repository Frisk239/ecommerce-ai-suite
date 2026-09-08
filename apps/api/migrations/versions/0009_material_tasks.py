"""第九批：material_tasks 表（ADR 0038 素材任务生命周期）

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-08

素材中心自有表，不是中台对象（0012）：状态 queued/running/pending_qc/
registered/failed（0038）。title/content 是生成文案本体（抽检通过前只住
本行，失败不进中台=不登记任何字节，0029）；asset_id 只在 registered 后
指向登记出的素材资产。down 直接删表——任务不是中台对象，无数据迁移负担。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "material_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("title", sa.String(200), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("last_error", sa.String(500), nullable=True),
        sa.Column("asset_id", sa.Integer(), sa.ForeignKey("assets.id"), nullable=True),
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
    op.create_index("ix_material_tasks_status", "material_tasks", ["status"])


def downgrade() -> None:
    op.drop_index("ix_material_tasks_status", table_name="material_tasks")
    op.drop_table("material_tasks")
