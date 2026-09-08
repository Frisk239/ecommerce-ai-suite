"""应用工厂 + 启动 lifespan（迁移/种子/引擎与存储装配）。

治理发布写回刀：auth / assets / products / audit / health 五组路由。
启动顺序：alembic upgrade head -> 幂等种子 -> 就绪；迁移失败不阻断进程
（/health 仍按数据库连通性如实上报，业务接口自然报错，日志有显式痕迹）。
"""

import logging
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from suite_api import mcp_server
from suite_api.db import create_database_engine, create_session_factory, to_sqlalchemy_url
from suite_api.routes import (
    assets,
    audit,
    auth,
    customer,
    health,
    knowledge_gaps,
    products,
    service,
)
from suite_api.services.rate_limit import CustomerRateLimits, SlidingWindowLimiter
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
    # MCP 会话管理器（ADR 0032）：mounted 子应用的 lifespan 不会执行，host 代跑；
    # 失败只废 /mcp，不拖累 /api/* 与 /health
    async with AsyncExitStack() as stack:
        mcp_manager = getattr(app.state, "mcp_session_manager", None)
        if mcp_manager is not None:
            try:
                await stack.enter_async_context(mcp_manager.run())
            except Exception:  # noqa: BLE001 - 同上：显式留痕，不拦主服务
                logger.exception("MCP 会话管理器启动失败：/mcp 不可用，其余接口不受影响")
        try:
            yield
        finally:
            await stack.aclose()
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
    app.include_router(service.router)
    app.include_router(knowledge_gaps.router)
    # 顾客通道（ADR 0021/0033）：无操作者鉴权，Bearer 令牌 + 两级限流在路由内；
    # 限流器挂 app.state（测试可替换为小阈值/假时钟实例）
    app.include_router(customer.router)
    app.state.customer_rate_limits = CustomerRateLimits()
    # 登录 IP 闸（10/60s，成功也计；测试可换成大阈值/假时钟）。只信 TCP 对端。
    app.state.login_limiter = SlidingWindowLimiter(10, 60)

    # MCP 连接层（ADR 0032）：官方 SDK Streamable HTTP 挂 /mcp 前缀，对外端点
    # /mcp/；Bearer 闸门在子应用层，/api/* 与 /health 不经过它。session
    # manager 由 lifespan 代跑（见 lifespan 内 AsyncExitStack）。
    app.mount("/mcp", mcp_server.build_mcp_app(app))
    return app


app = create_app()
