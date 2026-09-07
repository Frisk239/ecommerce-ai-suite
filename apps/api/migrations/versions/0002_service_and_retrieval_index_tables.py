"""第二批表：ADR 0023 三表 + 索引

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-06

领域映射（ADR 0023 唯一依据，逐列照做）：
- retrieval_chunks：id/asset_id/version_no/seq/chunk；只在发布事务内写入（0004
  只检索已发布；CONTEXT「已发布」=发布时切块入索引），发布事务外无写入路径。
- service_sessions：id/status(active|closed|registered)/created_at/closed_at；
  registered_asset_id 是 ADR「回流登记后状态置 registered 并指向登记出的资产」
  的直接落列（运行态流转列，非新数据语义）。
- service_messages：id/session_id/role(customer|agent)/content/citations JSONB
  （[{asset_id, version_no}]，仅 agent 消息）/kind(answer|refusal)/handoff 布尔
  （0018 拒答与转人工同消息显性；纯 handoff 消息本刀无产生路径，不预埋场景列）/
  created_at。

不预埋顾客通道/LLM/工具调用/多轮记忆列（ADR 0023 明令）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "retrieval_chunks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("asset_id", sa.Integer(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("chunk", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_retrieval_chunks_asset_id", "retrieval_chunks", ["asset_id"])
    op.create_index(
        "ix_retrieval_chunks_asset_id_version_no",
        "retrieval_chunks",
        ["asset_id", "version_no"],
    )
    op.create_table(
        "service_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("registered_asset_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["registered_asset_id"], ["assets.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_service_sessions_status", "service_sessions", ["status"])
    op.create_table(
        "service_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=10), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        # [{asset_id, version_no}]，仅 agent 消息非空（0007 引用带版本）
        sa.Column("citations", postgresql.JSONB(), nullable=True),
        # answer | refusal（customer 消息为 NULL：kind 是回答的属性）
        sa.Column("kind", sa.String(length=10), nullable=True),
        sa.Column("handoff", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["service_sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_service_messages_session_id", "service_messages", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_service_messages_session_id", table_name="service_messages")
    op.drop_table("service_messages")
    op.drop_index("ix_service_sessions_status", table_name="service_sessions")
    op.drop_table("service_sessions")
    op.drop_index(
        "ix_retrieval_chunks_asset_id_version_no", table_name="retrieval_chunks"
    )
    op.drop_index("ix_retrieval_chunks_asset_id", table_name="retrieval_chunks")
    op.drop_table("retrieval_chunks")
