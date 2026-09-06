# Ecommerce AI Suite

商家侧电商 AI 套件：FastAPI 单体 + Vite React 控制台 + Postgres(pgvector)。
当前刀 = **治理发布写回**（后端）：登录 → 上传规格文档挂商品 → 同步机洗抽字段（抽不到弃权）→ 待人洗确认/补填 → 发布（单事务写回商品 + 审计）。
领域决策见 `CONTEXT.md` 与 `docs/adr/`（表结构唯一依据：ADR 0022）；范围与排期见 `docs/slices.md`。

## 快速开始（compose 一键起）

```bash
docker compose up --build
```

打开 <http://localhost:5173>：健康卡应显示 API 与数据库双绿（页面真实调用 API 的 `GET /health`）。
api 容器启动时自动跑 `alembic upgrade head` + 幂等种子（操作者与两个商品），无需手工迁移。

| 端口 | 服务 | 说明 |
| --- | --- | --- |
| 5432 | db | `pgvector/pgvector:pg16` |
| 8000 | api | FastAPI 单体（治理发布后端 + `/health`） |
| 5173 | web | Vite + React 19 控制台壳 |

## 登录（0016 单操作者）

种子操作者 username `operator`，密码取 env `OPERATOR_PASSWORD`（默认 `operator123`，**仅开发用**）。
`POST /api/auth/login` 成功后签发 httpOnly 签名会话 cookie；所有写接口（登记/确认/发布/机洗重试）未登录返回 401。

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
  启动 lifespan 同样会自动迁移 + 种子（幂等，存在即跳过）。

- **web**：

  ```bash
  cd apps/web
  npm install
  npm run dev                  # /api 代理到 http://localhost:8000
  ```

## 数据库迁移（Alembic）

迁移挂在 `apps/api`（`alembic.ini` + `migrations/`）；连接串读 env `DATABASE_URL`：

```bash
DATABASE_URL=postgresql://suite:suite@localhost:5432/suite uv run alembic -c apps/api/alembic.ini upgrade head
DATABASE_URL=postgresql://suite:suite@localhost:5432/suite uv run alembic -c apps/api/alembic.ini downgrade base   # 可逆性验证
```

应用启动时会程序化执行同一迁移（`command.upgrade(cfg, "head")`），命令行方式供运维/排查使用。

## 跑测试

```bash
uv run pytest         # 单元：/health 冒烟 + 机洗抽取边界 + 发布闸门分类 + 会话签名 + 对象存储
uv run ruff check .   # lint（line-length 100）
```

### 集成测试（需真 Postgres）

先起 db，再设 `SUITE_TEST_DATABASE_URL`（测试自建 `suite_test` 库、结束自清理；未设则 skip）：

```bash
docker compose up -d db
SUITE_TEST_DATABASE_URL=postgresql://suite:suite@localhost:5432/suite_test uv run pytest
```

覆盖全链路：登录 → 上传（真写临时对象存储目录）→ 机洗抽到/弃权 → 未确认发布 422（缺项分「缺少」与「未确认」两类）→ 确认/补填 → 发布 200 → 商品 spec_values 写回带 `asset_id·version` 来源 → 审计留痕 → 未登录 401 → 机洗失败/就地重试 → 对象键形状。

web 构建校验：`cd apps/web && npm run build && npm run lint`

## 环境变量

见 `.env.example`：`DATABASE_URL`、`STORAGE_ROOT`、`OPERATOR_PASSWORD`（种子操作者密码，默认 operator123 仅开发）、`SESSION_SECRET`（会话 cookie 签名密钥，生产必换）、`XAI_API_KEY`（本阶段不调模型，仅留位）、`XAI_BASE_URL`。

## 仓库布局

```
apps/api            FastAPI 单体（suite_api，src 布局 + alembic migrations）
apps/web            Vite + React 19 控制台壳（suite-web）
packages/platform   中台基础库（suite_platform：对象存储端口 + 本地实现）
docs/               领域与 ADR（只读）
prototype/          冻结的产品原型（只读）
```
