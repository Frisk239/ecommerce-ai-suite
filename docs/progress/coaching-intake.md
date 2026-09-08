# Intake · 第 19 刀销售考核（feat/coaching）

- 日期：2026-09-08（Slice Owner 接手第 20 刀）
- Prev slug：`coaching`；实现两笔+评审处置 `122dbf2`+文档三笔，合入 `8120d9e`（PR #24）
- Merge 状态：**已合入 default**。第 20 刀从 `origin/main` 起 `feat/lineage`。
- 特殊性：同会话 Owner 一手验收。

## Evidence（Owner 一手，junitxml 机械计数）

| 项 | Closeout 声明 | Intake 复核（同会话原始输出） |
|---|---|---|
| 无 DB | `tests=428 passed=323 failed/errored=0 skipped=105` | 同 |
| 带库全量（5433） | `tests=428 passed=428 failed/errored=0 skipped=0` | 同（基线 391 → 428 只增） |
| ruff / web build | 通过 | 同 |
| Owner 浏览器点穿 | A-0009 出题→真 LLM 评分→回放 | 亲历：37/40 · 27/30 · 30/30 + 底座 qwen3.8-flash |

## Spec vs claim（抽查 3 项）

1. **题库动态推导** — PASS。confirmed qa_pairs 成题+转写兜底（集成钉）；只认当前指针版。
2. **打分无降级** — PASS。空 key→unscored 可重评（集成钉）；事务纪律实修后双钉测（in_transaction=False）。
3. **考核非中台对象** — PASS。检索/发布/MCP 零触点（评审全扫）。

## Safety

无秘密入库。

## 债务（轻，进审计刀 4 候看）

题库全量重推导 O(N)、SourceChip 复用、三元组类型、兜底题证据维钉测；17/18 刀债务不变。

## Verdict

**通过** — 进第 20 刀血缘视图（0026：资产详情拼引用/写回/考核使用派生视图；下游已齐一次拼全；MCP 导出无留痕表记 debt）。刀计数：第 20 刀；**其后触发审计刀 4**。
