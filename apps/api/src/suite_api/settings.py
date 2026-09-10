"""进程配置：全部来自环境变量（12-factor），默认值只服务本地开发。"""

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # 环境变量优先；本地开发可 cp .env.example .env 覆盖（README 的说明依赖此处）。
    # 注意：不能用 env_ignore_empty——测试进程靠「空串 env 覆盖 .env 真值」
    # 强制空 LLM 凭证（conftest 口径），空串一律忽略会让 .env 密钥漏进测试。
    model_config = SettingsConfigDict(extra="ignore", env_file=".env")

    database_url: str = "postgresql://suite:suite@localhost:5432/suite"
    storage_root: Path = Path("./data/objects")

    # 种子操作者（0016 单店一种操作者）：username 固定 operator
    operator_password: str = "operator123"
    # 会话 cookie 签名密钥：生产必换；默认值仅供本地开发
    session_secret: str = "dev-insecure-session-secret"
    session_ttl_seconds: int = 7 * 24 * 3600
    # 顾客会话令牌 TTL（第 45 刀）：与上面操作者 cookie 的 7 天分开——顾客令牌是
    # 「无需注册的一次性访问凭证」，24 小时够走完一次咨询，同时把泄露面收窄。
    customer_token_ttl_seconds: int = 24 * 3600

    # 可嵌入小组件白名单（第 45b 刀）：逗号分隔的宿主 origin（如
    # "https://shop.example.com,http://localhost:5173"）。**空 = 未启用嵌入**：
    # 带 X-Widget-Origin 的请求一律 403。非白名单同样 403——这是嵌入的唯一闸
    # （routes/customer 在建会话端点校验）。
    widget_allowed_origins: str = ""

    # OpenAI 兼容 Chat Completions（ADR 0028/0033）。密钥只从 .env 读，不入库。
    # 未配 key 时不建客户端、不发请求。接通生成是独立刀。
    llm_api_key: str = ""
    llm_base_url: str = "https://opencode.ai/zen/go/v1"
    llm_model: str = "qwen3.8-flash"

    # 连接层 Bearer（ADR 0032）。空则 MCP 全部 401；与操作者会话无关。
    mcp_bearer_token: str = ""

    # 顾客通道 XFF 信任模式（安全面收口刀）。默认 False=直连：限流 IP 口径只信
    # TCP 对端地址，完全忽略 X-Forwarded-For（自报头换不了 IP 闸 key）；True=
    # 反代模式：信 XFF 第一跳，部署者负责让反向代理强制覆盖该头（README 顾客
    # 通道节）。不配即最保守（fail-closed，与 0033「不做无令牌狂刷」同向）。
    customer_trust_proxy: bool = False

    @field_validator("customer_trust_proxy", mode="before")
    @classmethod
    def _customer_trust_proxy_empty_str(cls, value: object) -> object:
        """compose 以 ``${CUSTOMER_TRUST_PROXY:-}`` 传空串占位：bool 解析空串会
        ValidationError 拒启动，空串语义=回落直连默认（fail-closed）。"""
        return False if value == "" else value


@lru_cache
def get_settings() -> Settings:
    return Settings()
