# Intake · 第 12 刀回流增强（feat/reflow-qa）

- 日期：2026-09-08（Slice Owner 接手第 13 刀）
- Prev slug：`reflow-qa`；实现提交 `202923e`+`968aecc`，评审处置 `accc643`，文档 `ae61a5f`/`46a295c`/`7ee3ae9`，合入 `9cb4676`（PR #16）
- Merge 状态：**已合入 default**——`7ee3ae9` 是 `origin/main`（`9cb4676`）的祖先。第 13 刀从 `origin/main` 起 `feat/order-tools`。
- 特殊性：本刀 intake 由**同一 Owner 会话一手验收**（实现/门禁/浏览器点穿/评审/合并全程亲跑），无二手声明需要复验；下表为当时的原始记录摘取。

## Evidence（Owner 一手，junitxml 机械计数）

| 项 | Closeout 声明 | Intake 复核（同会话原始输出） |
|---|---|---|
| 无 DB | `tests=237 passed=178 failed/errored=0 skipped=59` | 同（评审处置后 Owner 重跑两次一致） |
| 带库全量（5433） | `tests=237 passed=237 failed/errored=0 skipped=0` | 同（基线 206 → 237 只增不减） |
| ruff | All checks passed | 同 |
| Owner 浏览器点穿 | A-0009 全闭环 | 亲历：真模型问答引用 A-0003·v1 → 回流抽 QA 草稿 → 人洗（改文案+确认，标签转已确认）→ 发布 → 再问命中 QA 块引用 A-0009·v1；视觉抽查（截图 AI 核验）token 一致 |

## Spec vs claim（抽查 3 项）

1. **LLM 失败分级** — PASS。空 key 弃权降级（既有回流测试零改动，237 全绿含旧测试）；坏输出停已接入可重试（集成测试钉死）；`kind=="dialogue"` 显式分派 + 非 dialogue 滤撞名键（评审真 bug 已修，2 个撞名单测）。
2. **只有 confirmed 进索引** — PASS。`qa_pair_chunks` 消费 confirmed；`test_unconfirmed_machine_draft_never_indexed` 钉死；浏览器验收实证发布后才命中。
3. **转写按轮切块/无必填闸门不变** — PASS。`chunk_text` 未动（单测断言前两块不变）；`publishing.py` 零改动。

## Safety

- 无秘密入库；`.env` 仅经 compose 注入；测试进程强制空 key（conftest 既有约定）。

## 债务（closeout 遗留，轻，不阻断）

- `client.ts` requestText 抽公共核心；`_version_or_404` 提取；测试替身共用 helper；PII 打码（全仓现状即无）；聚组/多会话批量抽留后续刀。

## Verdict

**通过** — 可进 step 3 短对齐第 13 刀（订单工具：只读 get_order_status + 转人工触发器落地，词条「库存/订单工具失败时同样发生」的实现缺口）。刀计数：第 13 刀；审计刀 3 于第 15 刀后触发。
