# Intake · 第 13 刀订单工具（feat/order-tools）

- 日期：2026-09-08（Slice Owner 接手第 14 刀）
- Prev slug：`order-tools`；实现提交 `4c8fdc0`，评审处置 `3fbd3ad`，文档 `d4f6e07`/`9ac11b1`/`10716a8`，合入 `20b9453`（PR #17）
- Merge 状态：**已合入 default**。第 14 刀从 `origin/main` 起 `feat/stock-tool`。
- 特殊性：同会话 Owner 一手验收（门禁/浏览器六项点穿/两轴评审/合并），无二手声明需复验；下表为当时原始记录。

## Evidence（Owner 一手，junitxml 机械计数）

| 项 | Closeout 声明 | Intake 复核（同会话原始输出） |
|---|---|---|
| 无 DB | `tests=259 passed=193 failed/errored=0 skipped=66` | 同（注释处置后重跑一致） |
| 带库全量（5433） | `tests=259 passed=259 failed/errored=0 skipped=0` | 同（基线 237 → 259 只增） |
| ruff | All checks passed | 同 |
| Owner 浏览器六项 | 双通道命中/查无、刷新还原、零漂移 | 亲历：工具条 `get_order_status(SO-1001) → 已发货 · 2 个物流事件`；SO-9999 转人工徽章+交接摘要+无拒答徽章+无缺口芯片；刷新工具条还原；知识问题仍引用 `A-0009 · v1`；顾客通道同形状 |

## Spec vs claim（抽查 3 项）

1. **非订单零漂移** — PASS。`test_run_ask_commits_before_llm_stream` 零改动绿（不在 diff）；分派纯前置正则；HTTP 复核测试双钉。
2. **不检索不缺口（0024）** — PASS。三哨兵单测（retrieve/gap/LLM 调用即炸）+ 集成 `knowledge_gaps` 计数不变断言。
3. **orders 不升格** — PASS。无路由/MCP/检索/治理触点（评审子代理全扫）。

## Safety

- 无秘密入库；orders 仅引擎工具读。

## 债务（轻，不阻断）

- 后端 ToolCallRecord 类型、三态判别收敛、kind 注释集中、命名后缀、前端双重断言（评审记录在 `docs/progress/order-tools-review.md`）。
- 无单号的订单问题走检索→拒答转人工（语义正确，文案未特化）。

## Verdict

**通过** — 可进 step 3 短对齐第 14 刀（库存工具：ADR 0036 预留同模式复用；词条「库存可 mock」与「_Avoid_ 用规格文档回答有没有货」兑现）。刀计数：第 14 刀；**审计刀 3 于第 15 刀后触发**。
