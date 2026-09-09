"""第 39 刀：knowledge_gaps.hit_count（热度）+ assets.last_verified_at（保鲜）。

两列一笔（同刀自进化仪表的最小落点）：

- ``knowledge_gaps.hit_count``：NOT NULL，server_default '1'——新行默认 1
  （首次拒答即 1 次被问），ADD COLUMN 带 server_default 即把存量行全部回填
  为 1（无需独立 UPDATE；规格说明的「存量行 UPDATE 1」由此达成）。应用层
  record_refusal_gap 命中既有 open 缺口时 +1（services/knowledge_gaps），
  列表按 hit_count DESC 排序（热度=被问次数）。
- ``assets.last_verified_at``：nullable——「发布=验证快照」（publish 事务内置
  now）、「重新验证」动作（verify 端点+audit）刷新；NULL=新灌未验证，检索
  侧不降权（spec 内嵌裁决：保守，只有显式验证过后超 STALE_DAYS 天才降权）。

downgrade：两列原样删除（hit_count 无独立索引，无附加对象要清）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "knowledge_gaps",
        sa.Column("hit_count", sa.Integer(), nullable=False, server_default=sa.text("1")),
    )
    op.add_column(
        "assets",
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("assets", "last_verified_at")
    op.drop_column("knowledge_gaps", "hit_count")
