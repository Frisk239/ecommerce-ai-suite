# build context = 仓库根（见 compose.yaml），workspace 成员 suite-platform 需要一起 COPY
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN pip install --no-cache-dir uv==0.12.1

WORKDIR /app

# 1) 先只拷 workspace 元数据 + lock：装第三方依赖，吃满缓存层
COPY pyproject.toml uv.lock ./
COPY apps/api/pyproject.toml apps/api/
COPY packages/platform/pyproject.toml packages/platform/
RUN uv sync --frozen --no-dev --no-install-workspace --project apps/api

# 2) 再拷成员源码：editable 安装 suite-platform 与 suite-api
COPY packages/platform/src packages/platform/src
COPY apps/api/src apps/api/src
RUN uv sync --frozen --no-dev --project apps/api

EXPOSE 8000
CMD ["/app/.venv/bin/uvicorn", "suite_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
