"""Alembic 环境：目标元数据 = suite_api.models.Base.metadata。

连接串优先级：程序化注入（应用启动 lifespan 的 set_main_option）
> 环境变量 DATABASE_URL > 报错。URL 统一经 to_sqlalchemy_url 归一化
（postgresql:// -> postgresql+psycopg://，驱动是 psycopg3）。
"""

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# src 布局：确保不依赖已安装包也能从仓库源码导入
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from suite_api.db import to_sqlalchemy_url  # noqa: E402
from suite_api.models import Base  # noqa: E402

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _resolve_url() -> str:
    url = config.get_main_option("sqlalchemy.url")
    if not url:
        url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError(
            "缺少数据库连接串：请设 DATABASE_URL 环境变量，或由应用启动时注入 sqlalchemy.url"
        )
    return to_sqlalchemy_url(url)


def run_migrations_offline() -> None:
    context.configure(
        url=_resolve_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = _resolve_url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
