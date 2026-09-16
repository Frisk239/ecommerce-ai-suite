"""第 98b 刀：compose_tasks 内容成片任务表（ADR 0056）。

成片模块自有的候选留档（不是中台对象，0012 同口径）：plan 同步产出时间线
候选+预览成片+剪映草稿（字节住对象存储 ``compose/`` 暂存前缀），publish
人闸门确认后经双闸复用登记 material 资产转 registered。与 material_tasks
（0038）同型：任务面只记运行状态与暂存键，登记触点在 register_asset。

downgrade：删表即回滚（compose/ 暂存字节由应用层管理，迁移不碰对象存储）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "compose_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "product_id",
            sa.Integer(),
            sa.ForeignKey("products.id"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("template", sa.String(length=20), nullable=False),
        sa.Column(
            "timeline",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("duration_seconds", sa.Float(), nullable=False),
        sa.Column(
            "with_tts", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("preview_object_key", sa.String(length=500), nullable=False),
        sa.Column("draft_object_key", sa.String(length=500), nullable=False),
        sa.Column("final_video_object_key", sa.String(length=500), nullable=True),
        sa.Column(
            "asset_id",
            sa.Integer(),
            sa.ForeignKey("assets.id"),
            nullable=True,
        ),
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
    op.create_index("ix_compose_tasks_status", "compose_tasks", ["status"])


def downgrade() -> None:
    op.drop_index("ix_compose_tasks_status", table_name="compose_tasks")
    op.drop_table("compose_tasks")
