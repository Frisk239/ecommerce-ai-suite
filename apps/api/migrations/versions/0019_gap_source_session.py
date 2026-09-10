"""走查修复：knowledge_gaps.session_id（缺口来源会话）。

`knowledge_gaps.session_id`：nullable FK service_sessions.id——首次拒答落这条
缺口时所在的会话，给操作者一条「去读原始对话」的线索（缺口抽屉里显示
「来源会话 #N」并可直接跳到客服页那条会话）。

只在首次插入时写入、归一化命中复用时不覆盖：溯源是可核对的历史，不是
「最近一次被问」。历史行保持 NULL（不可追溯就不编），前端按 NULL 显示
「—」。不加索引：该列只做展示，无查询/排序路径。

downgrade：删列（NULL 行无信息可丢）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "knowledge_gaps",
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("service_sessions.id"), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("knowledge_gaps", "session_id")
