"""集成测试夹具：读 env SUITE_TEST_DATABASE_URL，自建库自清理；未设则 skip。

跑法（README 同步）：docker compose up -d db 后
    SUITE_TEST_DATABASE_URL=postgresql://suite:suite@localhost:5432/suite_test uv run pytest
"""

import os

# 必须先于任何 suite_api import（suite_api.main 模块级 create_app() 即触发
# get_settings() 的 lru_cache 定格）：测试进程强制空 LLM 凭证——即使本机 .env
# 配了真 LLM_API_KEY，厂商生成路径在测试里也自动降级为证据组装模板，不做任何
# 外网调用（spec 第 7 刀：mock 缺省策略=无凭证 env 走降级，既有 SSE 模板断言不破）。
os.environ["LLM_API_KEY"] = ""

from pathlib import Path  # noqa: E402
from urllib.parse import urlsplit, urlunsplit  # noqa: E402

import psycopg  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from suite_api.main import create_app  # noqa: E402
from suite_api.services.rate_limit import SlidingWindowLimiter  # noqa: E402
from suite_api.settings import Settings  # noqa: E402

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
    # llm_api_key 显式空：与模块级 env 清理同口径（app settings 不带真凭证）
    settings = Settings(database_url=url, storage_root=storage_root, llm_api_key="")
    app = create_app(settings)
    # module 级 TestClient 下各用例多次 _login()，生产闸 10/60s 会误伤整套件
    app.state.login_limiter = SlidingWindowLimiter(10_000, 60.0)
    with TestClient(app) as client:  # with 触发 lifespan：alembic upgrade head + 幂等种子
        yield client, storage_root

    _run(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
