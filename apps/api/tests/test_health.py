"""/health 冒烟：不可达 DB -> 503 固定文案；路由已注册；错误不含连接串。"""

from fastapi.testclient import TestClient

from suite_api.main import create_app
from suite_api.settings import Settings, get_settings

# 端口 9（discard 协议）几乎必无监听：连接被拒，快速失败
_DEAD_DATABASE_URL = "postgresql://suite:secret-not-leaked@127.0.0.1:9/suite"


def _client_with_dead_db() -> TestClient:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(database_url=_DEAD_DATABASE_URL)
    return TestClient(app)


def test_health_route_is_registered() -> None:
    app = create_app()
    # url_path_for 是公开 API；直接遍历 app.routes 依赖路由内部结构（本版 FastAPI
    # 会把 include_router 包成惰性 _IncludedRouter），不可靠
    assert app.url_path_for("health") == "/health"


def test_health_returns_503_when_db_unreachable() -> None:
    client = _client_with_dead_db()
    resp = client.get("/health")

    assert resp.status_code == 503
    body = resp.json()
    assert body == {"status": "unhealthy", "database": "unreachable"}


def test_health_failure_leaks_no_connection_details() -> None:
    client = _client_with_dead_db()
    resp = client.get("/health")

    text = resp.text
    assert "secret-not-leaked" not in text
    assert "127.0.0.1:9" not in text
