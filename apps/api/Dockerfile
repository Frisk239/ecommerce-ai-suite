# build context = 仓库根（见 compose.yaml），workspace 成员 suite-platform 需要一起 COPY
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# ffmpeg（第 46 刀切片真链路）：第 46 刀起切片拣选用 `ffmpeg -c copy` 真切 mp4；
# slim 基础镜像不带，apt 装最小集（--no-install-recommends 少拖编码器依赖树），
# 装完清 apt lists 控制层体积。CI 的 ubuntu-latest 自带 ffmpeg，无需改 workflow。
# fonts-noto-cjk（第 98b 刀成片）：预览合成的字幕/AIGC 角标走 drawtext，渲染
# 中文需要 CJK 字形（slim 默认无字体，缺了只会渲染豆腐块）。
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

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
# 迁移资产：应用启动 lifespan 程序化跑 alembic upgrade head（路径基于 apps/api）
COPY apps/api/alembic.ini apps/api/
COPY apps/api/migrations apps/api/migrations
RUN uv sync --frozen --no-dev --project apps/api

EXPOSE 8000
CMD ["/app/.venv/bin/uvicorn", "suite_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
