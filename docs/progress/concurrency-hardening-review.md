# 工程第 11 刀两轴评审：并发收口（feat/concurrency-hardening）

日期：2026-09-08。Fixed point：`origin/main...HEAD`（评审时 `41429c9`）。Spec：`.scratch/concurrency-hardening/spec.md`。

## Standards

**零硬违规。** 0006 按 0004 风格映射既有 ADR 0024/0030 精确幂等（open 同问唯一，resolved 同文仍可）；CSV `source_kind=upload` 未改；登录闸不复用顾客 XFF；术语保持操作者/顾客/控制台。

Judgement（不改产品代码，记录）：

- `_rate_limited` 与顾客路由同文案（spec 要求同款 429）；沿用路由内助手，不抽共享。
- ask 两通道各写 `commit(); close()`（Must 2 两处都要）。
- `rate_limit.py` 模块原先只写顾客三闸——本刀已改 docstring，点明登录闸共用 `SlidingWindowLimiter`。
- 登录阈值改为 `LOGIN_IP_LIMIT` / `LOGIN_WINDOW_SECONDS`，与顾客常量并列。

## Spec

Must 1–7 与 opportunistic `.dockerignore` / README 反代一句均在 diff。无新 ADR；未扩池；既有路径文案/事件序/状态码未改（login 429 是新路径）。

**(a) 部分（机制锁住、HTTP 并发测未做）：**

- 验收「8 线程同时发问无池尽」：TestClient 串行，未加易碎 HTTP 压测；`test_run_ask_commits_before_llm_stream` 锁 commit 先于 `stream_chat`。
- 验收「导入期间发问不冻」：锁 `import_csv` 为同步 def（FastAPI 线程池）；无重叠 import∥ask HTTP 测。

**(c) 已实修：**

- 0006 建部分唯一前 DELETE 重复 open 同问（留最小 id），避免存量竞态行把 upgrade 卡死。
- 登录闸「只信 TCP、忽略 XFF」补 `test_login_rate_limit_ignores_xff`。

未做：并发缺口测试不强制断言走到 IntegrityError（barrier 仍断言只一条；索引兜底即使串行也成立）。

## 处置后提交

见 `review(11)` commit。
