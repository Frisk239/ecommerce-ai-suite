# Ecommerce AI Suite

商家侧电商 AI 套件：FastAPI 单体 + Vite React 控制台 + Postgres(pgvector)。
当前刀 = **客服引用**（后端）：发布事务切块入检索索引（ADR 0004/0023）→ 客服会话 SSE 流式回答带 `{asset_id, version_no}` 引用（0007/0018：无证据拒答+转人工）→ 会话回流登记为 kind=dialogue 资产（0013）→ 治理发布后再次命中引用（闭环）。
领域决策见 `CONTEXT.md` 与 `docs/adr/`（表结构唯一依据：ADR 0022/0023）；范围与排期见 `docs/slices.md`。

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
`POST /api/auth/login` 成功后签发 httpOnly 签名会话 cookie；所有业务接口（登记/确认/发布/机洗重试/客服会话与回流登记）未登录返回 401。

## 客服引用 API（第 3 刀，ADR 0023）

```bash
POST   /api/service/sessions                       # 新会话（active）
GET    /api/service/sessions                       # 列表（倒序：状态+首问摘要+消息数）
GET    /api/service/sessions/{id}                  # 详情（消息全量，含 citations/kind）
POST   /api/service/sessions/{id}/messages         # 发问 {"content": "..."} -> SSE 流
POST   /api/service/sessions/{id}/register         # 回流登记 -> kind=dialogue 资产（待人洗）
```

SSE 事件协议（`text/event-stream`，回答文本在流式开始前已完整收全落库，断连不产生 interrupted 消息，停止语义由前端表达）：

```
event: thinking    data: {"text": "正在检索已发布资产…"}
event: thinking    data: {"text": "正在生成回答…"}     # 仅模型路径；拒答/降级不出现
event: delta       data: {"text": "..."}            # 多片，~12 字/片
event: complete    data: {"message_id": 1, "citations": [{"asset_id": 3, "version_no": 1}],
                          "kind": "answer", "handoff": false, "gap_id": null, "fallback": false}
```

无命中 -> `kind: "refusal"`、`handoff: true`、`citations: []`，固定文案「抱歉，已发布资产里没有能回答这个问题的证据。」（0018：不编造不闲聊）。检索只查当前已发布版本（0004/0017），切块在发布事务内写入 `retrieval_chunks`（0002 第二批迁移）。

拒答（0024 知识缺口）同事务落 `knowledge_gaps`（同问法精确幂等不新建），`complete.gap_id` 即缺口 id（answer 恒为 null）；`GET /api/knowledge-gaps?status=open|resolved` 看待办（全登录），「补文档」=`POST /api/assets/register` 带可选表单字段 `knowledgeGapId`（来源 `source_kind` 由端点定值：上传=upload、回流=session_backflow），发布事务内缺口自动 resolved 并指向该资产。

## 厂商生成（第 7 刀，ADR 0033）

检索命中已发布证据时，回答由厂商大模型流式生成（OpenAI 兼容 Chat Completions，`openai` 官方包配 `base_url`）；证据块（命中切块含确认字段值，各带「来源：A-{id}·v{N}」标注）与顾客问题进 prompt。三个环境变量只写本机 `.env`（密钥不入库、不进日志/响应）：

| 变量 | 说明 |
| --- | --- |
| `LLM_API_KEY` | 厂商密钥；**为空时不建客户端、不发请求**，自动降级为证据组装模板回答 |
| `LLM_BASE_URL` | OpenAI 兼容端点（默认 `https://opencode.ai/zen/go/v1`） |
| `LLM_MODEL` | 模型名（默认 `qwen3.8-flash`） |

- **无证据不调模型**（0018）：拒答+转人工路径原样，防编造也省调用。
- **降级**：LLM 未配置/超时（20s，重试 0 次）/网络失败/空产出 -> 复用 `answer.py` 证据组装模板回答，走同一 delta 流，`complete` 事件带 `fallback: true`，前端显示「模板回退」徽章（诚实标注，不装作模型回答）；错误细节只进服务端日志且不含密钥。
- **引用服务端定**（0007）：`citations` 恒由检索命中确定，模型无引用决定权；系统提示明确要求模型不输出引用编号。
- 回答先收全再落库再流式：断连仍完整落库（契约不变）。
- **回流机洗抽 QA**（第 12 刀，ADR 0035）：配置 `LLM_API_KEY` 后，回流登记（`POST /api/service/sessions/{id}/register`）会在请求内同步等待一次 QA 抽取生成（≤20s）；LLM 失败则资产停已接入、就地重试端点可重跑，未配置则 `qa_pairs` 弃权照常待人洗。

## 顾客通道（第 8 刀，ADR 0021/0033）

