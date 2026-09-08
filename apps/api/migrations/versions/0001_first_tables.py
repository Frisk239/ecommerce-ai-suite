"""第一批表：ADR 0022 五表 + 三索引

Revision ID: 0001
Revises:
Create Date: 2026-09-06

领域映射（不改不增不删）：
- operators：0016 单操作者，无角色列
- products：spec_schema/spec_values JSONB（0019/0010）
- assets：kind/status/current_published_version_id（0002 三态、0006 指针）；
  运行列 product_id/title/last_error（挂商品、登记标题、机洗失败原因）
- asset_versions：不可变快照 + 对象键 + extracted/confirmed（0006/0003/0009）
- audit_log：append-only 留痕（0005/0016）

不预埋修订/回滚列（ADR 0022 明令）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSONB_EMPTY = sa.text("'{}'::jsonb")


def upgrade() -> None:
    op.create_table(
        "operators",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=100), nullable=False),
        sa.Column("password_hash", sa.String(length=200), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username"),
    )
    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("category", sa.String(length=50), nullable=False),
        sa.Column("spec_schema", postgresql.JSONB(), server_default=_JSONB_EMPTY, nullable=False),
        sa.Column("spec_values", postgresql.JSONB(), server_default=_JSONB_EMPTY, nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    # assets 先建（不带指向 asset_versions 的循环外键），asset_versions 建完再 ALTER 补
    op.create_table(
        "assets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=True),
        sa.Column("product_id", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("current_published_version_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "asset_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("asset_id", sa.Integer(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("object_key", sa.String(length=500), nullable=False),
        sa.Column(
            "extracted_fields", postgresql.JSONB(), server_default=_JSONB_EMPTY, nullable=False
        ),
        sa.Column(
            "confirmed_fields", postgresql.JSONB(), server_default=_JSONB_EMPTY, nullable=False
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("asset_id", "version_no", name="uq_asset_versions_asset_version"),
    )
    op.create_foreign_key(
        "fk_assets_current_published_version",
        "assets",
        "asset_versions",
        ["current_published_version_id"],
        ["id"],
    )
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("operator_id", sa.Integer(), nullable=False),
        sa.Column("asset_id", sa.Integer(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["operator_id"], ["operators.id"]),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_assets_status", "assets", ["status"])
    op.create_index("ix_asset_versions_asset_id", "asset_versions", ["asset_id"])
    op.create_index("ix_audit_log_asset_id", "audit_log", ["asset_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_log_asset_id", table_name="audit_log")
    op.drop_index("ix_asset_versions_asset_id", table_name="asset_versions")
    op.drop_index("ix_assets_status", table_name="assets")
    op.drop_table("audit_log")
    op.drop_constraint("fk_assets_current_published_version", "assets", type_="foreignkey")
    op.drop_table("asset_versions")
    op.drop_table("assets")
    op.drop_table("products")
    op.drop_table("operators")
