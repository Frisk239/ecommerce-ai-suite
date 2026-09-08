"""存量回填测试（0002 -> 0003 的 source_kind 回填）：conftest 直接 upgrade head，
不覆盖「旧库升级回填」路径，本文件单独钉死。

路径：独立测试库上 upgrade head -> downgrade -1 回到 0002（assets 还没有
source_kind 列的旧结构）-> 裸 SQL 插一行 assets（存量旧数据）-> upgrade head
-> 该行 source_kind == 'upload'（0003：带 server_default 加 NOT NULL 列即回填
旧行，随后摘掉默认逼新行显式给值）。需真 Postgres（SUITE_TEST_DATABASE_URL），
未设则 skip——与 conftest 同模式；自建独立库（后缀区分）自清理，不与 api
fixture 的 module 级库互踩。
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

_LEGACY_ASSET_TITLE = "旧结构存量资产"


def _alembic_config(database_url: str) -> Config:
    """与 main.py _run_migrations 同法：程序化注入 script_location 与连接串。"""
    cfg = Config(str(_API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_API_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", to_sqlalchemy_url(database_url))
    return cfg


@pytest.fixture()
def backfill_db_url() -> Iterator[str]:
    url = os.environ.get(_REQUIRED_ENV)
    if not url:
        pytest.skip(
            f"需真 Postgres：先 `docker compose up -d db`，再设 {_REQUIRED_ENV}"
            "=postgresql://suite:suite@localhost:5432/suite_test"
        )
    parts = urlsplit(url)
    admin_url = urlunsplit((parts.scheme, parts.netloc, "/postgres", "", ""))
    dbname = f"{parts.path.strip('/')}_backfill"

    def _run(sql: str) -> None:
        with psycopg.connect(admin_url, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(sql)

    # 自建独立库：先清场再建，测试结束自清理
    _run(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
    _run(f'CREATE DATABASE "{dbname}"')
    yield urlunsplit((parts.scheme, parts.netloc, f"/{dbname}", "", ""))
    _run(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')


def test_upgrade_0003_backfills_legacy_assets_source_kind(backfill_db_url: str) -> None:
    cfg = _alembic_config(backfill_db_url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0002")  # 回到 0002：assets 尚无 source_kind 列（head 可能已过 0003）

    # 0002 旧结构裸 SQL 插一行存量资产（无 source_kind 可填——列不存在）
    with psycopg.connect(backfill_db_url, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO assets (kind, status, title) VALUES ('document', 'ingested', %s)",
            (_LEGACY_ASSET_TITLE,),
        )

    command.upgrade(cfg, "head")  # 0003：加列并回填存量行

    with psycopg.connect(backfill_db_url, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT source_kind FROM assets WHERE title = %s", (_LEGACY_ASSET_TITLE,))
        row = cur.fetchone()
        assert row is not None
        assert row[0] == "upload"
