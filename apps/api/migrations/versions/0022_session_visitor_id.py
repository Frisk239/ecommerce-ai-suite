"""第 45b 刀：service_sessions.visitor_id（嵌入小组件的访客 id）。

`visitor_id`：nullable VARCHAR(64)——宿主页第一方 localStorage 里的 uuid，由加载器
生成并随 iframe 传入，widget 建会话时带 `X-Visitor-Id` 落库。用途是让商家用自己
那边的访客标识对账（独立访问 `/customer` 直开时为 NULL）。

不加索引：v1 只做展示（操作者会话行），没有按访客查询的路径；将来要做「同一访客
的会话」再补索引。

downgrade：删列（访客标识可丢，会话本身不受影响）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("service_sessions", sa.Column("visitor_id", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("service_sessions", "visitor_id")
