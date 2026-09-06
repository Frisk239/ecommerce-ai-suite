"""进程配置：全部来自环境变量（12-factor），默认值只服务本地开发。"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    database_url: str = "postgresql://suite:suite@localhost:5432/suite"
    storage_root: Path = Path("./data/objects")

    # xAI 只留配置占位：本阶段不建模型客户端、不发任何请求（见 docs/slices.md 排期）
    xai_api_key: str = ""
    xai_base_url: str = "https://api.x.ai/v1"


@lru_cache
def get_settings() -> Settings:
    return Settings()
