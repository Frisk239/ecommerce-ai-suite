"""第四批：同一资产最多一个未发布版本（修订流，ADR 0006）

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-08

部分唯一索引 UNIQUE (asset_id) WHERE published_at IS NULL：开修订时
同一资产同时最多一个待人洗版本。不加第四态、不加 in_flight_version_id 列。
audit_log.action 已是 String(20)，回滚用值 "rollback"，无枚举迁移。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_asset_versions_one_unpublished",
        "asset_versions",
        ["asset_id"],
        unique=True,
        postgresql_where=sa.text("published_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_asset_versions_one_unpublished", table_name="asset_versions")
