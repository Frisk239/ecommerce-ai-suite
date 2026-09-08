"""第十三批：JSONB containment 查询的 GIN 索引（第 24 刀技术债，audit-4 P1 债池）

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-08

生产规模唯一实质性能面：血缘查询按 containment 下推（lineage.py 的
``citations @> '[{"asset_id": N}]'`` 与 ``question_key @> '{"asset_id": N}'``），
此前两列无索引，大表下每次血缘打开都是全表扫。GIN + jsonb_path_ops 专为
``@>`` 设计（比默认 ops 更小更快，不支持的仅 ?/井号类操作符，本仓只用
containment）。零新表零新列，查询语义不动（只受益索引）；down 删索引即回。
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_service_messages_citations_gin",
        "service_messages",
        ["citations"],
        postgresql_using="gin",
        postgresql_ops={"citations": "jsonb_path_ops"},
    )
    op.create_index(
        "ix_coach_records_question_key_gin",
        "coach_records",
        ["question_key"],
        postgresql_using="gin",
        postgresql_ops={"question_key": "jsonb_path_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_coach_records_question_key_gin", table_name="coach_records")
    op.drop_index("ix_service_messages_citations_gin", table_name="service_messages")
