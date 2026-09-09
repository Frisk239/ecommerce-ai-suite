# Intake · 第 28 刀生命周期出口刀（feat/lifecycle-exits）

- 日期：2026-09-09（Slice Owner 接手第 29 刀）
- Prev slug：`lifecycle-exits`；实现两笔+文档三笔，合入 `c20b480`（PR #35）
- Merge 状态：**已合入 default**。第 29 刀从 `origin/main` 起 `feat/multi-turn`。
- 特殊性：同会话 Owner 一手验收。

## Evidence（Owner 一手）

无 DB `tests=533 passed=394 failed/errored=0 skipped=139`；带 DB `533/533/0/0`（基线 510→533）；浏览器+API 三出口实证（v4 换新正文机洗重算、放弃 v4 audit 行+行删、A-0018 废弃列表消失+A-0013 409 兜底）。

## Spec vs claim（抽查）

三闸门与 ADR 0042 一致（评审核）✓；storage.delete 首次接线✓；confirmed 保留口径✓。

## Safety

无秘密入库。

## 债务

P2×2（delete-commit 窗口/计数口径）；优化计划后续刀不变。

## Verdict

**通过** — 进第 29 刀客服多轮记忆刀（goal ①「必须有：多轮」；0021 引擎层扩展，无新 ADR——语义进 spec：最近 N 轮进 prompt、拒答/工具轮不进记忆、检索语义不变）。刀计数：第 29 刀。
