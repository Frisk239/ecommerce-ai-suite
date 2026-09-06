"""数据库层：裸 psycopg 连通检查（/health 语义）+ SQLAlchemy engine 管理。

/health 不依赖 ORM/迁移：仍走 check_database（原语义保留）。
业务引擎统一走 to_sqlalchemy_url 归一化的 psycopg3 URL。
"""

import psycopg
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

_DRIVER_PREFIX = "postgresql+psycopg://"
_BARE_PREFIX = "postgresql://"


def check_database(database_url: str, *, timeout_seconds: float = 3.0) -> bool:
    """执行 SELECT 1 验证连通性。

    任何失败都只返回 False：异常文本（含连接串/密码）一律不向上传播，
    防止经 /health 响应泄露。
    """
    try:
        with psycopg.connect(database_url, connect_timeout=max(1, int(timeout_seconds))) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
    except Exception:  # noqa: BLE001 - 健康检查必须吞掉一切连接错误细节
        return False
    return True


def to_sqlalchemy_url(database_url: str) -> str:
    """postgresql:// -> postgresql+psycopg://（驱动固定 psycopg3，ADR 0022 工程选型）。"""
    if database_url.startswith(_DRIVER_PREFIX):
        return database_url
    if database_url.startswith(_BARE_PREFIX):
        return _DRIVER_PREFIX + database_url[len(_BARE_PREFIX) :]
    return database_url


def create_database_engine(database_url: str) -> Engine:
    return create_engine(to_sqlalchemy_url(database_url), pool_pre_ping=True)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)
