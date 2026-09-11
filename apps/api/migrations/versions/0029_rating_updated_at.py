"""第 71 刀：session_ratings.updated_at（评分可改——覆盖式留最新）。

Owner 裁决（2026-09-11）：评分允许修改（顾客常在后续互动后想改分）。语义是
**覆盖式留最新**：一行仍然唯一（uq_session_ratings_session_id 不动），改评
是 UPDATE；``updated_at`` 记最后一次修改（NULL=从未改过，即首评原样）。
"""

import sqlalchemy as sa
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("session_ratings", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("session_ratings", "updated_at")
