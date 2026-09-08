"""GIN 索引迁移钉测（0013，第 24 刀债务池）：up/down/up 幂等跑一遍。

路径与 test_migration_backfill 同模式：独立测试库（后缀 _gin）上 upgrade head
-> pg_indexes 目录断言两列 GIN 索引在位且 ops=jsonb_path_ops（containment
``@>`` 专用）-> downgrade 0012 双双消失 -> 再 upgrade head 回到在位（迁移脚本
可重复执行）。containment 查询的行为面由既有线血缘/考核集成测试兜住（索引只
提速不改语义）；lifespan 随全部集成测试恒跑 0013——本文件专钉索引本体与往返。
需真 Postgres（SUITE_TEST_DATABASE_URL），未设则 skip。
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

# 索引名 -> 表名（血缘 citations 与考核 question_key 两个 containment 消费者）
_GIN_INDEXES = {
    "ix_service_messages_citations_gin": "service_messages",
    "ix_coach_records_question_key_gin": "coach_records",
}


def _alembic_config(database_url: str) -> Config:
    """与 main.py _run_migrations 同法：程序化注入 script_location 与连接串。"""
    cfg = Config(str(_API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_API_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", to_sqlalchemy_url(database_url))
    return cfg


@pytest.fixture()
def gin_db_url() -> Iterator[str]:
    url = os.environ.get(_REQUIRED_ENV)
    if not url:
        pytest.skip(
            f"需真 Postgres：先 `docker compose up -d db`，再设 {_REQUIRED_ENV}"
            "=postgresql://suite:suite@localhost:5432/suite_test"
        )
    parts = urlsplit(url)
    admin_url = urlunsplit((parts.scheme, parts.netloc, "/postgres", "", ""))
    dbname = f"{parts.path.strip('/')}_gin"

    def _run(sql: str) -> None:
        with psycopg.connect(admin_url, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(sql)

    _run(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
    _run(f'CREATE DATABASE "{dbname}"')
    yield urlunsplit((parts.scheme, parts.netloc, f"/{dbname}", "", ""))
    _run(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')


def _indexed_defs(url: str) -> dict[str, str]:
    with psycopg.connect(url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT indexname, indexdef FROM pg_indexes WHERE indexname = ANY(%s)",
            (list(_GIN_INDEXES),),
        )
        return dict(cur.fetchall())


def test_gin_indexes_up_down_up(gin_db_url: str) -> None:
    cfg = _alembic_config(gin_db_url)

    command.upgrade(cfg, "head")
    defs = _indexed_defs(gin_db_url)
    assert set(defs) == set(_GIN_INDEXES)
    for name, defn in defs.items():
        assert f"ON public.{_GIN_INDEXES[name]}" in defn
        assert "USING gin" in defn
        assert "jsonb_path_ops" in defn  # containment @> 专用 ops（比默认 ops 更小更快）

    command.downgrade(cfg, "0012")  # 0013 down：删索引，表与数据不动
    assert _indexed_defs(gin_db_url) == {}

    command.upgrade(cfg, "head")  # 幂等往返：重放 0013 回到在位
    assert set(_indexed_defs(gin_db_url)) == set(_GIN_INDEXES)
