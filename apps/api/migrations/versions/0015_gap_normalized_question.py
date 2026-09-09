"""第十五批：knowledge_gaps.normalized_question（第 30 刀归一化幂等键）

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-09

缺口幂等键从「原问精确匹配」升为「归一化问」（services/knowledge_gaps.
normalize_question：strip → 全角标点转半角 → 去尾部问句标点）——「…兑换？」
与「…兑换」不再各开一条 open。question 原问列不动（仍存原问做展示）；
本批：加列 normalized_question → 存量 open 行回填归一化值 → 删 0006 的
question 部分唯一索引 → 回填口径下重复的 open 行留 id 最小一条（先例 0006
删重）→ 建 normalized_question 部分唯一索引。resolved 历史行不动（不回填，
保持 NULL；resolved 不参与查重）。SQL 的 translate/rtrim 集合与应用层
normalize_question 同口径（迁移回填测试钉死两者对得上）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 与 services/knowledge_gaps.normalize_question 同口径：
# strip(E' \t\r\n') → 全角标点转半角 → rtrim 尾部问句标点（全/半角都在集合里）
_BACKFILL_SQL = sa.text(
    """
    UPDATE knowledge_gaps
    SET normalized_question = rtrim(
        translate(
            btrim(question, E' \\t\\r\\n'),
            '，。！？；：、（）～',
            ',.!?;:,()~'
        ),
        '？?！!。，、；;.,'
    )
    WHERE status = 'open' AND normalized_question IS NULL
    """
)


def upgrade() -> None:
    op.add_column(
        "knowledge_gaps", sa.Column("normalized_question", sa.Text(), nullable=True)
    )
    # 先回填（resolved 行不回填：不参与查重，历史行不动）
    op.execute(_BACKFILL_SQL)
    # 归一化口径下重复的 open 行：留 id 最小的一条（先例 0006 删重）。必须在
    # 建新唯一索引之前——否则归一化重复行会让唯一索引建不起来。
    op.execute(
        sa.text(
            """
            DELETE FROM knowledge_gaps AS dup
            USING knowledge_gaps AS keep
            WHERE dup.status = 'open'
              AND keep.status = 'open'
              AND dup.normalized_question = keep.normalized_question
              AND dup.id > keep.id
            """
        )
    )
    op.drop_index("uq_knowledge_gaps_open_question", table_name="knowledge_gaps")
    op.create_index(
        "uq_knowledge_gaps_open_normalized",
        "knowledge_gaps",
        ["normalized_question"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )


def downgrade() -> None:
    # 回到 0006 口径：question 精确部分唯一（归一化重复行已在上行删过，
    # 同 question 必同归一化，精确键上无冲突）。normalized_question 列随删。
    op.drop_index("uq_knowledge_gaps_open_normalized", table_name="knowledge_gaps")
    op.create_index(
        "uq_knowledge_gaps_open_question",
        "knowledge_gaps",
        ["question"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )
    op.drop_column("knowledge_gaps", "normalized_question")
