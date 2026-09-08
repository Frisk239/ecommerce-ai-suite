"""第五批：service_sessions.customer_token（ADR 0021/0023 顾客通道）

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-08

领域映射（ADR 0021/0023 已锁语义的直接落列，无新领域决策、不新开 ADR）：
- service_sessions.customer_token：可空 String(64)，非空即顾客会话（不加
  origin 列——token 本身就是判据）；unique 索引既保证令牌不撞车，也支撑按
  令牌直查会话。存原文（spec 工程裁决：token_urlsafe 32 字节熵，单店内部
  系统，DB 泄露不在本刀威胁模型；hash 则无法按令牌直查）。
- 操作者预览会话不写本列（恒 NULL）；旧行全 NULL，Postgres 唯一索引对
  多 NULL 不冲突，回填不需要。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "service_sessions",
        sa.Column("customer_token", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "uq_service_sessions_customer_token", "service_sessions", ["customer_token"], unique=True
    )


def downgrade() -> None:
    op.drop_index("uq_service_sessions_customer_token", table_name="service_sessions")
    op.drop_column("service_sessions", "customer_token")
