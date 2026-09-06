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


def get_engine(request: Request) -> Engine:
    engine = getattr(request.app.state, "engine", None)
    if engine is None:
        engine = create_database_engine(request.app.state.settings.database_url)
        request.app.state.engine = engine
        request.app.state.session_factory = create_session_factory(engine)
    return engine


def get_db(request: Request) -> Generator[Session, None, None]:
    get_engine(request)
    with request.app.state.session_factory() as session:
        yield session


def get_storage(request: Request):
    storage = getattr(request.app.state, "storage", None)
    if storage is None:
        from suite_platform.storage import LocalDirectoryStorage

        storage = LocalDirectoryStorage(request.app.state.settings.storage_root)
        request.app.state.storage = storage
    return storage


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
