# 工程第 11 刀 closeout：并发收口（feat/concurrency-hardening）

日期：2026-09-08。上游：审计刀 2 P1 簇（`docs/progress/audit-2-closeout.md`）。短对齐：`.scratch/concurrency-hardening/spec.md`。基线=`origin/main`（PR #14 合并后）。Intake：`docs/progress/audit-2-intake.md`（通过）。

## 交付（P1 七项一揽子，零新功能面）

1. **LLM 等待不占连接**：`run_ask` 在 retrieve+compose 之后、`await stream_chat` 之前 `db.commit()`，20s 等待不再 idle-in-transaction。
2. **SSE 返回前释放 session**：操作者/顾客 ask 路由 `run_ask` 后 `db.commit(); db.close()` 再 `StreamingResponse`。传输层慢客户端属反代责任（README 一句）。
3. **import_csv 离开事件循环**：`async def` → 同步 `def`（FastAPI 线程池）；`file.file.read()`。CSV 语义未改（upload / 逐行 commit）。
4. **缺口幂等收口**：alembic 0006 部分唯一 `uq_knowledge_gaps_open_question`（`question` WHERE `status='open'`）；`record_refusal_gap` 快路径 + SAVEPOINT 兜 IntegrityError（不可 session.rollback，以免丢掉拒答消息）。不新开 ADR。
5. **login IP 闸**：`app.state.login_limiter` 10/60s，成功也计；只信 TCP 对端，不复用顾客 XFF。429 + Retry-After 与顾客同款文案。
6. **limiter 清扫**：每 512 次 `check` 删空 deque 的 key。
7. **retrieve 确定性截断**：`.order_by(RetrievalChunk.id)` 再 `limit(10000)`。
8. **顺手**：`apps/web/.dockerignore` 补 `.env`。

未扩连接池（默认 5+10），持有时长问题不被容量掩盖。

## Owner 验收（全过）

- 重建 api 容器后 alembic `0005 → 0006` 成功；`GET /health` `{"status":"ok","database":"connected"}`。
- 顾客页浏览器：点「开始咨询」→「保温杯的净含量是多少？」→ 真模型「净含量为500ml。」+ 引用 `A-0003 · v1`（零行为回归）。
- 集成：并发双 Session 同问只一条缺口；login 第 3 次 429；换 XFF 仍 429；`import_csv` 非 coroutine；commit 先于 `stream_chat`；retrieve SQL 含 `ORDER BY retrieval_chunks.id`。

## 两轴评审与处置

- Standards：**零硬违规**。实修：限流模块 docstring 点明登录闸共用；登录阈值命名常量。judgement 不抽 `_rate_limited` / SSE 释放助手（路由内既有模式）。
- Spec：Must 1–7 在 diff。实修：0006 建索引前 DELETE 重复 open 同问；登录闸忽略 XFF 单测。未做（记债务）：8 线程 HTTP 压测、导入∥发问重叠 HTTP 测（TestClient 串行；机制分别由 commit-before-LLM 与同步路由锁住）。

## 计数（摘自命令输出）

- 仓库根无 DB：`151 passed, 55 skipped, 2 warnings in 6.58s`
- 带 `SUITE_TEST_DATABASE_URL` 全量：`206 passed, 27 warnings in 22.44s`
- `uv run ruff check apps packages`：All checks passed
- web 无产品 diff（仅 `.dockerignore` 加 `.env`）

## 遗留

- 8 线程同时发问 / 导入期间伴流发问：无 HTTP 级重叠测（Out 的分布式限流、SSE 传输超时仍属部署）。
- 并发缺口测试不强制断言走到 IntegrityError（count==1 仍成立）。
- 生产若在 0006 之前已有重复 open 同问：升级脚本会删较新的重复行（留最小 id）。
- P2 与审计刀 2「仍欠」（token TTL、孤儿字节、放弃修订、TTFT、UiMessage 等）不进本刀。

## 下一刀

回流增强（调研 §6 候选 2：会话回流后 LLM 抽 QA 草稿→人洗→发布）。审计刀 3 约再 5 刀后。
