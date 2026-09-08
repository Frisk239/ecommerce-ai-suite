# 工程第 24 刀 closeout：后端债池刀（feat/debt-2）

日期：2026-09-08。上刀 intake：`docs/progress/overview-connect-intake.md`（通过）。短对齐：`.scratch/debt-2/spec.md`（audit-4 P1 后端部分；零新功能面无新 ADR）。基线=`origin/main`（922f97f，PR #29 合并后）。

## 交付（audit-4 P1 后端一揽子）

1. **迁移 0013 GIN 索引**：`service_messages.citations`、`coach_records.question_key` 各 `gin (col jsonb_path_ops)`——血缘两 containment 与考核查询在大表下走索引（生产规模唯一实质性能面）；只经 alembic（down 对称）。
2. **题库单资产推导**：`find_question`/`score_attempt` 按 question_key.asset_id 单资产推导（与列表端点同源 `asset_questions`，prompt 逐字节等价钉测）；列表端点保持全量。
3. **N+1 三连收口**：`load_product_names` 一次 IN——material/clips 列表批取（SQL 计数==1 钉测）。
4. **material 四小条**：retry 直入 running（死转移删）；qc 审核截断后 title（与落库同串）；storage 死参数删净（「生成不碰字节」注记保留 docstring）；reject commit 收服务层（与其余转移一致）。

## Owner 验收

门禁全绿（491/491，迁移 0013 随 lifespan 跑通）；GIN 迁移+coaching 模块抽跑 32/32；行为零变化（存量端点响应形状不变——评审核实）。

## 两轴评审（`docs/progress/debt-2-review.md`）

净可合（评审真 PG 复核）；P2×3 记债（qc 截断边界明示非回归/SQL 计数断言脆/基线口径漂移）。

## 计数（摘自命令输出，junitxml 机械计数）

- `uv run ruff check apps packages`：All checks passed
- 仓库根无 DB：`tests=491 passed=368 failed/errored=0 skipped=123`
- 带 `SUITE_TEST_DATABASE_URL=…localhost:5433/suite_test`：`tests=491 passed=491 failed/errored=0 skipped=0`（基线 485 → 491 只增）

## 遗留

audit-4 债池余项：**前端收口（UiMessage 13 字段×4/ask 双闭包/题源 Link×3/action-error 内联×6/web 零测试基建）=第 25 刀主题**；P2 池不变（token TTL/孤儿字节/放弃修订/检索缓存等）。

## 下一刀

刀计数：第 24 刀（审计刀 4 后第 4 刀）。**第 25 刀=前端收口刀**（audit-4 债池前端部分）；**其后触发审计刀 5**（计数线到）。CONTEXT 推进句已回写。
