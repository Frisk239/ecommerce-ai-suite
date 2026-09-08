"""第三批：知识缺口表 + 资产来源列（ADR 0030）

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-06

领域映射（ADR 0030 唯一依据，逐列照做）：
- knowledge_gaps：id/question（顾客原问）/product_id 可空/status（open|resolved，
  默认 open）/resolved_by_asset_id 可空/resolved_at 可空/created_at；索引
  (status, created_at)。缺口不是资产：无检索/发布路径；精确幂等（同 question
  文本且 open 不新建）由应用层 SELECT 保证——0024 未锁去重策略，工程裁决防
  重复拒答灌水，不建唯一约束。
- assets.source_kind：NOT NULL 枚列 upload/session_backflow/clip_pick/
  material_generated/mcp_registered/seed（0025），由登记端点语义定值，不让
  调用方填报；存量回填 'upload'（回填后摘掉 server_default，逼新行显式给值）。
  枚举校验在应用层（services/registration），与 status/kind 同口径，不加 CHECK。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "knowledge_gaps",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="open"),
        sa.Column("resolved_by_asset_id", sa.Integer(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.ForeignKeyConstraint(["resolved_by_asset_id"], ["assets.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_knowledge_gaps_status_created_at", "knowledge_gaps", ["status", "created_at"]
    )
    # 存量回填：带 server_default 加 NOT NULL 列即回填旧行 'upload'，随后摘掉默认
    op.add_column(
        "assets",
        sa.Column("source_kind", sa.String(length=20), nullable=False, server_default="upload"),
    )
    op.alter_column("assets", "source_kind", server_default=None)


def downgrade() -> None:
    op.drop_column("assets", "source_kind")
    op.drop_index("ix_knowledge_gaps_status_created_at", table_name="knowledge_gaps")
    op.drop_table("knowledge_gaps")
