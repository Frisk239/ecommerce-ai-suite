# 压测报告（第 103 刀）：本机 compose 口径四路径基线 + 限流闸验证 + 埋点对照

> ttft_seconds 等指标自第 47 刀埋点以来从未出过报告（观测债）。本刀用自制
> 轻量压测（`scripts/perf/loadtest.py`，stdlib 线程 + httpx，不引 locust）在本机
> compose 栈上跑出第一份基线数字，并与 /metrics 埋点对照。**服务器未实挂——
> 真环境数字待实挂后补**，本报告全部数字都是本机口径（偏差声明见末节）。

## 环境声明（先读，所有数字的定语）

- **本机 Windows 11 + Docker Desktop**（compose 栈：api 单 uvicorn 进程、PG 5433、
  web），压测客户端与服务端同机——客户端本身消耗 CPU，RPS 是**下界**。
- **非服务器口径**：无独立压测机、无内网链路；HTTP 走 Docker Desktop 的
  Windows→WSL2 网络转发（NAT），health 基线的 p50 里含这一层。
- **LLM 走公网网关**（opencode.ai/zen，qwen3.8-flash）：refusal/rag 路径的每次
  发问含 1–2 次公网 HTTPS 往返，**TTFT 含外网 RTT 与网关排队**；服务器实挂若
  网关 RTT 更小，这两条路径的数字会显著改善。
- 数据形态：演示库（179 商品、614 检索块），会话消息随压测累积（每问落
  customer+agent 两条消息——真实写负载的一部分，如实计入）。

## 方法

- 脚本：`scripts/perf/loadtest.py --path P --users N --duration 60`；引擎路径走
  **控制台预览通道**（operator 登录，每线程一个会话——无顾客三闸干扰，闸语义
  由独立的 ratelimit 场景验证）；延迟=请求发起→SSE 流读完；TTFT=请求发起→
  首个 `delta` 事件到达（rag/refusal 路径）。
- 四条路径的引擎行为（冒烟实测钉死，非推断）：

| path | 问句（固定） | 引擎路径 | LLM 调用 |
| --- | --- | --- | --- |
| health | -（GET /health） | 框架+DB 连通 | 0 |
| tool | 显示器有货吗 | 库存类目聚合快路径（get_stock） | 0（实测 <0.1s） |
| refusal | 这个产品支持意念控制吗 | 提议步→弱命中→生成→模型自述未覆盖→拒收口（kind=refusal） | **2 次**（提议+生成） |
| rag | Xperia Ear Duo 什么时候上市的 | 提议步→检索命中→生成 | 2 次（提议+生成） |

  refusal 并非「零 LLM 拒答」：能稳定落 refusal 结局的问句都过提议步与生成步
  （模型自述证据未覆盖才按拒收口，`fallback_reason=no_coverage`）；词表/快路径
  能截走的问句（订单、库存、目录）都不是无证据句。这是引擎对无证据问句的
  真实成本，如实压。

## 数字（机械粘贴，两档各 60s）

| path | users | requests | RPS | p50 | p95 | p99 | 错误率 | 429 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| health | 10 | 15661 | 261.02 | 0.04s | 0.06s | 0.08s | 0.00% | 0 |
| health | 30 | 15542 | 259.03 | 0.11s | 0.19s | 0.23s | 0.00% | 0 |
| tool | 10 | 4671 | 77.85 | 0.12s | 0.20s | 0.23s | 0.00% | 0 |
| tool | 30 | 4403 | 73.38 | 0.40s | 0.58s | 0.66s | 0.00% | 0 |
| refusal | 10 | 61 | 1.02 | 9.70s | 15.20s | 16.01s | 0.00% | 0 |
| refusal | 30 | 180 | 3.00 | 10.25s | 19.40s | 24.33s | 0.00% | 0 |
| rag | 10 | 79 | 1.32 | 7.54s | 24.21s | 26.82s | 0.00% | 0 |
| rag | 30 | 246 | 4.10 | 6.97s | 12.42s | 25.37s | 0.00% | 0 |

TTFT（首个 delta 到达，客户端口径）：

| path | users | p50 | p95 | p99 |
| --- | --- | --- | --- | --- |
| tool | 10 | 0.10s | 0.17s | 0.19s |
| tool | 30 | 0.35s | 0.45s | 0.46s |
| refusal | 10 | 9.70s | 15.20s | 16.01s |
| refusal | 30 | 10.25s | 19.39s | 24.33s |
| rag | 10 | 7.54s | 24.21s | 26.82s |
| rag | 30 | 6.97s | 12.42s | 25.37s |

（SSE 是「先收全再流」：客户端 TTFT ≈ 完整时长，见埋点对照节的口径差异。）

## 限流闸验证实录（ratelimit 场景）

`--path ratelimit --users 40`：40 线程 barrier 对齐后同时打
`POST /api/customer/sessions`（单 IP=本机）。

