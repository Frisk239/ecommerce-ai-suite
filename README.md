# Ecommerce AI Suite

商家侧电商 AI 套件：FastAPI 单体 + Vite React 控制台 + Postgres(pgvector)。
本刀 = **工程脚手架**，无任何业务；业务范围与排期见 `docs/slices.md`，领域决策见 `CONTEXT.md` 与 `docs/adr/`。

## 快速开始（compose 一键起）

```bash
docker compose up --build
```

打开 <http://localhost:5173>：健康卡应显示 API 与数据库双绿（页面真实调用 API 的 `GET /health`）。

| 端口 | 服务 | 说明 |
| --- | --- | --- |
| 5432 | db | `pgvector/pgvector:pg16`（本刀只装不用） |
| 8000 | api | FastAPI 单体，本刀仅 `/health` |
| 5173 | web | Vite + React 19 控制台壳 |

## 本地开发

前置：Python 3.12+（uv 自动管理）、Node 24+、[uv](https://docs.astral.sh/uv/)。

```bash
cp .env.example .env          # 可选：本地覆盖连接串等
uv sync                       # 安装 workspace（apps/api + packages/platform）
```

- **api**（需本地或 compose 的 Postgres）：

  ```bash
  uv run uvicorn suite_api.main:app --reload --app-dir apps/api/src
  ```

  或在 compose 只起 db：`docker compose up db`，api 本地跑。

- **web**：

  ```bash
  cd apps/web
  npm install
  npm run dev                  # /api 代理到 http://localhost:8000
  ```

## 跑测试

```bash
uv run pytest         # 冒烟：API 健康路由 + 平台对象存储
uv run ruff check .   # lint（line-length 100）
```

web 构建校验：`cd apps/web && npm run build && npm run lint`

## 环境变量

见 `.env.example`：`DATABASE_URL`、`STORAGE_ROOT`、`XAI_API_KEY`（本阶段不调模型，仅留位）、`XAI_BASE_URL`。

## 仓库布局

```
apps/api            FastAPI 单体（suite_api，src 布局）
apps/web            Vite + React 19 控制台壳（suite-web）
packages/platform   中台基础库（suite_platform：对象存储端口 + 本地实现）
docs/               领域与 ADR（只读）
prototype/          冻结的产品原型（只读）
```
