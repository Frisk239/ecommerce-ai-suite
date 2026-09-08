# Closeout · 工程第 5 刀：MCP 只读已发布（feat/mcp-readonly）

- 日期：2026-09-07
- 分支：`feat/mcp-readonly`（`3a013e8` 方向包 → `f87cbde` 实现 → 评审修复）：基于 main `c32c815`（四刀已合并）
- 短对齐：`.scratch/mcp-readonly/spec.md`（本地）

## 交付（外部客户端路径全程）

外部 Agent（官方 MCP Python SDK streamablehttp client，代表 Cursor/Claude）持 Bearer 连 `http://localhost:8000/mcp/` → initialize（protocol 2025-11-25）→ tools/list 恰好四件（无 publish）→ `search_published("保温杯")` 只命中已发布（与客服同一检索函数，0017）→ `get_asset(1)` 当前已发布版含正文与字段；`get_asset(1, version=1)` 历史已发布；待人洗 ID → isError「资产或版本不存在，或从未发布」不泄漏存在性（0020）→ `register_asset(正文)` → 已接入进治理队列（source_kind=mcp_registered 服务端定值，走 registration 同骨架，0013）→ `export_published()` 当前已发布清单**含正文全文**。无/错/空 Bearer → 401 fail-closed。

- **挂载**：官方 SDK（`mcp` 1.29.1，pin `>=1.10,<2`——1.10 起修 CVE-2025-53365）内置 FastMCP 类 → `streamable_http_app()` mount `/mcp`（stateless+json）；session manager 由 host lifespan 代跑（失败只废 /mcp 不拦主服务）。
- **鉴权**：纯 ASGI `BearerGateMiddleware`（mount 不走 FastAPI 依赖）；每请求实读 settings、`secrets.compare_digest`、scheme 大小写不敏感、401 带 `WWW-Authenticate: Bearer`；不读操作者 cookie（结构性隔离）。
- **四工具**全部复用 services 层（retrieve/registration/asset_view 新增 `read_version_text`）；register 的 FastAPI HTTPException 转工具错误文案（HTTP 语义不泄入连接层）。
- `scripts/mcp_smoke.py`（token 只从 env 读）；compose db 宿主端口 `${PG_PORT:-5432}` 可覆盖；README MCP 连接节 + Cursor `mcp.json`。

## 证据（Owner 亲跑，输出原文）

| 项 | 输出 |
| --- | --- |
| `uv run pytest` | `106 passed, 27 skipped, 1 warning in 6.24s` |
| 集成（SUITE_TEST_DATABASE_URL@5433 临时库） | `133 passed, 18 warnings in 13.67s`（125+8） |
| `uv run ruff check .` | `All checks passed!` |
| smoke（真端点，PG_PORT=5434 栈） | `connected … protocol=2025-11-25`；`tools: ['export_published','get_asset','register_asset','search_published']`；四工具全通；`no-bearer:401 / wrong-bearer:401` |
| Owner 亲验 | `get_asset(9)`（待人洗）→ `isError: True` + 统一口径文案；无 Bearer POST initialize → 401 |
| 浏览器治理台 | MCP 登记件（mcp-smoke）来源列=「连接层登记」、待人洗在治理队列 |

## 评审（两轴）与修复

硬违规 0、越刀 0、语义重点全部确证（同一检索函数、指针版校验、export join 指针、fail-closed、cookie 结构性无效）。修复 3 项（评审 judgement）：`mcp` 下界抬 `>=1.10`（CVE-2025-53365 修复线，lock 本就 1.29.1）；401 补 `WWW-Authenticate` 头 + scheme 大小写不敏感；register 的 HTTPException 转工具文案。

## Deviations

1. ADR 0032「不用 FastMCP」字面 vs 官方 SDK 内置类名就叫 FastMCP——实现用官方 `mcp.server.fastmcp.FastMCP`（docstring 已辨析），两轴评审认可意图满足；ADR 可在下一次方向更新里把措辞改成「不用第三方 fastmcp 库」。
2. 测试经 httpx ASGITransport 直打 ASGI（不开端口）；真网络端点由 Owner smoke 覆盖。
3. SDK 2.x 已出（改名 MCPServer/httpx2）——锁 1.x 线，升级 2.x 属后续独立事项。
4. 主库混有历次验收演示数据（含旧卷第 4 刀数据与 mcp-smoke 件）；演示前可 `docker compose down -v && rm -rf data/objects/*` 重铺。

## 债务

1. stateless 无服务端推送（本刀无需求）；未来通知类能力需回 stateful。
2. SDK 默认 DNS-rebinding 防护仅放行 localhost/127.0.0.1；真实域名部署需配 `transport_security`。
3. `read_version_text` 无大小上限（导出全文属 ADR 要求，资源面留意）。
4. smoke 与 test 的 payload 提取小重复；`main.py` 一个不可达防御分支；`asset_view` 测试妥协参数——微债。
5. MCP register 无治理台侧 2MB/content-type 前置校验（spec 未要求，同骨架成立；外部 Agent 误传大文本由机洗失败兜住）。

## 下一 Owner 注意

- 更后面（独立刀，slices.md 表）：修订流+回滚（0031 缺口默认开修订）、**厂商生成**（0033：LLM_API_KEY/BASE_URL/MODEL 已在 .env 就位，Chat API 接入仍受 0018/0007 约束）、顾客对话通道（0021/0033）、素材/切片/考核。
- 环境注意：suanming 项目常占 5432——起本仓栈用 `PG_PORT=5434 docker compose up -d`；集成测试用 5433 临时 pg 容器；gh 需 HTTPS_PROXY=7890。
