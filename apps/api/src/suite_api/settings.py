"""进程配置：全部来自环境变量（12-factor），默认值只服务本地开发。"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # 环境变量优先；本地开发可 cp .env.example .env 覆盖（README 的说明依赖此处）
    model_config = SettingsConfigDict(extra="ignore", env_file=".env")

    database_url: str = "postgresql://suite:suite@localhost:5432/suite"
    storage_root: Path = Path("./data/objects")

    # 种子操作者（0016 单店一种操作者）：username 固定 operator
    operator_password: str = "operator123"
    # 会话 cookie 签名密钥：生产必换；默认值仅供本地开发
    session_secret: str = "dev-insecure-session-secret"
    session_ttl_seconds: int = 7 * 24 * 3600

    # OpenAI 兼容 Chat Completions（ADR 0028/0033）。密钥只从 .env 读，不入库。
    # 未配 key 时不建客户端、不发请求。接通生成是独立刀。
    llm_api_key: str = ""
    llm_base_url: str = "https://opencode.ai/zen/go/v1"
    llm_model: str = "qwen3.8-flash"


@lru_cache
def get_settings() -> Settings:
    return Settings()
