"""向量列迁移钉测（0034，第 105 刀）：up/down/up 幂等跑一遍 + 列/索引/扩展形状。

路径与 test_gin_index_migration 同模式：独立测试库（后缀 _vec）上 upgrade head
-> 断言 retrieval_chunks.embedding 列在位（udt=vector、nullable）+ HNSW 索引
（cosine ops）+ vector 扩展在装 -> downgrade 0033 三者全消 -> 再 upgrade head
回位（迁移可重复执行）。列真吃 1024 维向量的行为面（写入/回读/vector_dims）由
test_embedding_publish_integration 的真发布路径钉（那里有真资产行喂外键）。
需真 Postgres（SUITE_TEST_DATABASE_URL，compose db 是 pgvector/pgvector:pg16），
未设则 skip。
"""

import os
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from alembic import command
from alembic.config import Config

from suite_api.db import to_sqlalchemy_url

_REQUIRED_ENV = "SUITE_TEST_DATABASE_URL"
# apps/api 根（src 布局：tests/test_x.py -> tests -> apps/api）
_API_ROOT = Path(__file__).resolve().parents[1]

_INDEX_NAME = "ix_retrieval_chunks_embedding_hnsw"


def _alembic_config(database_url: str) -> Config:
    """与 main.py _run_migrations 同法：程序化注入 script_location 与连接串。"""
    cfg = Config(str(_API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_API_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", to_sqlalchemy_url(database_url))
    return cfg


@pytest.fixture()
def vector_db_url() -> Iterator[str]:
    url = os.environ.get(_REQUIRED_ENV)
    if not url:
        pytest.skip(
            f"需真 Postgres：先 `docker compose up -d db`，再设 {_REQUIRED_ENV}"
            "=postgresql://suite:suite@localhost:5432/suite_test"
        )
    parts = urlsplit(url)
    admin_url = urlunsplit((parts.scheme, parts.netloc, "/postgres", "", ""))
    dbname = f"{parts.path.strip('/')}_vec"

    def _run(sql: str) -> None:
        with psycopg.connect(admin_url, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(sql)

    _run(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
    _run(f'CREATE DATABASE "{dbname}"')
    yield urlunsplit((parts.scheme, parts.netloc, f"/{dbname}", "", ""))
    _run(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')


def _column(url: str) -> tuple[str, str] | None:
    """(udt_name, is_nullable)；列不存在返回 None。"""
    with psycopg.connect(url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT udt_name, is_nullable FROM information_schema.columns"
            " WHERE table_name = 'retrieval_chunks' AND column_name = 'embedding'"
        )
        row = cur.fetchone()
        return (row[0], row[1]) if row else None


def _index_def(url: str) -> str | None:
    with psycopg.connect(url) as conn, conn.cursor() as cur:
        cur.execute("SELECT indexdef FROM pg_indexes WHERE indexname = %s", (_INDEX_NAME,))
        row = cur.fetchone()
        return row[0] if row else None


def _extension_installed(url: str) -> bool:
    with psycopg.connect(url) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
        return cur.fetchone() is not None


def test_vector_column_and_hnsw_index_up_down_up(vector_db_url: str) -> None:
    cfg = _alembic_config(vector_db_url)

    command.upgrade(cfg, "head")
    assert _column(vector_db_url) == ("vector", "YES")  # pgvector 类型、nullable
    assert _extension_installed(vector_db_url) is True
    defn = _index_def(vector_db_url)
    assert defn is not None
    assert "ON public.retrieval_chunks" in defn
    assert "USING hnsw" in defn  # HNSW：建索引时机与数据量无关（空表起步即有效）
    assert "embedding vector_cosine_ops" in defn  # bge-m3 余弦相似度口径（<=>）

    command.downgrade(cfg, "0033")  # 0034 down：列/索引/扩展全消
    assert _column(vector_db_url) is None
    assert _index_def(vector_db_url) is None
    assert _extension_installed(vector_db_url) is False

    command.upgrade(cfg, "head")  # 幂等往返：重放 0034 回到在位
    assert _column(vector_db_url) == ("vector", "YES")
    assert _index_def(vector_db_url) is not None
    assert _extension_installed(vector_db_url) is True
