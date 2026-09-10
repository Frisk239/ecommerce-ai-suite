# 第 47 刀 closeout：可观测最小版（`feat/observability-47`）

日期：2026-09-11。规格 `docs/progress/observability-intake.md`（Owner 十一条裁决）；依据 `docs/roadmap-product-hardening.md` 第 47 刀（第二梯队第三项）。**无新表无迁移**；新依赖两个（`prometheus-fastapi-instrumentator` / `structlog`）。

## 交付

| 件 | 交付 |
| --- | --- |
| 结构化日志 | `observability.configure_logging`：structlog JSON → stdout，经 `ProcessorFormatter` 接管 stdlib——**既有 12 处 `logging.getLogger(...)` 调用一行未改**即变 JSON 行；`LOG_LEVEL` 调级别（默认 INFO）。uvicorn 自带 handler 清掉并放开传播（否则同进程一半 JSON 一半裸文本） |
| 关联 id | `CorrelationIdMiddleware`：**纯 ASGI**（不用 `BaseHTTPMiddleware`——它给流式响应再包一层 anyio 流，SSE 长连接在它下面是已知破坏面，而顾客主路径就是 SSE）。接受 `X-Request-Id`（8–64 位、`[A-Za-z0-9._-]`，不合规丢弃重生成）→ contextvar → 每条 JSON 日志的 `correlation_id` 字段 + 响应头回显 |
| HTTP RED | instrumentator 默认指标；`excluded_handlers=["/metrics","/health"]`（自抓自我放大 + 探针噪声）；`should_exclude_streaming_duration=True`——SSE 端点只计到响应首字节，否则一条长流把 HTTP 延迟分布顶穿 |
| 三个自定义 | `chat_requests_total{channel,kind,generated}`（`generated=false` 即模板/工具/目录回答，**顺带把「闸回退率」从记债变成可观测值**）、`ttft_seconds{model}`（口径=请求进入中间件 → 厂商首个增量，含检索/提议/工具步；只记生成路径）、`llm_tokens_total{direction,model}`（厂商 usage；缺失即不记，**不用字数估算冒充 token**） |
| 采集点 | 发问计数在**两条 ask 路由**（channel=customer/operator，不在 SSE 生成器里——生成器懒执行，计数不该取决于客户端读没读）；TTFT 在引擎首个生成增量处；token 在 `llm.stream_chat`（流式，`finally` 里记一次，断连也不丢已到的 usage）与 `complete_tool_proposal`（非流式 `resp.usage`） |
| `/metrics` | 新设置 `METRICS_TOKEN`（**空 = 一律 401**，fail-closed），Bearer 常量时间比对，**不接受 cookie**；端点由 instrumentator `.expose(dependencies=[...])` 注册，不自搓第二份实现。settings 从 `app.state` 读（与全仓请求依赖同口径，不是进程级 lru_cache） |
| 可选抓取 | compose `prometheus` 服务挂 `metrics` profile（**默认不启**）+ `ops/prometheus.yml`（15s 抓 `api:8000/metrics`，令牌走 `credentials_file` 挂载 `ops/metrics_token`，已 gitignore） |
| alembic 收口 | `migrations/env.py` 加 `configure_logger` 属性闸：程序化迁移（启动 lifespan）不再让 `fileConfig` 顶掉 JSON 配置、也不 disable 既有 logger；CLI 直跑行为不变 |

## 验收（端到端，真浏览器 + 真容器栈）

1. **闸**（改造后的 api 容器，`METRICS_TOKEN=demo-metrics-token`）：`/metrics` 无 token → **401**、错 token → **401**、对 token → **200**；默认栈（conftest 的 Settings 无 token）→ 401（集成钉测）。
2. **RED**：连打若干接口后 `/metrics` 里 `http_requests_total` 只有 3 条样本，**`/health` 与 `/metrics` 都不在其列**（自抓与探针已排除）。
3. **自定义（真顾客问一句）**：浏览器打开 `/customer` → 开始咨询 → 问「保温杯的净含量是多少」→ 真答「保温杯的净含量为 500ml。[1]」+ 引用 `A-0009 · v1`（真走到厂商）。随后 `/metrics`：
   - `chat_requests_total{channel="customer",generated="true",kind="answer"} 1`
   - `ttft_seconds_count{model="qwen3.8-flash"} 1`、`ttft_seconds_sum … 8.38`（检索 + 厂商首字的真实首字延迟）
   - `llm_tokens_total{direction="input",model="qwen3.8-flash"} 455`、`{direction="output"} 331`（**厂商 usage 真值**，非估算）