| 状态码 | 次数 | Retry-After |
| --- | --- | --- |
| 201 | 5 | - |
| 429 | 35 | 全部 `60` |

**闸真工作**：IP 建会话闸（5/60s）精确触发——恰好 5 个 201 后其余全 429，
Retry-After=60（窗口剩余等待整秒）。压测引擎路径全程（控制台通道）429=0，
顾客闸与引擎压测互不干扰，闸序符合 services/rate_limit 的设计声明。

## 埋点对照（/metrics ttft_seconds vs 压测实测）

rag 两档前后抓 /metrics，取差值段（histogram 累计值做差仍是合法直方图）：

| 口径 | n | p50 | p95 | p99 |
| --- | --- | --- | --- | --- |
| 埋点 `ttft_seconds`（服务端） | 319 | 6.94s | 12.56s | >20s（落 20–∞ 桶） |
| 压测 TTFT（客户端，首个 delta） | 325 | 6.97–7.54s | 12.42–24.21s | 25.37–26.82s |

**口径差异（必读）**：

1. 埋点=**请求进入中间件 → LLM 流首块**（服务端 `observe_first_token`，在
   LLM 首个生成增量处记）；压测=**请求发起 → 首个 delta SSE 事件到达**。
2. 服务端是「先收全再流」：LLM 全文收完落库后才开始吐 SSE——所以客户端
   TTFT ≈ 完整请求时长，**恒大于等于埋点**。差值 = LLM 首块之后的剩余生成 +
   落库 + SSE 传输。p50 差 ~0.1–0.6s，与该解释量级一致。
3. **埋点只记生成路径**：health/tool 两路 0 样本（不进直方图，正确）；
   refusal 虽结局拒答，但拒收口前真的调了生成步，所以**有**埋点样本（起点
   count 248 里 241 来自 refusal 压测）——这不是 bug，是「生成后拒收口」路径
   的真实记录；看板读 ttft_seconds 时要记得分母含这类样本。
4. n 差（325-319=6）：6 次发问被提议步截走（模型提议了工具、代码授权执行），
   未走生成步——埋点不计，客户端按完整 SSE 计。两套口径各自自洽。
5. 埋点 p99 无法从 histogram 读出（桶上限 20s，与 LLM 超时上限对齐是刻意的）；
   客户端口径补上这一格：p99 ≈ 25–27s（网关偶发慢尾）。

## 观察（瓶颈记录，不修——Out 边界）

1. **health 260 RPS 封顶**：10→30 并发 RPS 不升（261→259）、延迟×3——单进程
   uvicorn + 每请求真连 PG（check_database）+ Windows→WSL2 NAT 是天花板；
   并发加不死，延迟线性涨。
2. **tool 74–78 RPS**：快路径本身 <0.1s，RPS 限制在每问 2 条消息的真实写库 +
   消息表随压测增长（recent_turns 查询）+ 30 并发下同步路由线程池排队
   （p50 0.12→0.40s）。
3. **refusal/rag 由公网 LLM 网关支配**：单问 7–16s，p95 双峰（网关偶发 20s+）。
   rag 30u 档 p50 反而低于 10u 档（6.97 vs 7.54）——网关侧波动大于本机并发
   效应，LLM 是绝对瓶颈，本机 CPU/DB 远未到压力线。
4. 全矩阵错误率 0.00%、无 429——单进程容量内稳定性良好。

## 偏差与适用边界（如实声明）

- 本机口径 ≠ 服务器口径：无独立压测机（客户端抢 CPU）、NAT 网络层、单
  uvicorn worker。**服务器实挂后需重跑**：预期 health/tool 路径 RPS 大幅上升
  （去 NAT + 独立客户端），refusal/rag 取决于服务器到 LLM 网关的 RTT。
- LLM 公网网关无 SLA：refusal/rag 数字受当时网关状态支配（同日不同时跑会
  漂移）；TTFT 含外网往返，不代表纯引擎开销。引擎内开销 ≈ 客户端完整时长
  − LLM 网关时长（未单独剥离）。
- refusal/rag 各只跑 60s×2 档（n=61–246）：p95/p99 是网关尾部的粗描，非
  长稳统计。
- 限流闸为**进程内**滑动窗口：多副本部署时闸口径变化（每进程独立计数），
  本验证只在单进程 compose 下成立。

## 复现

```bash
# 本机 compose 栈（db 5433）+ .env 设 METRICS_TOKEN 后 --force-recreate api
uv run python scripts/perf/loadtest.py --path health --users 30 --duration 60
uv run python scripts/perf/loadtest.py --path tool    --users 30 --duration 60
uv run python scripts/perf/loadtest.py --path refusal --users 30 --duration 60
uv run python scripts/perf/loadtest.py --path rag     --users 30 --duration 60
uv run python scripts/perf/loadtest.py --path ratelimit --users 40   # 闸验证（一波）
curl -s -H "Authorization: Bearer $METRICS_TOKEN" localhost:8000/metrics | grep ^ttft_seconds
```
