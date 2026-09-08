# Intake · 审计刀 2（feat/audit-2）

- 日期：2026-09-08（Slice Owner 跨刀接手第 11 刀）
- Prev slug：`audit-2`；实现提交 `e3fe9d0`，合入 `82353cc`（PR #14）
- Merge 状态：**已合入 default** — `e3fe9d0` 是 `origin/main` 的祖先。本地 `main` 落后远端；第 11 刀从 `origin/main` 起 `feat/concurrency-hardening`（当前 HEAD = `82353cc`，工作树干净）。

## Evidence（Owner 复核，命令输出机械摘取）

| 项 | Closeout 声明 | Intake 复核 |
|---|---|---|
| 无 DB `uv run pytest` | `147 passed, 52 skipped` | `147 passed, 52 skipped, 2 warnings in 6.37s` + EXIT=0 |
| 带库全量 | `199 passed` | `199 passed, 26 warnings in 20.81s` + EXIT=0（`SUITE_TEST_DATABASE_URL=…/suite_test`） |
| `uv run ruff check apps packages` | All checks passed | `All checks passed!` + EXIT=0 |
| 产品代码 | 审计刀不改产品代码 | `e3fe9d0` 仅四文件：`CONTEXT.md` / `docs/adr/0030-…md` / `docs/progress/audit-2-closeout.md` / `docs/slices.md` |

## Spec vs claim（抽查 3 项）

1. **ADR 0030 实施澄清已回写** — PASS。`docs/adr/0030-knowledge-gap-and-source-kind-tables.md:24`：`gap_id` 按通道分叉（顾客白名单 / 操作者保持），`fallback` 两通道都带。
2. **P0 零 / 无产品代码** — PASS。审计刀提交纯文档；门禁与 closeout 计数一致。
3. **P1 簇七项仍在 HEAD** — PASS（探索子代理核验，行号略有漂移但根因仍在）：LLM 等待持连接（`chat_engine.py` retrieve 后 `await llm.stream_chat` 再 commit）；`get_db` 到响应体完才归还；`import_csv` 为 `async def` 内同步登记；缺口 check-then-insert、迁移 0001–0005 无 open-question 部分唯一；`login` 无限流；limiter 只剪 deque 不删空 key；`retrieve` `limit(10000)` 无 `order_by`。池参数未写死，SQLAlchemy QueuePool 默认 5+10。alembic 头 = `0005`，下一迁 `0006` 正确。

## Safety

- `git ls-files` 无 `.env` / `*.db` / `data/objects`；入库仅 `.env.example`。
- `e3fe9d0` 四份 markdown，无密钥。

## 债务（转交第 11 刀，不判返工）

P1 簇一揽子 = 本刀 Must（审计裁决：优先于回流增强）：

- `run_ask` LLM 等待期不持 DB 事务；ask 路由 SSE 返回前释放 session
- `import_csv` 离开事件循环
- 缺口部分唯一索引 + IntegrityError 兜底
- login IP 限流；limiter 过期空 key 清扫；retrieve 排序定截断

P2 与「仍欠」清单（token TTL、孤儿字节、放弃修订、TTFT、UiMessage 等）**不进本刀**。

文档债（不阻断）：`docs/slices.md` 审计刀 2 节仍写「待 PR」（PR #14 已合）；P2 仍写「0030 未回写」但同提交已补。 nit：closeout「16 并发」相对默认池 5+10=15 差 1，不影响施工。

## Verdict

**通过** — 可进 step 3 短对齐第 11 刀（并发收口，spec 已在 `.scratch/concurrency-hardening/spec.md`）。
