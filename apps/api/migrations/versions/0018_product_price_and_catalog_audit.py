"""第 41 刀：products 价格列（price_cents/currency）+ audit_log 产品档。

两组落点（同刀厚切的最小列集）：

- ``products.price_cents``：nullable INTEGER——NULL=未定价（CONTEXT「商品」
  词条：单店单价，不建变体/价目表）。存量行保持 NULL（未定价），演示价由
  seed.py 按类目基准回填（仅 NULL 回填纪律，不覆盖手改价）。
- ``products.currency``：VARCHAR(3) NOT NULL，server_default 'CNY'——加列即
  把存量行全部回填为 CNY（无需独立 UPDATE）；v1 单币种语义，不做结算。
- ``audit_log.product_id``：nullable FK products.id——改价留痕的产品档
  （action='price_change'，asset_id/version_no 为 NULL：改价不是资产发布，
  非资产 publish）。既有资产留痕行 product_id 保持 NULL，查询口径不变
  （按 asset_id 过滤时天然排除改价行）。

downgrade：删三列、asset_id/version_no 恢复 NOT NULL（若已有 NULL 改价行
则恢复失败——属预期：append-only 留痕不建议向下迁移，届时先清改价行）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column("price_cents", sa.Integer(), nullable=True),
    )
    op.add_column(
        "products",
        sa.Column("currency", sa.String(3), nullable=False, server_default=sa.text("'CNY'")),
    )
    op.add_column(
        "audit_log",
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=True),
    )
    op.alter_column("audit_log", "asset_id", existing_type=sa.Integer(), nullable=True)
    op.alter_column("audit_log", "version_no", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    op.alter_column("audit_log", "version_no", existing_type=sa.Integer(), nullable=False)
    op.alter_column("audit_log", "asset_id", existing_type=sa.Integer(), nullable=False)
    op.drop_column("audit_log", "product_id")
    op.drop_column("products", "currency")
    op.drop_column("products", "price_cents")
