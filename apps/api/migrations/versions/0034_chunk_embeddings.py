"""第 105 刀（向量基础设施 A1）：retrieval_chunks.embedding 列 + HNSW 向量索引。

只备料不动检索：本刀给切块存语义坐标（embedding），检索打分路径（retrieve 的
bigram 词法）一行不改——融合检索是 106 刀的事，本列当前无任何读消费者。

- ``embedding vector(1024)`` nullable：bge-m3 的维度。无 embedding 的块 NULL
  （未配 EMBED_API_KEY 的环境 / 云调用失败 / 回填未跑过），NULL 不影响词法检索。
- 索引选 **HNSW + vector_cosine_ops**（pgvector 0.5+；compose db 镜像
  pgvector/pgvector:pg16 实测 0.8.5）：HNSW 建索引时机与数据量无关，空表/
  单店千行级起步即有效；ivfflat 需要「先有数据再建」且 lists 依赖行数预估，
  对本仓「迁移先行、数据后灌」的形态是劣选。ops 用 cosine（bge-m3 官方相似
  度口径，106 刀融合按 <=> 取近邻）。
- pgvector 类型非 SQLAlchemy 内建：列与索引一律 ``op.execute`` 裸 SQL（0025 迁移
  ``conn.execute(sa.text(...))`` 的同族先例）；ORM（models.RetrievalChunk）**不**
  映射该列——写读走裸 SQL（发布补写/回填脚本），106 刀接检索时再议映射。
- ``CREATE EXTENSION IF NOT EXISTS vector``：compose db 是 pgvector 镜像（扩展
  可装未装，实测 extversion 空）；suite 用户是容器超级用户，可装。

down：删索引、删列、卸扩展（本仓只有此列用 pgvector，卸干净不留「幽灵扩展」）。
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX_NAME = "ix_retrieval_chunks_embedding_hnsw"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("ALTER TABLE retrieval_chunks ADD COLUMN embedding vector(1024)")
    op.execute(
        f"CREATE INDEX {_INDEX_NAME} ON retrieval_chunks USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX {_INDEX_NAME}")
    op.execute("ALTER TABLE retrieval_chunks DROP COLUMN embedding")
    op.execute("DROP EXTENSION IF EXISTS vector")
