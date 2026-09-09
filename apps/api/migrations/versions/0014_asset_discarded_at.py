"""第十四批：assets.discarded_at（ADR 0042 废弃失败资产）

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-09

废弃（discard）不是资产第四态（CONTEXT「资产状态」词条）：不进三态枚举、
不改 status 列——discarded_at 非空=治理动作后的隐藏标记（工程终态），
列表默认过滤 NULL。仅「已接入且从未发布过」的失败资产可置：published_at
全空与指针空保证没有权威历史被抹（已发布字节永不删）。可空列、无回填、
无索引（过滤默认恒带本列，量级=单店治理队列）；down 删列即回。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("assets", sa.Column("discarded_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("assets", "discarded_at")
