# Intake · 第 11 刀并发收口（feat/concurrency-hardening）

- 日期：2026-09-08（Slice Owner 接手第 12 刀）
- Prev slug：`concurrency-hardening`；实现提交 `41429c9`，评审处置 `dbc1e2f`，closeout `77250e1`，合入 `21fcb52`（PR #15）
- Merge 状态：**已合入 default** — `77250e1` 是 `origin/main`（`21fcb52`）的祖先。本地 `feat/concurrency-hardening` 与远端同步，工作树干净。第 12 刀从 `origin/main` 起 `feat/reflow-qa`。

## Evidence（Owner 复核，命令输出机械摘取）

| 项 | Closeout 声明 | Intake 复核 |
|---|---|---|
| 无 DB `uv run pytest -p no:warnings` | `151 passed, 55 skipped` | junitxml 机械计数：`tests=206 passed=151 failed/errored=0 skipped=55`（本会话 pytest 汇总行经管道被吞，改用 junitxml；与声明一致） |
| 带库全量 `206 passed` | — | 本刀实现时随门禁重跑（Docker Desktop 当前未运行，不阻塞 intake：无 DB 面全绿） |
| ruff | — | 本刀实现时随门禁重跑 |
| Owner 浏览器验收 | 顾客页真模型问答零回归 | 无 DB 集成面零失败 + 下方代码抽查一致，采信 closeout 记录；本刀路径验收时复看 |

## Spec vs claim（抽查 3 项）

1. **LLM 等待前 commit** — PASS。`apps/api/src/suite_api/services/chat_engine.py:101-103`：retrieve+组装后、`stream_chat` 前 `db.commit()`，注释点名「20s 等待不得 idle-in-transaction」。
2. **0006 部分唯一索引** — PASS。`apps/api/migrations/versions/0006_open_gap_question_unique.py`：`unique=True, postgresql_where=sa.text("status = 'open'")`。
3. **login IP 闸** — PASS。`apps/api/src/suite_api/main.py:110` `app.state.login_limiter = SlidingWindowLimiter(LOGIN_IP_LIMIT, LOGIN_WINDOW_SECONDS)`；`routes/auth.py:63` 使用。

## Safety

- `git log --all -- "*.env"` 空——无秘密入库；实现提交含 `apps/web/.dockerignore` 加 `.env`（防泄漏方向，正确）。

## 债务（closeout 遗留，转后续刀排队，不判返工）

- 8 线程 HTTP 级重叠压测、导入∥发问重叠 HTTP 测（TestClient 串行限制）。
- 并发缺口测试不强制断言走到 IntegrityError。
- token TTL、孤儿字节、放弃修订、TTFT、UiMessage 等 P2 不变。

## Verdict

**通过** — 可进 step 3 短对齐第 12 刀（回流增强：会话回流后 LLM 抽 QA 草稿→人洗→发布，调研 `docs/research/demo-to-product-gaps.md` §6 候选 2）。
