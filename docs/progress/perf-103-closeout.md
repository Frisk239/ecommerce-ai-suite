# 第 103 刀 closeout：压测（第四阶段收官刀）

日期：2026-09-17。分支 `feat/perf-103`（stacked 于 `feat/judge-llm-102`）。

## 交付

1. **`scripts/perf/loadtest.py`**：stdlib+httpx 自制并发压测（--users/--duration/--path；四路径：health/tool/refusal/rag；RPS/p50/p95/错误率/429 计数；rag 另采 TTFT）——零新依赖。
2. **本机 compose 口径报告** `docs/research/perf-report.md`（两档×60s，错误率全 0%）：health 261/259 RPS（p95 0.06-0.19s）；tool 77.9/73.4 RPS（p95 0.20-0.58s）；refusal 1.0/3.0 RPS（**9.7-10.3s——LLM 网关绝对支配**）；rag 1.3/4.1 RPS（TTFT p50 ≈7s 含公网往返）。
3. **限流闸精确验证**：40 并发打顾客建会话→**恰好 5×201+35×429+Retry-After 全 60**（5/60s/IP 精确触发）。
4. **埋点对照**：rag 路径 n=319 差值段——埋点 ttft p50 6.94s vs 客户端 ≈7.0s **吻合**（「先收全再流」架构下客户端 TTFT≈完整时长，口径声明）。
5. **两条架构发现**（不修，报告输出）：①「无证据不调模型」实为「**不调生成**」——提议步先于检索，零命中拒答仍付 1 次提议 LLM、弱命中收口付 2 次（**README/goal/引擎 docstring 三处表述随刀精化**——评审 P1 实修）；②health 260 RPS=单进程 uvicorn 封顶（Dockerfile CMD 无 --workers 实证）。

## 证据

- Owner 门禁 **1511/0/0/0**（+16）+ ruff 全过。
- 评审：闸实录与 rate_limit.py 逐项吻合；报告数字自洽；16 纯函数测试承重。

## 评审实修（两轴子代理：无 P0、P1×2）

- 「无证据不调模型」三处精化为「不调生成」（口径级订正——防编造语义不变，调用成本语义诚实化）；
- 埋点对照点 4 归因订正（工具执行后仍进生成步——推测改实证）；roadmap 103 随刀注记。

## 记债

1. 服务器真环境数字待实挂后补（本机口径声明在报告）。
2. `_run_ratelimit` 工作线程非 daemon（单线程先死会挂死——间隙小刀）。
3. 单进程 uvicorn 封顶（部署时 --workers 评估——ops/deploy.md 补一句属部署面）。

## 后续

**第四阶段（88–103）全路线收官。** 剩余待 Owner：23+PR 按序合并（#124–#147 含本刀）；四把云 key（Groq/VLM/IMGGEN/TTS）+自录录像+服务器实挂（真厂商形状/真环境数字补跑）。
