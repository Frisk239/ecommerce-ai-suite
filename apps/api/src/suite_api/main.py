"""应用工厂 + 启动 lifespan（迁移/种子/引擎与存储装配）。

治理发布写回刀：auth / assets / products / audit / health 五组路由。
启动顺序：alembic upgrade head -> 幂等种子 -> 就绪；迁移失败不阻断进程
（/health 仍按数据库连通性如实上报，业务接口自然报错，日志有显式痕迹）。
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from suite_api.db import create_database_engine, create_session_factory, to_sqlalchemy_url
from suite_api.routes import assets, audit, auth, health, products
from suite_api.services.seed import seed_startup_data
from suite_api.settings import Settings, get_settings

logger = logging.getLogger(__name__)

# 开发期控制台（vite dev server）跨源访问本地 API
_DEV_WEB_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]

# apps/api 根（src 布局：suite_api/main.py -> src/suite_api -> src -> apps/api）
_API_ROOT = Path(__file__).resolve().parents[2]


def _run_migrations(database_url: str) -> None:
    cfg = Config(str(_API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_API_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", to_sqlalchemy_url(database_url))
    command.upgrade(cfg, "head")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = app.state.settings
    settings.storage_root.mkdir(parents=True, exist_ok=True)
    from suite_platform.storage import LocalDirectoryStorage

    engine = create_database_engine(settings.database_url)
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)
    app.state.storage = LocalDirectoryStorage(settings.storage_root)
    try:
        _run_migrations(settings.database_url)
        seed_startup_data(engine, settings.operator_password)
    except Exception:  # noqa: BLE001 - 启动期迁移/种子失败不吞日志，但不拦 /health 语义
        logger.exception("启动迁移/种子失败：业务接口将不可用；/health 按数据库连通性如实上报")
    try:
        yield
    finally:
        engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="Ecommerce AI Suite API", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings or get_settings()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_DEV_WEB_ORIGINS,
        allow_credentials=True,  # 会话 cookie 跨源携带
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(assets.router)
    app.include_router(products.router)
    app.include_router(audit.router)
    return app


app = create_app()
