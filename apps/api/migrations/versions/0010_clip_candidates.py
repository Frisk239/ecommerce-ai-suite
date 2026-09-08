"""第十批：clip_candidates 表（ADR 0014/0039 直播切片候选）

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-08

切片模块自有表，不是中台对象（0014：候选不是资产，拣选才登记）：状态
pending/registered 单向（0039：已登记不可再拣选）。transcript 是 ASR 转写
mock，登记字节=「[start-end] 转写」文本（kind=video，非 mp4）；
source_video_label 只存源录像名称字符串，不存录像字节。
registered_asset_id 只在 registered 后指向登记出的视频资产（回执锚，同
material_tasks.asset_id 先例）。down 直接删表——候选不是中台对象，无数据
迁移负担。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "clip_candidates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("timecode_start", sa.String(8), nullable=False),
        sa.Column("timecode_end", sa.String(8), nullable=False),
        sa.Column("transcript", sa.Text(), nullable=False),
        sa.Column("source_video_label", sa.String(120), nullable=False),
        sa.Column("registered_asset_id", sa.Integer(), sa.ForeignKey("assets.id"), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_clip_candidates_status", "clip_candidates", ["status"])


def downgrade() -> None:
    op.drop_index("ix_clip_candidates_status", table_name="clip_candidates")
    op.drop_table("clip_candidates")