`POST /api/customer/sessions` 无登录签发会话令牌（Bearer）；`POST /api/customer/sessions/{id}/messages` 发问走与控制台预览同一客服引擎。三道限流闸（进程内滑动窗口，429+Retry-After）：IP 建会话 5/60s、IP 发问 30/60s（先于令牌鉴权——狂刷不碰库）、会话发问 10/60s（后于令牌鉴权——无效令牌耗不了真会话的配额）。会话不存在与令牌无效统一 401 同文案（自增 session id 不可探测）。传输层慢客户端防护属部署侧反代责任（应用只在返回 SSE 前释放数据库会话）。

限流 IP 口径两种部署模式（env `CUSTOMER_TRUST_PROXY`）：

| 模式 | 取值 | 语义 |
| --- | --- | --- |
| 直连（默认） | 空 / `false` | 只信 TCP 对端地址，**完全忽略 `X-Forwarded-For`**：api 直接暴露（含 compose 现状）时，伪造该请求头换不了 IP 闸 key——不配即最保守（fail-closed） |
| 反代 | `true` | 信 `X-Forwarded-For` 第一跳：api 只接反向代理流量时用，**部署者必须让反代强制覆盖/清洗该头**，否则顾客自报头即可绕过 IP 闸 |

## MCP 连接层（第 5 刀，ADR 0032）

外部 Agent（Cursor / Claude / 官方 SDK 客户端）经 Streamable HTTP 连同一份中台，端点 `http://localhost:8000/mcp/`。

- **鉴权**：`Authorization: Bearer <MCP_BEARER_TOKEN>`，与操作者登录会话完全隔离（不读 cookie）。token 未配置或为空时所有 MCP 调用一律 401；本地开发默认值见 `.env.example`（`dev-mcp-bearer`，生产必换）。
- **四工具**（没有 publish——发布只属于治理台操作者）：

  | 工具 | 语义 |
  | --- | --- |
  | `search_published(query)` | 检索当前已发布切块（与客服同一索引），返回 `{asset_id, version_no, title, chunk, score}` |
  | `get_asset(asset_id, version?)` | 取已发布资产正文；不传 version=当前指针版，传 version=历史已发布版；待人洗/已接入一律拒绝 |
  | `register_asset(content, title?, product_id?)` | 登记文档（必须带正文），来源固定 `mcp_registered`，落为已接入等治理台处理 |
  | `export_published()` | 全部当前已发布资产，含该版正文全文 |

- **冒烟**（需先有已发布资产）：

  ```bash
  docker compose up -d --build api
  MCP_BEARER_TOKEN=dev-mcp-bearer uv run python scripts/mcp_smoke.py
  # 可选参数：search 关键词、asset_id、version
  MCP_BEARER_TOKEN=dev-mcp-bearer uv run python scripts/mcp_smoke.py 保温杯 1 1
  ```

- **Cursor mcp.json**：

  ```json
  {"mcpServers":{"ecommerce-suite":{"url":"http://localhost:8000/mcp/","headers":{"Authorization":"Bearer <token>"}}}}
  ```

- compose 端口：本机 5432 被其他项目占用时，`.env` 设 `PG_PORT`（如 `PG_PORT=5433`）后 `docker compose up -d db`，`DATABASE_URL` / `SUITE_TEST_DATABASE_URL` 同步指向该端口。

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
客服刀闭环：发布（切块入索引）→ 问「净含量」SSE 引用 v1 → 问待人洗独有内容 refusal+handoff → 回流登记 dialogue 资产待人洗 → 发布 → 再问命中引用该对话 → 版本跟随指针（指针前移后命中 v2 块）。

web 构建校验：`cd apps/web && npm run build && npm run lint`

## 环境变量

见 `.env.example`：`DATABASE_URL`、`STORAGE_ROOT`、`OPERATOR_PASSWORD`（种子操作者密码，默认 operator123 仅开发）、`SESSION_SECRET`（会话 cookie 签名密钥，生产必换）、`LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL`（OpenAI 兼容 Chat Completions，只写本机 `.env`，禁止入库）、`MCP_BEARER_TOKEN`（连接层独立凭证，空则 MCP 全部 401，不复用登录 cookie）、`CUSTOMER_TRUST_PROXY`（顾客通道 XFF 信任模式，空=直连忽略 XFF，反代部署设 true，语义见「顾客通道」节）。真实 LLM 密钥只落到 `.env`。

## 仓库布局

```
apps/api            FastAPI 单体（suite_api，src 布局 + alembic migrations）
apps/web            Vite + React 19 控制台壳（suite-web）
packages/platform   中台基础库（suite_platform：对象存储端口 + 本地实现）
docs/               领域与 ADR（只读）
prototype/          冻结的产品原型（只读）
```