4. **关联 id**：`curl -H "X-Request-Id: e2e-trace-00001" /health` → 响应头原样回显；`X-Request-Id: nope` → 换成 32 位 hex。容器日志里**同一条请求链共用一个 id**：`httpx → opencode 网关` 两次、`uvicorn.access` 那行都是 `4dcf51f8…`——一行一问能拼回一条链，这正是本刀要的东西。
5. **启动日志**：`docker compose logs api` 首屏全是 JSON，**含 alembic 的迁移行**（`Context impl PostgresqlImpl.` 等）——证明 `fileConfigure` 顶掉配置的那条路被堵住（它也把 alembic 自己的 INFO 挡住了，现在照常可见）。
6. **可选抓取（真跑通）**：`printf '%s' "$METRICS_TOKEN" > ops/metrics_token` + `--profile metrics up` → Prometheus 目标 `suite-api` **health=up**，`/api/v1/query?query=chat_requests_total` 返回 `{channel="customer",generated="true",kind="answer"} 1`——抓取链路（令牌 → 端点 → 指标）三段都真通。**订正**：Prometheus **不展开**配置文件里的 `${VAR}`（容器 env 有值、配置文件原样保留 → 抓取 401），故令牌从环境变量改成挂文件（README 已改，intake 裁决 10 已订正）。
7. **门禁**：集成 **816 → 840 passed / 0 failed / 0 skipped**（新增 24 例：19 单测 + 5 集成）；ruff 全过；前端未改（build/lint 回归确认 7/0）；新依赖已落 `uv.lock`。

后端钉测：`test_observability.py` 19 例——id 收口六形态（合规/过短/过长/带空格/带换行/空）、中间件回显与生成、请求退出后 contextvar 复位、stdlib 调用变 JSON（字段齐）、级别过滤、usage 形状三态（dict/对象/None/缺一半）、两个计数器的增量、**标签集合断言**（`chat_requests_total` 恰三个标签等，多一个 id 类标签即基数风险）、闸三态（未配置/错 token/非 Bearer 方案）、端点含 RED+三个自定义、`/health` 与 `/metrics` 不入 HTTP 指标。`test_observability_integration.py` 5 例——默认栈关闭、拒答计数 `kind=refusal,generated=false`、**生成路径 TTFT+1 且计数 `generated=true`**、SSE 响应头回显、**拒答不记 TTFT**（不把检索耗时当首字延迟）。

## 评审处置（两轴独立只读子代理：规格符合性 / 工程健壮性）

评审报 **P0 无**、**P1×5**，全部实修并复核；P2 里顺手修 4 条、记债 4 条。

- **P1-1 畸形 usage 会把「降级」变成 500（真 bug）**：`usage_counts` 对 `{"prompt_tokens": "abc"}` / 嵌套对象直接 `int()` → `TypeError/ValueError`，而调用点在 `stream_chat` 的 **`finally`** 里——厂商换了返回形状时会顶掉原本的异常（「超时 → 模板降级」变成 500）。修法：`usage_counts` 捕获 `TypeError/ValueError` 返回 None（形状不认识就不记），补 3 例畸形 parametrize + 1 例「畸形 usage 不得掩盖流中途 LLMUnavailable」。
- **P1-2 token 接线没有测试**：删掉 `stream_chat` 的 `finally` 那行，24 例仍全绿——等于没测。修法：`_FakeStream` 支持挂 `usage`（只挂最后一块），新增 5 例：流式记两向、无 usage 不记、畸形不炸、畸形不掩盖异常、非流式 `complete_tool_proposal` 记 `resp.usage`。**现在删掉任何一处接线都会红。**
- **P1-3 `channel=customer` 与 `kind=handoff` 两条记账路径无测试**：补两例（顾客会话直落库绕过建会话 IP 闸、词表快路径「我要转人工」→ `kind="handoff"`）。
- **P1-4 correlation_id 进日志只有机制没有钉子**：补一例手工驱动纯 ASGI 中间件的用例——请求内打一条 stdlib 日志，断言 JSON 行的 `correlation_id` 与响应头同值。
- **P1-5 抓取令牌文档与事实不符**：源文件不存在时 Docker 会把源路径建成同名**目录**、Prometheus 起得来但 target down（不是 401）。README 与 `ops/prometheus.yml` 注释均改成「先建文件再起」，并补 `ops/metrics_token.example`。

