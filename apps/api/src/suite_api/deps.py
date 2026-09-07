"""请求级依赖：settings / engine / db session / 对象存储 / 当前操作者。

settings 从 app.state 读（create_app 注入），集成测试可用自定义 Settings
构造 app；现有 /health 的 get_settings override 行为不受影响。
"""

from collections.abc import Generator

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from suite_api.db import create_database_engine, create_session_factory
from suite_api.models import Operator
from suite_api.services.sessions import SESSION_COOKIE_NAME, verify_session_value
from suite_api.settings import Settings


def request_settings(request: Request) -> Settings:
    return request.app.state.settings


def ensure_engine(app) -> Engine:
    """engine/session_factory 惰性装配（app.state）：lifespan 已建则复用。

    公开给两类调用方：请求侧 get_db（经 get_engine）与 MCP 连接层工具
    （无 Request 对象，凭 host app 引用走同一装配口径）。
    """
    engine = getattr(app.state, "engine", None)
    if engine is None:
        engine = create_database_engine(app.state.settings.database_url)
        app.state.engine = engine
        app.state.session_factory = create_session_factory(engine)
    return engine


def get_engine(request: Request) -> Engine:
    return ensure_engine(request.app)


def get_db(request: Request) -> Generator[Session, None, None]:
    ensure_engine(request.app)
    with request.app.state.session_factory() as session:
        yield session


def ensure_storage(app):
    storage = getattr(app.state, "storage", None)
    if storage is None:
        from suite_platform.storage import LocalDirectoryStorage

        storage = LocalDirectoryStorage(app.state.settings.storage_root)
        app.state.storage = storage
    return storage


def get_storage(request: Request):
    return ensure_storage(request.app)


def get_current_operator(
    request: Request, db: Session = Depends(get_db)
) -> Operator:
    """签名 cookie 验签 -> 操作者；失败一律 401（0016：控制台必须登录）。"""
    settings = request.app.state.settings
    operator_id = verify_session_value(request.cookies.get(SESSION_COOKIE_NAME), settings.session_secret)
    if operator_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录或会话已过期"
        )
    operator = db.scalar(select(Operator).where(Operator.id == operator_id))
    if operator is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录或会话已过期")
    return operator
