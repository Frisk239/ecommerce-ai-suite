"""第 54 刀：service_sessions.host_origin（嵌入宿主的来源站点）。

为什么：第 45b 刀的来源闸校验了 `X-Widget-Origin` 却**不落库**——商家可能把
widget 挂在自己**多个站点**上，只靠 `visitor_id`（宿主自己那边的 uuid）对账，
看不出「这条会话来自哪个站」（审计刀 8/9 记债的最后一条）。

口径：过闸的来源**归一值**（小写、去尾斜杠，与 `_widget_gate` 的比对口径同源）；
**独立访问（/customer 直开）为 NULL**——没有宿主。不做回填（存量会话没有这个事实，
不编造）。

down 删列：宿主站点是运行时事实，降级即丢弃（与 0022 visitor_id 同口径）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("service_sessions", sa.Column("host_origin", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("service_sessions", "host_origin")
