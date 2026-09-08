"""第七批：orders 工具数据源表 + 消息 tool 列（ADR 0036）

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-08

orders：订单工具（get_order_status）的只读数据源，不是中台对象（0002 不升
格）——不进检索/治理/MCP，不与 products/assets 建任何外键。单号唯一索引；
items=[{name, qty}]、events=[{at, text}]（按时间序）均 JSONB。

service_messages.tool：工具调用记录 {name, arg, result} JSONB nullable——
回放完整性（重载会话也还原灰底工具条；与 gap_id 的运行时口径不同，工具条
是已发生动作的留档）。kind 新值 "handoff"（工具失败转人工，不连带缺口，
0024）是纯取值扩展，无 schema 改动。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "orders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("order_no", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        # [{name, qty}]：商品名是展示文本快照，不指向 products（无外键语义）
        sa.Column(
            "items",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        # [{at, text}] 物流事件，按时间序
        sa.Column(
            "events",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("placed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_no", name="uq_orders_order_no"),
    )
    op.add_column(
        "service_messages",
        sa.Column("tool", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("service_messages", "tool")
    op.drop_constraint("uq_orders_order_no", "orders", type_="unique")
    op.drop_table("orders")
