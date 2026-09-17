"""第 94b 刀：service_messages.media_citations（媒体引用，ADR 0052）。

一列落点：nullable JSONB，形状 ``[{asset_id, version_no, mime}]``——citations
（``[{asset_id, version_no}]``）的**姊妹键**：同一份服务端证据派生的媒体附件，
随消息落列，重载会话照样出图/出播放器（与 citations 同寿命，不是 gap_id 那种
运行时键）。

- 仅 agent 消息为列表：无媒体命中恒 ``[]``（形态固定，不是可选键；与 citations
  的「拒答=[]」口径同款）；customer 消息 NULL（ADR 0023「仅 agent」）。
- **存量回填空列表**：历史消息发生在「媒体引用」存在之前，没有可派生的媒体
  附件——回填 ``'[]'`` 与「当时确实无媒体」等价；不回填 NULL 是为了让
  「非空即已派生」与「NULL=非 agent 消息」两个口径在数据面可分（读视图对
  NULL 原样透传）。
- 派生规则在代码（``services/media.media_citations_for``）：mime 从资产种类+
  对象键后缀常量表取；本迁移只加列，不动任何行内容。

downgrade：删列即回滚（无索引、无附加对象要清）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "service_messages",
        sa.Column("media_citations", postgresql.JSONB(), nullable=True),
    )
    op.execute(
        "UPDATE service_messages SET media_citations = '[]'::jsonb"
        " WHERE role = 'agent' AND media_citations IS NULL"
    )


def downgrade() -> None:
    op.drop_column("service_messages", "media_citations")
