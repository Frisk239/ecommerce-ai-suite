"""第 45 刀：service_sessions.customer_token_expires_at（顾客令牌 TTL）。

`customer_token_expires_at`：nullable timestamptz——顾客令牌的过期时刻，签发时
写 `created_at + customer_token_ttl_seconds`（默认 24h）。**迁移内回填存量行**为
`created_at + interval '24 hours'`：老会话本来永不过期，回填等于按「签发时刻 + 24h」
补上它本该有的过期时间（不编造「永不过期」，也不留 NULL 逃逸口——校验侧把 NULL
视为不可用）。

操作者预览会话 `customer_token` 恒为 NULL，其 `expires_at` 也保持 NULL（该列只对
顾客令牌有意义）。

downgrade：删列（过期信息可丢；应用侧退回「永不过期」的旧语义）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "service_sessions",
        sa.Column("customer_token_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    # 存量回填：只对「有顾客令牌」的会话补过期时间（操作者预览会话保持 NULL）。
    # 口径固定 24h、**不随 env 覆盖**：老行签发时并没有 TTL 概念，这里只是给它补上
    # 「签发时刻 + 当时默认的 24h」这一合理值——之后新签发的行才跟随
    # settings.customer_token_ttl_seconds。两者数值今天相同，若将来调大 TTL，老行
    # 仍是 24h，属预期（回填是一次性的历史修正，不是持续同步）。
    op.execute(
        "UPDATE service_sessions"
        " SET customer_token_expires_at = created_at + interval '24 hours'"
        " WHERE customer_token IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_column("service_sessions", "customer_token_expires_at")
