"""第八批：products.stock 可空 int（ADR 0037）

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-08

stock 是商品上的字段语义（词条原文），mock 值由种子灌（演示店铺语境）：
可空 int，NULL=库存未设置——只被库存工具（get_stock）读，不进检索索引、
不可登记/发布/引用、不进治理台、无 MCP 路径（0002 库存不升格，orders 同
口径）。种子回填（钛钢保温杯 42、瓶装水 0）在 services/seed.py 做：迁移
不写业务数据，存量行留 NULL 即「未设置」，由下一次 seed 幂等回填。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column("stock", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("products", "stock")
