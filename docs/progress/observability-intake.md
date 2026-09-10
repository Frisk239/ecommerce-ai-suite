# 第 47 刀规格：可观测最小版（intake + Owner 裁决）

日期：2026-09-11。分支 `feat/observability-47`（基于 main `c35263b`）。依据：`docs/roadmap-product-hardening.md` 第 47 刀（第二梯队第三项）。

## 痛点

产品自称「可被追溯」，但**运行时完全不可观测**：没有指标端点、没有结构化日志、没有请求关联 id。出了事只有散装的 `logger.warning` 文本行，一行一问、拼不回一条请求链；「拒答率多少 / 首字多慢 / 模板回退占几成」这类问题在演示与面试里只能靠翻库手数。

## 现状（实测）

- 日志 = 9 个模块的 `logging.getLogger(__name__)` 裸调，默认格式（非结构化、无请求关联）。
- 无 `prometheus_client` / `structlog` 依赖，无 `/metrics`。
- 引擎入口 `run_ask` 的产出 `AskOutcome` 已带全部观测所需元数据（`answer.kind`、`generated`、`fallback`、`tool`、`ticket`），只是没人消费。
- 厂商 `stream_chat` 的流式块**默认就带 usage**（本机实测：plain 调用 `completion_tokens/prompt_tokens` 有值），不必加 `stream_options`——省掉「网关不认新参数 → 静默降级」的风险。

## 裁决

| # | 裁决 | 理由 |
| --- | --- | --- |
| 1 | 只加两个依赖：`prometheus-fastapi-instrumentator`（实装 8.1.0）+ `structlog`（实装 26.1.0）。**不引 OTel / 采集栈 / 面板** | roadmap 字面；最小可观测的意义是「有口径、能抓、能读」，不是铺栈 |
| 2 | HTTP 层用 instrumentator 默认 RED 指标；`excluded_handlers=["/metrics", "/health"]`；**`should_exclude_streaming_duration=True`** | 自抓自看会自我放大；SSE 端点整段流时长若进 HTTP 延迟分布，一条长流就把 p95 顶穿、失真 |
| 3 | 自定义恰三个：`chat_requests_total{channel,kind,generated}`、`ttft_seconds`（Histogram）、`llm_tokens_total{direction,model}` | roadmap 点名的三个；`generated` 这个标签顺带把「模板回退率」从记债变成可观测量（审计刀 6 起的观测项） |
| 4 | **标签值必须有限**：channel ∈ {customer, operator}、kind ∈ {answer, refusal, handoff}、generated ∈ {true, false}、direction ∈ {input, output}、model = settings.llm_model（单值）。**不许把 session_id/asset_id/问题文本打进标签** | 基数爆炸是自建指标最常见的自杀方式；按 id 打标等于把 Prometheus 变成数据库 |
| 5 | `ttft_seconds` 口径 = **请求进入中间件 → 厂商首个增量**（含检索/提议/工具步，即顾客真实感知的首字延迟）。非生成路径（拒答/工具/目录回落）不记 | 只量 LLM 段会把检索慢藏起来；口径写进 help 文本，不靠口头约定 |
| 6 | `llm_tokens_total` 从厂商 usage 取（**订正（实现期）**：流式只留最后一块非空值、`finally` 记一次——口径假设「流末给累计总量」，逐块给增量的网关会低估；非流式提议步取 `resp.usage`）。**厂商没给 usage 就不记**，不用字数估算冒充 token | 宁缺毋假：估算值进了指标就再也分不清真假 |
| 7 | `/metrics` 鉴权 = 新设置 `METRICS_TOKEN`（**空 = 一律 401**，fail-closed），请求带 `Authorization: Bearer <token>` 常量时间比对；**不接受 cookie** | Prometheus 不会登录；指标面比治理面宽，单独凭证比复用操作者会话干净。复用 instrumentator 的 `expose(dependencies=[...])`，不手搓端点 |
| 8 | 日志 = structlog JSON（stdlib interop：既有 `getLogger(...).warning(...)` 调用**一行不改**，经 `ProcessorFormatter` 变 JSON），correlation id 由 contextvars 自动进每条日志；`LOG_LEVEL` 可调（默认 INFO）。**留 `trace_id` 字段口子**（本刀只填 correlation_id，不接 OTel） | 既有 9 处调用点不动 = 改动面最小；JSON 是给机器读的格式，字段口子先留好 |
| 9 | correlation id 中间件走**纯 ASGI**（不用 `BaseHTTPMiddleware`）：接受客户端 `X-Request-Id`（长度 8–64、字符集 `[A-Za-z0-9._-]`，不合规丢弃重生成），响应回写同名头 | `BaseHTTPMiddleware` 会把流式响应再包一层 anyio 流，SSE 长连接在它下面有已知破坏面——本仓的顾客主路径就是 SSE |
| 10 | compose 加**可选** `prometheus` 服务（`metrics` profile，默认不启）+ `ops/prometheus.yml`（带 token 抓 `/metrics`） | roadmap 字面「可选 profile，默认关」；有真抓取配置才算「能抓」，但绝不进默认启动路径。**订正（实现期实测）**：Prometheus **不展开**配置文件里的 `${VAR}`（env 里有值、配置文件原样保留 → 抓取 401），故令牌改成挂**文件** `ops/metrics_token`（gitignore，模板 `ops/metrics_token.example`）+ `credentials_file` |
| 11 | 前端**零改动** | 指标是给运维/面试看的，不是控制台功能；控制台已有总览页（43 刀） |

## 验收

1. **端点**：无 token / 错 token → 401；对 token → 200 且文本含 `http_request_duration_seconds`、`chat_requests_total`、`ttft_seconds`、`llm_tokens_total`；默认栈（`METRICS_TOKEN` 空）→ 401（不裸奔）。
2. **RED**：打任意接口 → `http_requests_total` 计数增；`/metrics`、`/health` 自身不出现在指标里。
3. **自定义**：顾客问一句能答的 → `chat_requests_total{channel="customer",kind="answer"}` +1；拒答 → `kind="refusal"`；转人工 → `kind="handoff"`；`generated` 与 `outcome.generated` 一致。
4. **tokens**：配了 `LLM_API_KEY` 时发一问 → `llm_tokens_total{direction="input"}` 与 `{direction="output"}` 均增；未配置时（CI）不增、不报错。
5. **TTFT**：生成路径 → `ttft_seconds` 直方图 `_count` +1；拒答路径不增。
6. **correlation id**：响应带 `X-Request-Id`；传合规 id 原样回显、传超长/带非法字符的 id 被换掉；JSON 日志行含同一 id。
7. **日志**：`LOG_LEVEL=WARNING` 时 INFO 行不出、WARNING 行出且是合法 JSON（含 `event`/`level`/`timestamp`/`correlation_id` 字段）。
8. **门禁**：后端全量绿、ruff 净；前端 build 绿、lint 7/0（未改前端，回归确认）；**新依赖落 `uv.lock`**。
9. **端到端（playwright + curl）**：起 compose → 顾客页真问一句 → `/metrics` 里三个自定义指标都能看到非零值。

## Out

- OTel / trace 传播 / span、Grafana 面板与告警规则、指标持久化与远端写（remote_write）、日志采集（Loki/ELK）、按会话/商品维度打标、前端性能指标（RUM）、`/metrics` 的多租户隔离。
- 本刀不动的：既有 9 处日志调用点的**文案**（只改渲染格式）、引擎的落库与 SSE 事件形状（加指标不许改契约——既有测试是哨兵）。
