"""第六批：open 知识缺口同问部分唯一（ADR 0024/0030/0031 工程收口）

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-08

部分唯一索引 UNIQUE (question) WHERE status = 'open'：并发拒答同问只落
一条 open 缺口。映射既有 ADR 0024/0031/0030 精确幂等，不新开 ADR。
resolved 行同文必须仍允许（故部分唯一，不是全表 unique）——补文档发布后
同问再拒答可另开新缺口。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 并发窗口曾能落重复 open 同问：先留 id 最小的一条，再建部分唯一。
    op.execute(
        sa.text(
            """
            DELETE FROM knowledge_gaps AS dup
            USING knowledge_gaps AS keep
            WHERE dup.status = 'open'
              AND keep.status = 'open'
              AND dup.question = keep.question
              AND dup.id > keep.id
            """
        )
    )
    op.create_index(
        "uq_knowledge_gaps_open_question",
        "knowledge_gaps",
        ["question"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )


def downgrade() -> None:
    op.drop_index("uq_knowledge_gaps_open_question", table_name="knowledge_gaps")
