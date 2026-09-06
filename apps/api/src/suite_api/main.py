"""应用工厂。本刀只挂 health 路由；业务刀从这里逐个挂模块路由。"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from suite_api.routes import health

# 开发期控制台（vite dev server）跨源访问本地 API
_DEV_WEB_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]


def create_app() -> FastAPI:
    app = FastAPI(title="Ecommerce AI Suite API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_DEV_WEB_ORIGINS,
        allow_methods=["GET"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    return app


app = create_app()
