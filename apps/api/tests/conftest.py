"""集成测试夹具：读 env SUITE_TEST_DATABASE_URL，自建库自清理；未设则 skip。

跑法（README 同步）：docker compose up -d db 后
    SUITE_TEST_DATABASE_URL=postgresql://suite:suite@localhost:5432/suite_test uv run pytest
"""

import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from fastapi.testclient import TestClient

from suite_api.main import create_app
from suite_api.settings import Settings

_REQUIRED_ENV = "SUITE_TEST_DATABASE_URL"


def _split_url(url: str) -> tuple[str, str]:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "/postgres", "", "")), parts.path.strip("/")


@pytest.fixture(scope="module")
def api(tmp_path_factory: Path) -> tuple[TestClient, Path]:
    url = os.environ.get(_REQUIRED_ENV)
    if not url:
        pytest.skip(
            f"需真 Postgres：先 `docker compose up -d db`，再设 {_REQUIRED_ENV}"
            "=postgresql://suite:suite@localhost:5432/suite_test"
        )
    admin_url, dbname = _split_url(url)

    def _run(sql: str) -> None:
        with psycopg.connect(admin_url, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(sql)

    # 自建库：先清场再建，测试结束自清理
    _run(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
    _run(f'CREATE DATABASE "{dbname}"')

    storage_root = tmp_path_factory.mktemp("objects")
    settings = Settings(database_url=url, storage_root=storage_root)
    app = create_app(settings)
    with TestClient(app) as client:  # with 触发 lifespan：alembic upgrade head + 幂等种子
        yield client, storage_root

    _run(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