P2 顺手修四条：`LOG_LEVEL` 拼错会让 `root.setLevel` 在启动期抛 `ValueError` → 加 `safe_level()` 白名单回落（补单测）；`test_usage_counts_...` 名不副实（object 分支没测）→ 补 `SimpleNamespace` 用例；RED 断言同义反复（拿被排除的 `/health` 当样本）→ 改打真业务 handler 并断言 `handler="/api/auth/me"` 在牌面上；「`trace_id` 字段口子」的措辞含糊 → 改成「字段位在 `shared_processors`，接 OTel 时在那里加处理器」。

**评审顺带逼出一个真设计缺陷（自查发现，评审也点到）**：instrumentator 在「同名指标已存在」时会捕获 duplicate ValueError 并**静默放弃全部默认 instrumentation**——同进程建第二个 app（集成测试、多实例/多 worker 同进程）就再也记不到 HTTP 指标且不报错。修法：每 app 一份 `CollectorRegistry`（`build_metrics_registry`，三个自定义 Collector 同时挂默认与各 app 的 registry，值共享），并补一例「两个 app 实例都要记 RED」的钉子。这条在单 app 生产栈上不显，但属于「静默降级」那一类，值得在本刀堵掉。

**复核实测**（改完重建容器）：`/metrics` 200；打 `/api/auth/me` → 牌面出现 `handler="/api/auth/me"`；真顾客问一句 → `chat_requests_total{channel="customer",generated="true",kind="answer"} 1`、`ttft_seconds_sum 9.43`、`llm_tokens_total` 输入 455 / 输出 343；响应头 `X-Request-Id: probe-trace-0001` 原样回显。

## 诚实披露

- **TTFT 是「请求进入 → 首个生成增量」**，含检索/提议/工具步，不等于厂商首 token 纯耗时，也不等于用户看到第一个 SSE 事件的时间（`thinking` 事件是立即下发的）。口径写在指标 help 文本里，不靠口头约定。
- **TTFT 只在有请求上下文时记**：直调 `run_ask` 的单元测试没有请求起点，`observe_first_token` 会跳过——测试里看到的 `ttft_seconds_count` 只来自走 HTTP 的集成用例。
- **token 只有厂商给了 usage 才记**：本机实测网关流式与实例化都默认回传 usage（所以演示里两向都有数）；换了不给 usage 的厂商，指标就是空的——这是刻意的（不用字数估算冒充），不是 bug。
- **`/metrics` 是单 token 闸，不做网段/多租户隔离**：默认栈（不设 `METRICS_TOKEN`）直接 401 不裸奔；生产应把端点限制在网内并配长随机 token（README 已写）。
- **日志接管是进程级副作用**：`configure_logging` 会替换 root handler（幂等，不叠加）并清空 uvicorn 三个 logger 的 handler。以库形式嵌进别人的进程里跑会改变宿主日志格式——本仓是单体应用，这是可接受且正是本刀的目的。
- **没接 OTel / trace 传播 / 面板 / 告警 / 远端写**：日志只留了 `correlation_id`，`trace_id` 字段口子留而未填（ADR/roadmap 的 Out 段一致）。
- 端到端证据是**本会话实测输出**（上列数字均为命令回显与 `/metrics` 抓取），未落成截图/脚本文件。

## 后续

- **下一刀=第 48 刀 CSAT + 反馈闭环补全**（会话结束触发 1–5 星、thumbs-up 补收、打回素材补理由、43 刀仪表加 CSAT 卡）。第 48 刀后按节奏是**审计刀 9**（第 50 刀之后）。
- 仍待 Owner 裁决（审计刀 8 记债的四条产品面缺口）：商品价只覆盖 3/115、多来源数据在产品面不可见、工作队列首屏 183 条原始灌入、widget 不落宿主 origin。
