"""GET /health：API 自身 + 数据库连通性的唯一探针。"""

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from suite_api.db import check_database
from suite_api.settings import Settings, get_settings

router = APIRouter(tags=["health"])


class HealthBody(BaseModel):
    status: str  # "ok" | "unhealthy"
    database: str  # "connected" | "unreachable"


@router.get("/health", response_model=HealthBody)
def health(response: Response, settings: Settings = Depends(get_settings)) -> HealthBody:
    if check_database(settings.database_url):
        return HealthBody(status="ok", database="connected")
    # 固定文案，不携带任何驱动错误细节（连接串/密码不出现在响应里）
    response.status_code = 503
    return HealthBody(status="unhealthy", database="unreachable")
